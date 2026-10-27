#!/usr/bin/env python3
"""Multi-symbol replay of compute_intraday_signal (ORB+VWAP) on 15m RTH bars."""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
sys.path.insert(0, str(DASH))

from engine import (  # noqa: E402
    UA,
    _parse_chart,
    atr14,
    compute_intraday_signal,
    fetch_chart,
    regular_session,
)

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = DASH / "cache"

DEFAULT_SYMBOLS = ["SNDK", "NVDA", "MU", "SKHY", "WDC", "AMD", "AVGO", "TSM"]


@dataclass
class Trade:
    day: str
    side: str
    entry_t: str
    entry: float
    S: float
    T1: float
    exit_t: str
    exit: float
    reason: str
    R1: Optional[float]
    pnl_pct: float
    eq5x: float
    setup: str


def load_15m(symbol: str) -> List[dict]:
    path = CACHE / f"{symbol}_15m.json"
    if path.exists():
        try:
            rows, _ = _parse_chart(json.loads(path.read_text()))
            if rows:
                return rows
        except Exception:
            pass
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range=15d&interval=15m&includePrePost=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())
    rows, _ = _parse_chart(raw)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))
    except Exception:
        pass
    return rows


def atr_asof(daily: List[dict], d: date) -> float:
    hist = [x for x in daily if x["dt_ny"].date() <= d]
    return float(atr14(hist) or 0) or 1.0


def replay_symbol(symbol: str, last_n_days: int = 12) -> Dict[str, Any]:
    try:
        rows = load_15m(symbol)
    except Exception as e:
        return {"symbol": symbol, "error": f"15m load failed: {e}", "n_trades": 0}

    sess = regular_session(rows)
    by: Dict[date, List[dict]] = defaultdict(list)
    for b in sess:
        by[b["dt_ny"].date()].append(b)
    days = sorted(by.keys())
    if not days:
        return {"symbol": symbol, "error": "no RTH bars", "n_trades": 0}

    try:
        daily, _, _ = fetch_chart(symbol, "3mo", "1d", prefer_cache=True)
    except Exception:
        daily = []

    focus = days[-last_n_days:]
    trades: List[Trade] = []
    day_logs = []
    # 可跨日：不强制收盘平仓；止损用入场日结构 S（不因次日 ORB 收窄）
    open_trade: Optional[dict] = None
    entered_day: Optional[date] = None

    for d in focus:
        day = by[d]
        if len(day) < 4:
            continue
        atr = atr_asof(daily, d) if daily else max(
            (max(x["h"] for x in day) - min(x["l"] for x in day)) * 1.2, day[-1]["c"] * 0.02
        )
        orb_h = max(x["h"] for x in day[:2])
        orb_l = min(x["l"] for x in day[:2])
        day_o = day[0]["o"]
        signals_seq = []
        path_l = day[0]["l"]
        path_h = day[0]["h"]

        for i in range(2, len(day)):
            bars = day[: i + 1]
            num = den = 0.0
            for b in bars:
                tp = (b["h"] + b["l"] + b["c"]) / 3
                if b["v"] > 0:
                    num += tp * b["v"]
                    den += b["v"]
            vwap = num / den if den else bars[-1]["c"]
            px = float(bars[-1]["c"])
            path_l = min(path_l, min(x["l"] for x in bars), px)
            path_h = max(path_h, max(x["h"] for x in bars), px)

            sig = compute_intraday_signal(
                px=px,
                atr=float(atr),
                orb_l=float(orb_l),
                orb_h=float(orb_h),
                vwap=float(vwap),
                day_o=float(day_o),
                path_l=float(path_l),
                path_h=float(path_h),
                bars=bars,
            )
            t = bars[-1]["dt_bj"].strftime("%m-%d %H:%M")
            signals_seq.append(sig["signal"])

            if open_trade:
                side = open_trade["side"]
                S, T1, E = open_trade["S"], open_trade["T1"], open_trade["E"]
                hi, lo = bars[-1]["h"], bars[-1]["l"]
                exit_px = exit_why = None
                # 跨日只认入场日 S/T1；当日可用 ORB 失效平仓。不因反向开仓信号反手。
                same_day = open_trade.get("day") == str(d)
                if side == "long":
                    if lo <= S:
                        exit_px, exit_why = S, "触S"
                    elif hi >= T1:
                        exit_px, exit_why = T1, "触T1"
                    elif same_day and sig["signal"] == "平多":
                        exit_px, exit_why = px, "平多"
                else:
                    if hi >= S:
                        exit_px, exit_why = S, "触S"
                    elif lo <= T1:
                        exit_px, exit_why = T1, "触T1"
                    elif same_day and sig["signal"] == "平空":
                        exit_px, exit_why = px, "平空"
                if exit_px is not None:
                    pnl = ((exit_px - E) / E * 100) if side == "long" else ((E - exit_px) / E * 100)
                    trades.append(
                        Trade(
                            day=open_trade.get("day") or str(d),
                            side=side,
                            entry_t=open_trade["t"],
                            entry=E,
                            S=S,
                            T1=T1,
                            exit_t=t,
                            exit=float(exit_px),
                            reason=str(exit_why),
                            R1=open_trade.get("R1"),
                            pnl_pct=round(pnl, 2),
                            eq5x=round(pnl * 5, 2),
                            setup=open_trade.get("setup") or "",
                        )
                    )
                    open_trade = None

            if (
                open_trade is None
                and entered_day != d
                and sig.get("entry_ready")
                and sig["signal"] in ("做多", "做空")
            ):
                E = (
                    px
                    if sig["entry_low"] <= px <= sig["entry_high"]
                    else (sig["entry_low"] + sig["entry_high"]) / 2
                )
                open_trade = {
                    "side": "long" if sig["signal"] == "做多" else "short",
                    "E": E,
                    "S": sig["S"],
                    "T1": sig["T1"],
                    "t": t,
                    "day": str(d),
                    "R1": sig.get("R1"),
                    "setup": sig.get("setup"),
                }
                entered_day = d

        # 收盘：浮盈则兑现（提高胜率）；浮亏则跨日拿到结构 S，避免贴脸打损后踏空
        if open_trade is not None and open_trade.get("day") == str(d):
            px = day[-1]["c"]
            t = day[-1]["dt_bj"].strftime("%m-%d %H:%M")
            E, side = open_trade["E"], open_trade["side"]
            unreal = ((px - E) / E * 100) if side == "long" else ((E - px) / E * 100)
            if unreal > 0:
                trades.append(
                    Trade(
                        day=str(d),
                        side=side,
                        entry_t=open_trade["t"],
                        entry=E,
                        S=open_trade["S"],
                        T1=open_trade["T1"],
                        exit_t=t,
                        exit=px,
                        reason="收盘止盈",
                        R1=open_trade.get("R1"),
                        pnl_pct=round(unreal, 2),
                        eq5x=round(unreal * 5, 2),
                        setup=open_trade.get("setup") or "",
                    )
                )
                open_trade = None

        day_logs.append(
            {
                "day": str(d),
                "orb": f"{orb_l:.2f}-{orb_h:.2f}",
                "counts": dict(Counter(signals_seq)),
            }
        )

    if open_trade is not None:
        last_d = focus[-1]
        last_bars = by[last_d]
        px = last_bars[-1]["c"]
        t = last_bars[-1]["dt_bj"].strftime("%m-%d %H:%M")
        E, side = open_trade["E"], open_trade["side"]
        pnl = ((px - E) / E * 100) if side == "long" else ((E - px) / E * 100)
        trades.append(
            Trade(
                day=open_trade.get("day") or str(last_d),
                side=side,
                entry_t=open_trade["t"],
                entry=E,
                S=open_trade["S"],
                T1=open_trade["T1"],
                exit_t=t,
                exit=px,
                reason="样本末日",
                R1=open_trade.get("R1"),
                pnl_pct=round(pnl, 2),
                eq5x=round(pnl * 5, 2),
                setup=open_trade.get("setup") or "",
            )
        )

    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    return {
        "symbol": symbol,
        "focus_days": [str(d) for d in focus],
        "n_days": len(day_logs),
        "n_trades": n,
        "win_rate": round(wins / n * 100, 1) if n else None,
        "avg_pnl_pct": round(sum(t.pnl_pct for t in trades) / n, 3) if n else None,
        "sum_eq5x": round(sum(t.eq5x for t in trades), 2) if n else None,
        "exits": dict(Counter(t.reason for t in trades)),
        "setups": dict(Counter(t.setup for t in trades)),
        "day_logs": day_logs,
        "trades": [asdict(t) for t in trades],
        "notes": "可跨日持仓；每日最多一笔；止损为入场日 ORB 对侧结构位",
    }


def main(argv: List[str]) -> None:
    symbols = [s.strip().upper() for s in (argv[1:] or DEFAULT_SYMBOLS) if s.strip()]
    summary = []
    print(f"回放符号: {', '.join(symbols)}")
    for sym in symbols:
        print(f"\n=== {sym} ===")
        r = replay_symbol(sym)
        if r.get("error"):
            print("失败:", r["error"])
            summary.append({"symbol": sym, "error": r["error"]})
            continue
        print(
            f"日数={r['n_days']} 笔数={r['n_trades']} 胜率={r['win_rate']}% "
            f"均笔={r['avg_pnl_pct']}% 5x加总={r['sum_eq5x']}% 出场={r['exits']}"
        )
        for t in r["trades"][-5:]:
            print(
                f"  {t['day']} {t['side']:5} {t['setup'][:10]:10} "
                f"pnl={t['pnl_pct']:+.2f}% → {t['reason']}"
            )
        out = OUT / f"{sym.lower()}_orb_vwap_replay.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2))
        print("wrote", out)
        summary.append(
            {
                "symbol": sym,
                "n_trades": r["n_trades"],
                "win_rate": r["win_rate"],
                "avg_pnl_pct": r["avg_pnl_pct"],
                "sum_eq5x": r["sum_eq5x"],
                "exits": r["exits"],
            }
        )

    summ_path = OUT / "multi_orb_vwap_summary.json"
    summ_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n==== 汇总 ====")
    for s in summary:
        if s.get("error"):
            print(f"{s['symbol']}: ERROR {s['error']}")
        else:
            print(
                f"{s['symbol']:6} n={s['n_trades']:3} win={s['win_rate']}% "
                f"avg={s['avg_pnl_pct']:+.2f}% eq5x={s['sum_eq5x']:+.1f}%"
            )
    print("wrote", summ_path)


if __name__ == "__main__":
    main(sys.argv)
