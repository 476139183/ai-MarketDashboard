#!/usr/bin/env python3
"""SNDK 15m wake：盘前(04:00–09:30) / RTH(09:30–16:00) / 盘后(16:00–20:00) ET。"""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# 扩展时段：04:00–20:00 ET；之外睡到下一交易日 04:00
EXT_START = (4, 0)
EXT_END = (20, 0)
RTH_OPEN = (9, 30)
RTH_CLOSE = (16, 0)


def now_ny() -> datetime:
    return datetime.now(NY)


def next_weekday(d):
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


def at(d, hm) -> datetime:
    h, m = hm
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=NY)


def phase_of(t: datetime) -> str:
    if t.weekday() >= 5:
        return "weekend"
    mins = t.hour * 60 + t.minute
    if mins < EXT_START[0] * 60 + EXT_START[1]:
        return "overnight"
    if mins < RTH_OPEN[0] * 60 + RTH_OPEN[1]:
        return "pre"
    if mins < RTH_CLOSE[0] * 60 + RTH_CLOSE[1]:
        return "rth"
    if mins < EXT_END[0] * 60 + EXT_END[1]:
        return "post"
    return "closed"


def prompt_for(phase: str) -> str:
    common = (
        "SIGNAL_LIVE=1 用现行新策略 markets/us/dashboard engine.analyze_symbol('SNDK','15m')："
        "15m信号→日线MA20闸→低位多·洗盘/不追涨杀跌；"
        "输出做多/做空/观望/平多/平空 + setup + E/S/T1/R1/entry_ready/daily_bias，北京时间；"
        "相对上一轮只说变化；标明时段（盘前/盘中/盘后）。"
    )
    if phase == "pre":
        body = (
            "美股盘前更新闪迪 SNDK："
            + common
            + "盘前流动性薄；列出缺口与开盘触发/失效位。"
        )
    elif phase == "post":
        body = (
            "美股盘后更新闪迪 SNDK："
            + common
            + "盘后流动性薄；勿当常规盘中追单。"
        )
    else:
        body = "美股RTH盘中更新闪迪 SNDK（新策略）：" + common
    return f'AGENT_LOOP_TICK_sndk15_rth {{"prompt":"{body}"}}'


def next_quarter(t: datetime) -> datetime:
    slot = (t.minute // 15 + 1) * 15
    base = t.replace(second=0, microsecond=0)
    if slot >= 60:
        return base.replace(minute=0) + timedelta(hours=1)
    return base.replace(minute=slot)


def next_ext_start(t: datetime) -> datetime:
    """下一交易日（或今日尚未开始的）04:00 ET。"""
    d = t.date()
    start = at(d, EXT_START)
    if t.weekday() < 5 and t < start:
        return start
    nd = next_weekday(d) if t.weekday() < 5 else d
    if t.weekday() >= 5:
        nd = d
        while nd.weekday() >= 5:
            nd += timedelta(days=1)
    else:
        nd = next_weekday(d)
    return at(nd, EXT_START)


def sleep_until(tgt: datetime, label: str) -> None:
    secs = max(1, int((tgt - now_ny()).total_seconds()))
    print(
        f"AGENT_LOOP_SLEEP_sndk15_rth waiting {secs}s until {label} "
        f"{tgt.strftime('%Y-%m-%d %H:%M %Z')} (now {now_ny().strftime('%H:%M:%S')})",
        flush=True,
    )
    time.sleep(secs)


def main() -> None:
    while True:
        t = now_ny()
        ph = phase_of(t)

        if ph in ("weekend", "overnight", "closed"):
            sleep_until(next_ext_start(t), "ext open 04:00")
            continue

        # 扩展时段内：对齐下一 :00/:15/:30/:45，超出 20:00 则等次日 04:00
        nxt = next_quarter(t)
        end = at(t.date(), EXT_END)
        if nxt > end:
            sleep_until(next_ext_start(at(t.date(), EXT_END)), "ext open 04:00")
            continue

        sleep_until(nxt, f"{phase_of(nxt)} {nxt.strftime('%H:%M')}")
        t2 = now_ny()
        ph2 = phase_of(t2)
        if ph2 in ("pre", "rth", "post"):
            print(prompt_for(ph2), flush=True)


if __name__ == "__main__":
    main()
