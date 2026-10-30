#!/usr/bin/env python3
"""验证：反抽空/回踩多（锚定 ORB 上下半区）vs 现行规则。先对比再改引擎。"""
from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine import (
    BJ,
    NY,
    TARGET_RR,
    atr14,
    compute_intraday_signal,
    ensure_rr_target,
    fetch_bars_intraday,
    fetch_chart,
    orb_bar_count,
    pick_t1,
    r1,
    regular_session,
    short_term_trend,
    sma,
    _min_gap,
    _risk_label,
    _score_strength,
    _strength_label,
)

OUT = Path(__file__).resolve().parent / "cache" / "sndk_entry_v2_compare.json"


def session_vwap(bars: List[dict]) -> float:
    num = den = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        v = float(b.get("v") or 0)
        if v > 0:
            num += tp * v
            den += v
    return num / den if den else float(bars[-1]["c"])


def compute_v2(
    *,
    px: float,
    atr: float,
    orb_l: float,
    orb_h: float,
    vwap: float,
    day_o: float,
    path_l: float,
    path_h: float,
    bars: List[dict],
    daily_bias: str = "—",
) -> Dict[str, Any]:
    """反抽空 / 回踩多：入场带锚定 ORB 上/下半区，避免 VWAP 下移后杀跌到价。"""
    atr_i = max(atr * 0.35, px * 0.008)
    gap = _min_gap(px, atr_i)
    buf = min(0.15 * atr, 0.015 * max(orb_h, orb_l, px))
    chase_buf = max(0.25 * atr_i, 0.004 * px)
    st_trend = short_term_trend(bars)
    sc = int(st_trend.get("score") or 0)
    trend_tag = st_trend.get("label") or "短期—"
    daily_tag = daily_bias if daily_bias and daily_bias != "—" else ""

    broke_down = path_l < orb_l - 1e-9
    broke_up = path_h > orb_h + 1e-9
    orb_mid = (orb_l + orb_h) / 2.0
    orb_span = max(orb_h - orb_l, gap)
    below_mid = px <= min(vwap, orb_mid)
    above_mid = px >= max(vwap, orb_mid)

    # 趋势：原 score + 冲高回落（价已跌回 VWAP 下）才提前定下跌
    if broke_up and px < vwap and sc <= 0:
        trend, structure = "下跌", "空头"
    elif broke_down and px > vwap and sc >= 0:
        trend, structure = "上涨", "多头"
    elif sc <= -2 and below_mid:
        trend, structure = "下跌", "空头"
    elif sc >= 2 and above_mid:
        trend, structure = "上涨", "多头"
    else:
        trend, structure = "震荡", "震荡"

    short_invalid = bool(broke_down and px > orb_h)
    long_invalid = bool(broke_up and px < orb_l)

    advice = signal = "观望"
    reason = ""
    setup = "震荡观察"
    side = None
    S = S_key = T1 = T1_key = None
    E_band = None
    R1v = stress = risk_pct = None
    entry_ready = at_entry = False
    entry_label = stop_note = None
    ref_price = None
    strength, strength_label, risk_label = 1, "弱", "—"

    if trend == "下跌":
        advice = signal = "做空"
        setup = "趋势空·反抽"
        side = "short"
        entry_label = "做空入场价"
        S = orb_h + buf
        stop_note = S_key = f"ORB上沿 {orb_h:.2f} + 缓冲"
        # 锚定 ORB 上半区反抽，不让带随砸盘 VWAP 下移
        band_hi = float(orb_h - gap)
        band_lo = float(max(orb_mid, orb_h - 0.40 * orb_span))
        if vwap >= orb_mid:
            band_lo = max(band_lo, min(float(vwap), band_hi - gap))
        if band_hi < band_lo:
            band_lo, band_hi = orb_mid, orb_h - gap
        S = max(S, band_hi + gap)
        E_band = [round(band_lo, 4), round(band_hi, 4)]
        E = (band_lo + band_hi) / 2.0
        near_low = px <= path_l + chase_buf or px <= orb_l + chase_buf
        # 必须在 ORB 中轴上方才算反抽空（禁止下半区杀跌）
        in_upper = px >= orb_mid - 1e-9
        at_entry = band_lo <= px <= band_hi and not near_low and in_upper
        chasing = px < band_lo or not in_upper
        T1, T1_key = pick_t1(
            side="short", E=E, atr_i=atr_i, E_min=band_lo, E_max=band_hi,
            day_o=day_o, bars=bars, px=px, in_e=at_entry,
        )
        ref_price = round(px if at_entry else E, 4)
        T1, T1_key = ensure_rr_target(
            side="short", E=float(ref_price), S=float(S), T1=float(T1), T1_key=T1_key
        )
        R1v = r1(ref_price, S, T1, "short")
        risk_pct = abs(ref_price - S) / ref_price * 100 if ref_price else None
        strength = _score_strength(sc=sc, side="short", px=px, orb_l=orb_l, orb_h=orb_h, R=R1v)
        if daily_tag == "日线偏多":
            strength = max(1, strength - 1)
        strength_label = _strength_label(strength)
        risk_label = _risk_label(risk_pct)
        entry_ready = at_entry
        r_note = f"；盈亏比{R1v:.2f}:1" if R1v is not None else ""
        if near_low or (not in_upper and px < band_lo):
            reason = f"下跌趋势，不杀跌；等反抽至ORB上半区[{band_lo:.2f},{band_hi:.2f}]{r_note}"
            entry_ready = at_entry = False
        elif at_entry:
            reason = f"下跌趋势·反抽到价，可做空（{trend_tag}{('·'+daily_tag) if daily_tag else ''}）{r_note}"
        elif chasing:
            reason = f"下跌趋势，未到反抽带；等[{band_lo:.2f},{band_hi:.2f}]{r_note}"
        else:
            reason = f"下跌趋势，等反抽[{band_lo:.2f},{band_hi:.2f}]（{trend_tag}）{r_note}"

    elif trend == "上涨":
        advice = signal = "做多"
        setup = "趋势多·回踩"
        side = "long"
        entry_label = "做多入场价"
        S = orb_l - buf
        stop_note = S_key = f"ORB下沿 {orb_l:.2f} − 缓冲"
        band_lo = float(orb_l + chase_buf)
        band_hi = float(min(orb_mid, orb_l + 0.40 * orb_span))
        if vwap <= orb_mid:
            band_hi = min(band_hi, max(float(vwap), band_lo + gap))
        if band_hi < band_lo:
            band_lo, band_hi = orb_l + chase_buf, orb_mid
        band_lo = max(band_lo, S + gap)
        if band_hi < band_lo:
            band_hi = band_lo + gap * 0.5
        E_band = [round(band_lo, 4), round(band_hi, 4)]
        E = (band_lo + band_hi) / 2.0
        near_high = px >= path_h - chase_buf or px >= orb_h - chase_buf
        in_lower = px <= orb_mid + 1e-9
        at_entry = band_lo <= px <= band_hi and not near_high and in_lower
        chasing = px > band_hi or not in_lower
        T1, T1_key = pick_t1(
            side="long", E=E, atr_i=atr_i, E_min=band_lo, E_max=band_hi,
            day_o=day_o, bars=bars, px=px, in_e=at_entry,
        )
        ref_price = round(px if at_entry else E, 4)
        T1, T1_key = ensure_rr_target(
            side="long", E=float(ref_price), S=float(S), T1=float(T1), T1_key=T1_key
        )
        R1v = r1(ref_price, S, T1, "long")
        risk_pct = abs(ref_price - S) / ref_price * 100 if ref_price else None
        strength = _score_strength(sc=sc, side="long", px=px, orb_l=orb_l, orb_h=orb_h, R=R1v)
        if daily_tag == "日线偏空":
            strength = max(1, strength - 1)
        strength_label = _strength_label(strength)
        risk_label = _risk_label(risk_pct)
        entry_ready = at_entry
        r_note = f"；盈亏比{R1v:.2f}:1" if R1v is not None else ""
        if near_high or (not in_lower and px > band_hi):
            reason = f"上涨趋势，不追涨；等回踩至ORB下半区[{band_lo:.2f},{band_hi:.2f}]{r_note}"
            entry_ready = at_entry = False
        elif at_entry:
            reason = f"上涨趋势·回踩到价，可做多（{trend_tag}{('·'+daily_tag) if daily_tag else ''}）{r_note}"
        elif chasing:
            reason = f"上涨趋势，未到回踩带；等[{band_lo:.2f},{band_hi:.2f}]{r_note}"
        else:
            reason = f"上涨趋势，等回踩[{band_lo:.2f},{band_hi:.2f}]（{trend_tag}）{r_note}"
    else:
        reason = f"震荡（{trend_tag}），等方向"

    if signal not in ("做多", "做空") and short_invalid:
        advice = signal = "平空"
        entry_ready = at_entry = False
        side = "cover_short"
        setup = "ORB失效平空"
        S = round(orb_h + buf, 4)
        stop_note = S_key = f"ORB上沿 {orb_h:.2f}"
        ref_price = round(px, 4)
        reason = f"空头失效：站上ORB上沿 {orb_h:.2f}，先平空。" + reason
    elif signal not in ("做多", "做空") and long_invalid:
        advice = signal = "平多"
        entry_ready = at_entry = False
        side = "cover_long"
        setup = "ORB失效平多"
        S = round(orb_l - buf, 4)
        stop_note = S_key = f"ORB下沿 {orb_l:.2f}"
        ref_price = round(px, 4)
        reason = f"多头失效：跌破ORB下沿 {orb_l:.2f}，先平多。" + reason

    entry_low = E_band[0] if E_band else None
    entry_high = E_band[1] if E_band else None
    stop = round(S, 4) if S is not None else None
    target = round(T1, 4) if T1 is not None else None
    actionable = signal in ("做多", "做空") and entry_ready
    if signal in ("平空", "平多"):
        action_label = "先平仓"
    elif actionable:
        action_label = "可按点位执行"
    elif signal in ("做多", "做空"):
        action_label = "等回踩/反抽"
    else:
        action_label = "当前观望"

    return {
        "trend": trend,
        "advice": advice,
        "signal": signal,
        "reason": reason,
        "structure": structure,
        "setup": setup,
        "actionable": actionable,
        "action_label": action_label,
        "at_entry": at_entry,
        "entry_ready": entry_ready,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "ref_price": ref_price,
        "stop": stop,
        "S": stop,
        "target": target,
        "T1": target,
        "R1": round(R1v, 2) if R1v is not None else None,
        "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
        "strength": strength if signal in ("做多", "做空") else None,
        "short_trend": st_trend,
        "orb": {"low": round(orb_l, 4), "high": round(orb_h, 4)},
        "vwap": round(vwap, 4),
    }


def replay_day(day_bars, atr, daily_bias, signal_fn) -> Dict[str, Any]:
    orb_n = orb_bar_count("15m")
    if len(day_bars) < orb_n + 1:
        return {"ok": False, "trades": [], "events": []}
    events, trades = [], []
    position = pending = None
    for i in range(orb_n, len(day_bars)):
        sub = day_bars[: i + 1]
        b = day_bars[i]
        orb_h = max(x["h"] for x in sub[:orb_n])
        orb_l = min(x["l"] for x in sub[:orb_n])
        vwap = session_vwap(sub)
        path_l = min(x["l"] for x in sub)
        path_h = max(x["h"] for x in sub)
        px = float(b["c"])
        sig = signal_fn(
            px=px, atr=float(atr), orb_l=float(orb_l), orb_h=float(orb_h),
            vwap=float(vwap), day_o=float(day_bars[0]["o"]),
            path_l=float(path_l), path_h=float(path_h), bars=sub, daily_bias=daily_bias,
        )
        t = b["dt_ny"].strftime("%H:%M")
        events.append({
            "t": t, "px": round(px, 4), "trend": sig.get("trend"), "advice": sig.get("advice"),
            "at_entry": sig.get("at_entry"), "E": [sig.get("entry_low"), sig.get("entry_high")],
            "stop": sig.get("stop"), "target": sig.get("target"), "R1": sig.get("R1"),
        })
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
                trades.append({
                    **position, "exit": round(exit_px, 4), "exit_t": t, "why": exit_why,
                    "pnl_pct": round(sign * (exit_px / position["entry"] - 1) * 100, 3),
                })
                position = None
                pending = None
        if pending is not None and position is None:
            e = float(b["o"])
            side = pending["side"]
            stop = pending["stop"]
            ok = (side == "short" and e < stop) or (side == "long" and e > stop)
            if ok:
                position = {
                    "side": side, "entry": round(e, 4), "stop": round(stop, 4),
                    "target": round(pending["target"], 4), "entry_t": t,
                    "signal_t": pending["signal_t"], "R1": pending.get("R1"),
                }
            pending = None
        if position is None and pending is None:
            if sig.get("advice") in ("做多", "做空") and sig.get("at_entry") and sig.get("stop") and sig.get("target"):
                pending = {
                    "side": "short" if sig["advice"] == "做空" else "long",
                    "stop": float(sig["stop"]), "target": float(sig["target"]),
                    "signal_t": t, "R1": sig.get("R1"),
                }
    if position is not None:
        last = day_bars[-1]
        e = float(last["c"])
        sign = -1 if position["side"] == "short" else 1
        trades.append({
            **position, "exit": round(e, 4),
            "exit_t": last["dt_ny"].strftime("%H:%M"), "why": "收盘强平",
            "pnl_pct": round(sign * (e / position["entry"] - 1) * 100, 3),
        })
    return {
        "ok": True,
        "day": day_bars[0]["dt_ny"].date().isoformat(),
        "orb": [round(min(x["l"] for x in day_bars[:orb_n]), 2), round(max(x["h"] for x in day_bars[:orb_n]), 2)],
        "actionable_bars": sum(1 for e in events if e.get("at_entry") and e.get("advice") in ("做多", "做空")),
        "trades": trades,
        "events": events,
    }


def summarize(days: List[dict]) -> Dict[str, Any]:
    trades = [t for d in days if d.get("ok") for t in d.get("trades") or []]
    wins = [t for t in trades if t["pnl_pct"] > 0]
    by_why: Dict[str, int] = defaultdict(int)
    for t in trades:
        by_why[t["why"]] += 1
    pnl = [t["pnl_pct"] for t in trades]
    shorts = [t for t in trades if t["side"] == "short"]
    return {
        "days": len(days),
        "days_with_trade": sum(1 for d in days if d.get("trades")),
        "trades": len(trades),
        "short_trades": len(shorts),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "avg_pnl_pct": round(sum(pnl) / len(pnl), 3) if pnl else None,
        "sum_pnl_pct": round(sum(pnl), 3) if pnl else None,
        "exit_why": dict(by_why),
        "avg_entry_vs_dayhigh_short": None,
    }


def main() -> None:
    symbol = "SNDK"
    daily, _, _ = fetch_chart(symbol, "3mo", "1d", prefer_cache=True)
    atr = atr14(daily)
    ma20 = sma([x["c"] for x in daily], 20)
    bars, meta, src = fetch_bars_intraday(symbol, "15m", prefer_cache=True)
    sess = regular_session(bars)
    by_day: Dict[Any, List[dict]] = defaultdict(list)
    for b in sess:
        by_day[b["dt_ny"].date()].append(b)
    close_by_day = {d["dt_ny"].date(): float(d["c"]) for d in daily}

    old_days, new_days = [], []
    for day in sorted(by_day):
        day_bars = by_day[day]
        if len(day_bars) < 4:
            continue
        px = close_by_day.get(day) or float(day_bars[-1]["c"])
        bias = "日线偏空" if ma20 and px < ma20 else ("日线偏多" if ma20 else "—")
        prior = [x for x in daily if x["dt_ny"].date() < day]
        day_atr = atr14(prior) if len(prior) >= 15 else atr
        if not day_atr:
            continue
        old_days.append(replay_day(day_bars, float(day_atr), bias, compute_intraday_signal))
        new_days.append(replay_day(day_bars, float(day_atr), bias, compute_v2))

    s_old, s_new = summarize(old_days), summarize(new_days)
    today = datetime.now(BJ).astimezone(NY).date().isoformat()
    # 若无今日则用最近日
    old_today = next((d for d in old_days if d.get("day") == today), old_days[-1] if old_days else {})
    new_today = next((d for d in new_days if d.get("day") == today), new_days[-1] if new_days else {})

    report = {
        "src": src,
        "old": s_old,
        "new_v2": s_new,
        "latest_day": old_today.get("day"),
        "old_latest_events": [
            e for e in (old_today.get("events") or [])
            if e.get("advice") in ("做多", "做空") or e.get("at_entry")
        ],
        "new_latest_events": [
            e for e in (new_today.get("events") or [])
            if e.get("advice") in ("做多", "做空") or e.get("at_entry")
        ],
        "old_latest_trades": old_today.get("trades"),
        "new_latest_trades": new_today.get("trades"),
        "prefer_v2": None,
    }
    # 决策：合计收益不差 + 有止盈改善，且不大幅增加止损次数
    old_sum = s_old.get("sum_pnl_pct") or 0
    new_sum = s_new.get("sum_pnl_pct") or 0
    old_tp = (s_old.get("exit_why") or {}).get("止盈", 0)
    new_tp = (s_new.get("exit_why") or {}).get("止盈", 0)
    old_sl = (s_old.get("exit_why") or {}).get("止损", 0)
    new_sl = (s_new.get("exit_why") or {}).get("止损", 0)
    better = new_sum >= old_sum - 0.3 and new_tp >= old_tp and new_sl <= old_sl + 2
    report["prefer_v2"] = better

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("=== SNDK 现行 vs 反抽/回踩锚定ORB ===")
    print("源", src)
    print("OLD", json.dumps(s_old, ensure_ascii=False))
    print("NEW", json.dumps(s_new, ensure_ascii=False))
    print("\n最近日", report["latest_day"])
    print("OLD events:")
    for e in report["old_latest_events"]:
        print(" ", e)
    print("OLD trades", report["old_latest_trades"])
    print("NEW events:")
    for e in report["new_latest_events"]:
        print(" ", e)
    print("NEW trades", report["new_latest_trades"])
    print("prefer_v2", better)
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
