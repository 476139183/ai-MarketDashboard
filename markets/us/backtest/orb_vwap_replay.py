#!/usr/bin/env python3
"""Multi-symbol / multi-interval replay of compute_intraday_signal on RTH bars.

按 1m / 5m / 15m 分开统计；尽量拉长 Yahoo 可用样本（1m≈7日，5m/15m≈60日）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
sys.path.insert(0, str(DASH))

from engine import (  # noqa: E402
    UA,
    _parse_chart,
    atr14,
    compute_intraday_signal,
    fetch_chart,
    normalize_interval,
    orb_bar_count,
    regular_session,
)

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = DASH / "cache"

DEFAULT_SYMBOLS = ["SNDK", "NVDA", "MU"]
DEFAULT_INTERVALS = ["1m", "5m", "15m"]

# Yahoo 各周期大致上限
YAHOO_RANGE = {"1m": "7d", "5m": "60d", "15m": "60d"}
DEFAULT_DAYS = {"1m": 7, "5m": 40, "15m": 40}


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
    setup: str
    interval: str


def _save_raw(symbol: str, interval: str, raw: dict) -> None:
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        text = json.dumps(raw)
        (CACHE / f"{symbol}_{interval}_bt.json").write_text(text, encoding="utf-8")
        (CACHE / f"{symbol}_{interval}.json").write_text(text, encoding="utf-8")
    except Exception:
        pass


def _yahoo_chart(symbol: str, range_: str, interval: str, retries: int = 4) -> Tuple[List[dict], dict]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval={interval}&includePrePost=false"
    )
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        for host in ("query1", "query2"):
            u = url.replace("query1", host)
            try:
                req = urllib.request.Request(
                    u,
                    headers={"User-Agent": UA, "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = json.loads(resp.read().decode())
                rows, meta = _parse_chart(raw)
                if rows:
                    _save_raw(symbol, interval, raw)
                    return rows, meta
            except Exception as e:
                last_err = e
                time.sleep(1.2 * (attempt + 1))
        # urllib 易 429：退回 curl（常仍可用）
        try:
            import subprocess

            u = url.replace("query1", "query2")
            proc = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "-A",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    u,
                ],
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                raw = json.loads(proc.stdout)
                rows, meta = _parse_chart(raw)
                if rows:
                    _save_raw(symbol, interval, raw)
                    return rows, meta
        except Exception as e:
            last_err = e
        time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"{symbol} {interval} Yahoo 失败: {last_err}")


def load_bars(symbol: str, interval: str, force_live: bool = False) -> List[dict]:
    """优先用本地长样本缓存；不足再拉 Yahoo。"""
    iv = normalize_interval(interval)
    range_ = YAHOO_RANGE.get(iv, "60d")
    candidates = [
        CACHE / f"{symbol}_{iv}_bt.json",
        CACHE / f"{symbol}_{iv}.json",
    ]

    best: Optional[List[dict]] = None
    best_days = 0
    if not force_live:
        for path in candidates:
            if not path.exists():
                continue
            try:
                rows, _ = _parse_chart(json.loads(path.read_text()))
                if not rows:
                    continue
                sess = regular_session(rows)
                nd = len({b["dt_ny"].date() for b in sess})
                if nd > best_days:
                    best, best_days = rows, nd
            except Exception:
                continue
        # 有可用缓存就先用（15m 本地常有数月）
        if best is not None and best_days >= 3:
            return best

    rows, _ = _yahoo_chart(symbol, range_, iv)
    return rows


def atr_asof(daily: List[dict], d: date) -> float:
    hist = [x for x in daily if x["dt_ny"].date() <= d]
    return float(atr14(hist) or 0) or 1.0


def replay_symbol(
    symbol: str,
    interval: str = "15m",
    last_n_days: Optional[int] = None,
    force_live: bool = False,
) -> Dict[str, Any]:
    iv = normalize_interval(interval)
    orb_n = orb_bar_count(iv)
    min_bars = orb_n + 1
    days_cap = last_n_days if last_n_days is not None else DEFAULT_DAYS.get(iv, 40)

    try:
        rows = load_bars(symbol, iv, force_live=force_live)
    except Exception as e:
        return {
            "symbol": symbol,
            "interval": iv,
            "error": f"{iv} load failed: {e}",
            "n_trades": 0,
        }

    sess = regular_session(rows)
    by: Dict[date, List[dict]] = defaultdict(list)
    for b in sess:
        by[b["dt_ny"].date()].append(b)
    days = sorted(by.keys())
    if not days:
        return {"symbol": symbol, "interval": iv, "error": "no RTH bars", "n_trades": 0}

    try:
        daily, _, _ = fetch_chart(symbol, "6mo", "1d", prefer_cache=True)
    except Exception:
        try:
            daily, _, _ = fetch_chart(symbol, "3mo", "1d", prefer_cache=False)
        except Exception:
            daily = []

    focus = days[-days_cap:]
    trades: List[Trade] = []
    day_logs = []
    open_trade: Optional[dict] = None
    entered_day: Optional[date] = None

    for d in focus:
        day = by[d]
        if len(day) < min_bars:
            continue
        atr = atr_asof(daily, d) if daily else max(
            (max(x["h"] for x in day) - min(x["l"] for x in day)) * 1.2,
            day[-1]["c"] * 0.02,
        )
        orb_h = max(x["h"] for x in day[:orb_n])
        orb_l = min(x["l"] for x in day[:orb_n])
        day_o = day[0]["o"]
        signals_seq = []
        path_l = day[0]["l"]
        path_h = day[0]["h"]

        for i in range(orb_n, len(day)):
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
                            setup=open_trade.get("setup") or "",
                            interval=iv,
                        )
                    )
                    open_trade = None
                    if exit_why == "触T1":
                        entered_day = None

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

        if open_trade is not None and open_trade.get("day") == str(d):
            px = day[-1]["c"]
            t = day[-1]["dt_bj"].strftime("%m-%d %H:%M")
            E, side = open_trade["E"], open_trade["side"]
            unreal = ((px - E) / E * 100) if side == "long" else ((E - px) / E * 100)
            if unreal >= 1.5:
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
                        reason="收盘止盈≥1.5%",
                        R1=open_trade.get("R1"),
                        pnl_pct=round(unreal, 2),
                        setup=open_trade.get("setup") or "",
                        interval=iv,
                    )
                )
                open_trade = None

        day_logs.append(
            {
                "day": str(d),
                "orb": f"{orb_l:.2f}-{orb_h:.2f}",
                "n_bars": len(day),
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
                setup=open_trade.get("setup") or "",
                interval=iv,
            )
        )

    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    return {
        "symbol": symbol,
        "interval": iv,
        "orb_bars": orb_n,
        "yahoo_range": YAHOO_RANGE.get(iv),
        "focus_days": [str(d) for d in focus],
        "day_span": f"{focus[0]} → {focus[-1]}" if focus else None,
        "n_days": len(day_logs),
        "n_trades": n,
        "win_rate": round(wins / n * 100, 1) if n else None,
        "avg_pnl_pct": round(sum(t.pnl_pct for t in trades) / n, 3) if n else None,
        "sum_pnl_pct": round(sum(t.pnl_pct for t in trades), 2) if n else None,
        "exits": dict(Counter(t.reason for t in trades)),
        "setups": dict(Counter(t.setup for t in trades)),
        "day_logs": day_logs,
        "trades": [asdict(t) for t in trades],
        "notes": f"{iv}·ORB={orb_n}根(≈30m)；可跨日；打损当日不再开；收盘浮盈≥1.5%可兑现",
    }


def _brief(r: Dict[str, Any]) -> Dict[str, Any]:
    if r.get("error"):
        return {"symbol": r["symbol"], "interval": r.get("interval"), "error": r["error"]}
    return {
        "symbol": r["symbol"],
        "interval": r["interval"],
        "n_days": r["n_days"],
        "day_span": r.get("day_span"),
        "n_trades": r["n_trades"],
        "win_rate": r["win_rate"],
        "avg_pnl_pct": r["avg_pnl_pct"],
        "sum_pnl_pct": r.get("sum_pnl_pct"),
        "exits": r["exits"],
        "setups": r.get("setups"),
    }


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(description="ORB+短线趋势回放（按周期分开）")
    p.add_argument("symbols", nargs="*", help="标的，默认 SNDK NVDA MU")
    p.add_argument(
        "--intervals",
        default="1m,5m,15m",
        help="逗号分隔周期，默认 1m,5m,15m",
    )
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="各周期最多用最近 N 个交易日（默认 1m=7, 5m/15m=40）",
    )
    p.add_argument("--live", action="store_true", help="强制重新拉 Yahoo，忽略缓存")
    args = p.parse_args(argv)

    symbols = [s.strip().upper() for s in (args.symbols or DEFAULT_SYMBOLS) if s.strip()]
    intervals = [normalize_interval(x) for x in args.intervals.split(",") if x.strip()]
    intervals = [x for x in intervals if x in ("1m", "5m", "15m")] or list(DEFAULT_INTERVALS)

    print(f"回放符号: {', '.join(symbols)}")
    print(f"周期: {', '.join(intervals)} | days={args.days or '按周期默认'} | live={args.live}")

    all_rows: List[Dict[str, Any]] = []
    by_iv: Dict[str, List[Dict[str, Any]]] = {iv: [] for iv in intervals}

    for iv in intervals:
        print(f"\n######## 周期 {iv} ########")
        for sym in symbols:
            print(f"\n=== {sym} · {iv} ===")
            r = replay_symbol(sym, interval=iv, last_n_days=args.days, force_live=args.live)
            brief = _brief(r)
            all_rows.append(brief)
            by_iv[iv].append(brief)
            if r.get("error"):
                print("失败:", r["error"])
                continue
            print(
                f"区间={r.get('day_span')} 日数={r['n_days']} 笔数={r['n_trades']} "
                f"胜率={r['win_rate']}% 均笔={r['avg_pnl_pct']}% 加总={r.get('sum_pnl_pct')}% "
                f"出场={r['exits']}"
            )
            for t in r["trades"][-5:]:
                print(
                    f"  {t['day']} {t['side']:5} {t['setup'][:12]:12} "
                    f"pnl={t['pnl_pct']:+.2f}% → {t['reason']}"
                )
            out = OUT / f"{sym.lower()}_{iv}_orb_vwap_replay.json"
            out.write_text(json.dumps(r, ensure_ascii=False, indent=2))
            print("wrote", out)
            time.sleep(0.35)  # 轻限流，避免 Yahoo 429

    # 分周期汇总
    interval_summary = {}
    for iv, rows in by_iv.items():
        ok = [x for x in rows if not x.get("error")]
        n = sum(x["n_trades"] or 0 for x in ok)
        # 合样本胜率：按笔加权近似用各标的 trades 需重读；这里用均笔简单汇总
        wins_est = 0.0
        pnl_sum = 0.0
        for x in ok:
            nt = x["n_trades"] or 0
            if nt and x.get("win_rate") is not None:
                wins_est += nt * (x["win_rate"] / 100.0)
            if x.get("sum_pnl_pct") is not None:
                pnl_sum += x["sum_pnl_pct"]
        interval_summary[iv] = {
            "symbols": ok,
            "n_trades_total": n,
            "win_rate_pooled": round(100.0 * wins_est / n, 1) if n else None,
            "sum_pnl_pct": round(pnl_sum, 2) if ok else None,
            "avg_pnl_per_trade": round(pnl_sum / n, 3) if n else None,
        }
        path = OUT / f"summary_{iv}.json"
        path.write_text(json.dumps(interval_summary[iv], ensure_ascii=False, indent=2))
        print("wrote", path)

    summ_path = OUT / "multi_interval_summary.json"
    payload = {"intervals": interval_summary, "rows": all_rows}
    summ_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    print("\n==== 按周期汇总 ====")
    for iv in intervals:
        s = interval_summary[iv]
        print(
            f"{iv:4} 总笔={s['n_trades_total']:4} "
            f"合样本胜率={s['win_rate_pooled']}% "
            f"均笔={s['avg_pnl_per_trade']} "
            f"加总={s['sum_pnl_pct']}%"
        )
    print("wrote", summ_path)


if __name__ == "__main__":
    main()
