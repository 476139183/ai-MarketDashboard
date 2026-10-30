#!/usr/bin/env python3
"""SNDK 新交易卡片策略回放：逐步 15m，到价入场，止损/1.5R 止盈，评估是否需改规则。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine import (
    BJ,
    NY,
    TARGET_RR,
    atr14,
    compute_intraday_signal,
    fetch_bars_intraday,
    fetch_chart,
    orb_bar_count,
    regular_session,
    sma,
)

OUT = Path(__file__).resolve().parent / "cache" / "sndk_card_replay.json"


def session_vwap(bars: List[dict]) -> float:
    num = den = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        v = float(b.get("v") or 0)
        if v > 0:
            num += tp * v
            den += v
    return num / den if den else float(bars[-1]["c"])


def replay_day(day_bars: List[dict], atr: float, daily_bias: str) -> Dict[str, Any]:
    orb_n = orb_bar_count("15m")
    if len(day_bars) < orb_n + 1:
        return {"ok": False, "reason": "bars不足"}

    events = []
    trades = []
    position = None  # {side, entry, stop, target, entry_i, entry_t}
    pending = None

    for i in range(orb_n, len(day_bars)):
        sub = day_bars[: i + 1]
        b = day_bars[i]
        orb_h = max(x["h"] for x in sub[:orb_n])
        orb_l = min(x["l"] for x in sub[:orb_n])
        vwap = session_vwap(sub)
        path_l = min(x["l"] for x in sub)
        path_h = max(x["h"] for x in sub)
        px = float(b["c"])
        sig = compute_intraday_signal(
            px=px,
            atr=float(atr),
            orb_l=float(orb_l),
            orb_h=float(orb_h),
            vwap=float(vwap),
            day_o=float(day_bars[0]["o"]),
            path_l=float(path_l),
            path_h=float(path_h),
            bars=sub,
            daily_bias=daily_bias,
        )
        t = b["dt_ny"].strftime("%H:%M")
        row = {
            "t": t,
            "px": round(px, 4),
            "trend": sig.get("trend"),
            "advice": sig.get("advice"),
            "at_entry": sig.get("at_entry"),
            "ref": sig.get("ref_price"),
            "stop": sig.get("stop"),
            "target": sig.get("target"),
            "R1": sig.get("R1"),
            "E": [sig.get("entry_low"), sig.get("entry_high")],
            "orb": [round(orb_l, 2), round(orb_h, 2)],
            "vwap": round(vwap, 2),
            "action": sig.get("action_label"),
        }
        events.append(row)

        # 先处理持仓：本根触止损/止盈（保守：同根先止损）
        if position is not None:
            side = position["side"]
            stop, target = position["stop"], position["target"]
            hit_stop = b["h"] >= stop if side == "short" else b["l"] <= stop
            hit_tgt = b["l"] <= target if side == "short" else b["h"] >= target
            exit_px = exit_why = None
            if hit_stop and hit_tgt:
                exit_px, exit_why = stop, "同根先止损"
            elif hit_stop:
                exit_px, exit_why = stop, "止损"
            elif hit_tgt:
                exit_px, exit_why = target, "止盈"
            if exit_px is not None:
                sign = -1 if side == "short" else 1
                pnl = sign * (exit_px / position["entry"] - 1) * 100
                trades.append(
                    {
                        **position,
                        "exit": round(exit_px, 4),
                        "exit_t": t,
                        "why": exit_why,
                        "pnl_pct": round(pnl, 3),
                    }
                )
                position = None
                pending = None

        # 入场：上一根发出到价信号 → 本根开盘成交（更贴近实盘）
        if pending is not None and position is None:
            e = float(b["o"])
            side = pending["side"]
            stop, target = pending["stop"], pending["target"]
            # 开盘必须仍大致顺向（空：开盘低于止损）
            ok = (side == "short" and e < stop) or (side == "long" and e > stop)
            if ok:
                position = {
                    "side": side,
                    "entry": round(e, 4),
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    "entry_t": t,
                    "signal_t": pending["signal_t"],
                    "R1": pending.get("R1"),
                    "trend": pending.get("trend"),
                }
            pending = None

        if position is None and pending is None:
            advice = sig.get("advice")
            if advice in ("做多", "做空") and sig.get("at_entry") and sig.get("stop") and sig.get("target"):
                pending = {
                    "side": "short" if advice == "做空" else "long",
                    "stop": float(sig["stop"]),
                    "target": float(sig["target"]),
                    "signal_t": t,
                    "R1": sig.get("R1"),
                    "trend": sig.get("trend"),
                }

    # 收盘强平
    if position is not None:
        last = day_bars[-1]
        e = float(last["c"])
        sign = -1 if position["side"] == "short" else 1
        pnl = sign * (e / position["entry"] - 1) * 100
        trades.append(
            {
                **position,
                "exit": round(e, 4),
                "exit_t": last["dt_ny"].strftime("%H:%M"),
                "why": "收盘强平",
                "pnl_pct": round(pnl, 3),
            }
        )

    actionable_n = sum(1 for e in events if e.get("at_entry") and e.get("advice") in ("做多", "做空"))
    return {
        "ok": True,
        "day": day_bars[0]["dt_ny"].date().isoformat(),
        "orb": [round(min(x["l"] for x in day_bars[:orb_n]), 2), round(max(x["h"] for x in day_bars[:orb_n]), 2)],
        "day_l": round(min(x["l"] for x in day_bars), 2),
        "day_h": round(max(x["h"] for x in day_bars), 2),
        "actionable_bars": actionable_n,
        "trades": trades,
        "events": events,
        "daily_bias": daily_bias,
    }


def summarize(days: List[Dict[str, Any]]) -> Dict[str, Any]:
    trades = [t for d in days if d.get("ok") for t in d.get("trades") or []]
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    by_why: Dict[str, int] = defaultdict(int)
    for t in trades:
        by_why[t["why"]] += 1
    pnl = [t["pnl_pct"] for t in trades]
    return {
        "target_rr": TARGET_RR,
        "days": len(days),
        "days_with_trade": sum(1 for d in days if d.get("trades")),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "avg_pnl_pct": round(sum(pnl) / len(pnl), 3) if pnl else None,
        "sum_pnl_pct": round(sum(pnl), 3) if pnl else None,
        "exit_why": dict(by_why),
        "trade_list": trades,
    }


def main() -> None:
    symbol = "SNDK"
    daily, _, _ = fetch_chart(symbol, "3mo", "1d", prefer_cache=True)
    atr = atr14(daily)
    ma20 = sma([x["c"] for x in daily], 20)
    bars, meta, src = fetch_bars_intraday(symbol, "15m", prefer_cache=False)
    sess = regular_session(bars)
    by_day: Dict[Any, List[dict]] = defaultdict(list)
    for b in sess:
        by_day[b["dt_ny"].date()].append(b)

    # 日线 bias：用当日收盘相对 MA20（回放用前收更严，这里用当日已知 close 近似）
    close_by_day = {}
    for d in daily:
        close_by_day[d["dt_ny"].date()] = float(d["c"])

    days_out = []
    for day in sorted(by_day):
        day_bars = by_day[day]
        if len(day_bars) < 4:
            continue
        px = close_by_day.get(day) or float(day_bars[-1]["c"])
        bias = "日线偏空" if ma20 and px < ma20 else ("日线偏多" if ma20 else "—")
        # ATR：用截至前一日的日线更严谨
        prior = [x for x in daily if x["dt_ny"].date() < day]
        day_atr = atr14(prior) if len(prior) >= 15 else atr
        if not day_atr:
            continue
        rep = replay_day(day_bars, float(day_atr), bias)
        days_out.append(rep)

    summary = summarize(days_out)
    # 今日细节
    today = datetime.now(BJ).astimezone(NY).date()
    today_rep = next((d for d in days_out if d.get("day") == today.isoformat()), None)
    if not today_rep and days_out:
        today_rep = days_out[-1]

    report = {
        "symbol": symbol,
        "src": src,
        "n_days": len(days_out),
        "summary": summary,
        "latest_day": {
            k: v
            for k, v in (today_rep or {}).items()
            if k != "events"
        },
        "latest_timeline": (today_rep or {}).get("events"),
        "notes": [
            "入场：信号到价后下一根15m开盘",
            "同根止损止盈同时触及时记为止损",
            f"止盈按卡片 TARGET_RR={TARGET_RR}",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=== SNDK 卡片策略回放 ===")
    print("源", src, "天数", summary["days"], "有成交日", summary["days_with_trade"])
    print(
        "成交", summary["trades"],
        "胜率", summary["win_rate"],
        "均pnl%", summary["avg_pnl_pct"],
        "合计%", summary["sum_pnl_pct"],
        "出场", summary["exit_why"],
    )
    for t in summary["trade_list"][-12:]:
        print(
            f"  {t.get('signal_t')}->{t.get('exit_t')} {t['side']} "
            f"E={t['entry']} S={t['stop']} T={t['target']} "
            f"→{t['exit']} {t['why']} pnl={t['pnl_pct']}%"
        )
    if today_rep and today_rep.get("ok"):
        print("\n=== 最近交易日", today_rep["day"], "bias", today_rep.get("daily_bias"))
        print("ORB", today_rep["orb"], "dayL/H", today_rep["day_l"], today_rep["day_h"])
        print("到价根数", today_rep["actionable_bars"], "成交", len(today_rep["trades"]))
        for e in today_rep["events"]:
            mark = ""
            if e.get("at_entry") and e.get("advice") in ("做多", "做空"):
                mark = " <<到价"
            if e["advice"] not in ("观望",) or e.get("at_entry"):
                print(
                    f"  {e['t']} px={e['px']} {e['trend']}/{e['advice']} "
                    f"at={e['at_entry']} E={e['E']} S={e['stop']} T={e['target']} R={e['R1']}{mark}"
                )
        for t in today_rep["trades"]:
            print("  TRADE", t)
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
