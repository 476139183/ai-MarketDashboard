#!/usr/bin/env python3
"""Replay S→E→T1 + 5x rules on daily (proxy) and 15m (intraday) Yahoo chart JSON."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo

    BJ = ZoneInfo("Asia/Shanghai")
    NY = ZoneInfo("America/New_York")
except Exception:
    BJ = timezone(timedelta(hours=8))
    NY = timezone(timedelta(hours=-4))

# markets/us/backtest/ → US 市场根目录
US_ROOT = Path(__file__).resolve().parents[1]
CACHE = US_ROOT / ".cache"
OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)


def load_chart(path: Path):
    with open(path) as f:
        d = json.load(f)
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c):
            continue
        rows.append(
            {
                "t": t,
                "o": float(o),
                "h": float(h),
                "l": float(l),
                "c": float(c),
                "v": float(v or 0),
                "dt_ny": datetime.fromtimestamp(t, NY),
                "dt_bj": datetime.fromtimestamp(t, BJ),
            }
        )
    return rows, r.get("meta", {})


def atr_series(rows, n=14):
    out = [None] * len(rows)
    trs = []
    for i in range(1, len(rows)):
        pc = rows[i - 1]["c"]
        tr = max(rows[i]["h"] - rows[i]["l"], abs(rows[i]["h"] - pc), abs(rows[i]["l"] - pc))
        trs.append(tr)
        if len(trs) >= n:
            out[i] = sum(trs[-n:]) / n
    return out


def sma_series(vals, n):
    out = [None] * len(vals)
    s = 0.0
    for i, x in enumerate(vals):
        s += x
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


@dataclass
class Trade:
    symbol: str
    side: str
    entry_bj: str
    exit_bj: str
    E: float
    S: float
    T1: float
    exit_px: float
    reason: str
    R1: float
    risk_pct: float
    pnl_px_pct: float
    equity_5x_pct: float
    mode: str


def r1_ok(E, S, T1, side):
    risk = abs(E - S)
    reward = abs(T1 - E)
    if risk <= 0:
        return False, 0.0
    if side == "long" and not (S < E < T1):
        return False, 0.0
    if side == "short" and not (T1 < E < S):
        return False, 0.0
    r1 = reward / risk
    return r1 >= 0.8, r1


def d_max(E, atr, high_vol=True):
    coef = 0.5 if high_vol else 0.6
    return min(0.07 * E, coef * atr)


# ---------- Daily proxy (no lookahead: signal at close i-1, trade day i) ----------


def backtest_daily(symbol: str, rows, high_vol=False) -> list[Trade]:
    closes = [r["c"] for r in rows]
    atrs = atr_series(rows)
    ma20 = sma_series(closes, 20)
    trades: list[Trade] = []
    i = 25
    while i < len(rows):
        atr = atrs[i - 1]
        m = ma20[i - 1]
        if atr is None or m is None:
            i += 1
            continue
        prev = rows[i - 1]
        day = rows[i]
        hv = high_vol or (atr / prev["c"] > 0.06)

        side = None
        # structure bias
        if prev["c"] < m and prev["c"] < rows[i - 2]["c"]:
            side = "short"
            S = prev["h"] + 0.15 * atr
            dm = d_max(prev["c"], atr, hv)
            E_min = max(S / 1.07, S - dm)
            E_max = min(S - 1e-6, prev["c"] + 0.15 * atr)
            if E_min > E_max:
                side = None
        elif prev["c"] > m and prev["c"] > rows[i - 2]["c"]:
            side = "long"
            S = prev["l"] - 0.15 * atr
            dm = d_max(prev["c"], atr, hv)
            E_max = min(S / 0.93, S + dm)
            E_min = max(S + 1e-6, prev["c"] - 0.15 * atr)
            if E_min > E_max:
                side = None
        else:
            side = None

        if not side:
            i += 1
            continue

        # entry fill on day i without peeking beyond OHLC rules
        E = None
        if side == "short":
            if E_min <= day["o"] <= E_max:
                E = day["o"]
            elif day["h"] >= E_min and day["l"] <= E_max:
                # bounce into band: fill at E_max (less favorable short)
                E = min(day["h"], E_max)
                if E < E_min:
                    E = None
        else:
            if E_min <= day["o"] <= E_max:
                E = day["o"]
            elif day["l"] <= E_max and day["h"] >= E_min:
                E = max(day["l"], E_min)
                if E > E_max:
                    E = None

        if E is None:
            i += 1
            continue

        dm = d_max(E, atr, hv)
        if abs(E - S) > dm + 1e-9 or abs(E - S) / E >= 0.07:
            i += 1
            continue

        # T1: 0.6 ATR toward trade, clipped by structure
        if side == "short":
            t_atr = E - 0.6 * atr
            t_struct = min(rows[i - 1]["l"], rows[i - 2]["l"])
            T1 = max(t_atr, t_struct) if t_struct < E else t_atr
            # ensure in band distance
            if E - T1 < 0.35 * atr:
                T1 = E - 0.35 * atr
            if E - T1 > 1.0 * atr:
                T1 = E - 1.0 * atr
        else:
            t_atr = E + 0.6 * atr
            t_struct = max(rows[i - 1]["h"], rows[i - 2]["h"])
            T1 = min(t_atr, t_struct) if t_struct > E else t_atr
            if T1 - E < 0.35 * atr:
                T1 = E + 0.35 * atr
            if T1 - E > 1.0 * atr:
                T1 = E + 1.0 * atr

        ok, r1 = r1_ok(E, S, T1, side)
        if not ok:
            i += 1
            continue

        # same-day exit: conservative path — stop before target if both hit
        exit_px, reason = day["c"], "收盘退出"
        if side == "short":
            stop_hit = day["h"] >= S
            tp_hit = day["l"] <= T1
            if stop_hit and tp_hit:
                # adverse first assumption
                exit_px, reason = S, "止损"
            elif stop_hit:
                exit_px, reason = S, "止损"
            elif tp_hit:
                exit_px, reason = T1, "止盈T1"
        else:
            stop_hit = day["l"] <= S
            tp_hit = day["h"] >= T1
            if stop_hit and tp_hit:
                exit_px, reason = S, "止损"
            elif stop_hit:
                exit_px, reason = S, "止损"
            elif tp_hit:
                exit_px, reason = T1, "止盈T1"

        pnl = (exit_px - E) / E if side == "long" else (E - exit_px) / E
        trades.append(
            Trade(
                symbol=symbol,
                side=side,
                entry_bj=day["dt_bj"].strftime("%Y-%m-%d %H:%M"),
                exit_bj=day["dt_bj"].strftime("%Y-%m-%d %H:%M"),
                E=round(E, 4),
                S=round(S, 4),
                T1=round(T1, 4),
                exit_px=round(exit_px, 4),
                reason=reason,
                R1=round(r1, 2),
                risk_pct=round(abs(E - S) / E * 100, 2),
                pnl_px_pct=round(pnl * 100, 2),
                equity_5x_pct=round(pnl * 500, 2),
                mode="daily",
            )
        )
        i += 1  # no overlapping for simplicity
    return trades


# ---------- 15m intraday (ORB + VWAP + S→E→T1) ----------


def session_days(rows):
    days = {}
    for b in rows:
        if b["dt_ny"].weekday() >= 5:
            continue
        mins = b["dt_ny"].hour * 60 + b["dt_ny"].minute
        if 9 * 60 + 30 <= mins < 16 * 60:
            days.setdefault(b["dt_ny"].date(), []).append(b)
    return days


def make_t1(E, S, atr, side, struct_px):
    """Prefer nearest structure in [0.35,1.0] ATR; else 0.6 ATR."""
    if side == "short":
        atr_t = E - 0.6 * atr
        if struct_px is not None and E - 1.0 * atr <= struct_px <= E - 0.35 * atr:
            T1 = struct_px
        else:
            T1 = atr_t
        T1 = min(max(T1, E - 1.0 * atr), E - 0.35 * atr)
    else:
        atr_t = E + 0.6 * atr
        if struct_px is not None and E + 0.35 * atr <= struct_px <= E + 1.0 * atr:
            T1 = struct_px
        else:
            T1 = atr_t
        T1 = max(min(T1, E + 1.0 * atr), E + 0.35 * atr)
    return T1


def backtest_15m(symbol: str, rows, atr14_daily: float, high_vol=False) -> list[Trade]:
    """Break ORB then wait for pullback into 5x-valid E band; one trade/session."""
    trades: list[Trade] = []
    days = session_days(rows)
    atr = atr14_daily
    if not atr or atr <= 0:
        return trades

    for day, bars in sorted(days.items()):
        if len(bars) < 6:
            continue
        orb_bars = bars[:2] if len(bars) >= 2 else bars[:1]
        orb_h = max(x["h"] for x in orb_bars)
        orb_l = min(x["l"] for x in orb_bars)
        day_o = bars[0]["o"]
        hv = high_vol or (atr / day_o > 0.06)
        # Intraday T1/stop scale: daily ATR is too wide for same-session T1
        atr_i = max(atr * 0.35, day_o * 0.008)

        num = den = 0.0
        pending = None  # dict side/S/E_min/E_max
        position = None

        for i, b in enumerate(bars):
            tp = (b["h"] + b["l"] + b["c"]) / 3
            if b["v"] > 0:
                num += tp * b["v"]
                den += b["v"]
            vwap = num / den if den else b["c"]

            if i < 2:
                continue

            if position is None and pending is None:
                # S/E 用日 ATR（与 5x 规则一致）；T1 用 atr_i 以便盘中可触及
                if b["c"] < orb_l and b["c"] < vwap:
                    S = orb_h + min(0.15 * atr, 0.015 * orb_h)
                    ref = min(vwap, (orb_l + S) / 2)
                    dm = d_max(ref, atr, hv)
                    E_min = max(S / 1.07, S - dm)
                    E_max = min(S - 1e-6, max(orb_l, vwap))
                    if E_min <= E_max:
                        pending = {"side": "short", "S": S, "E_min": E_min, "E_max": E_max}
                elif b["c"] > orb_h and b["c"] > vwap:
                    S = orb_l - min(0.15 * atr, 0.015 * orb_l)
                    ref = max(vwap, (orb_h + S) / 2)
                    dm = d_max(ref, atr, hv)
                    E_max = min(S / 0.93, S + dm)
                    E_min = max(S + 1e-6, min(orb_h, vwap))
                    if E_min <= E_max:
                        pending = {"side": "long", "S": S, "E_min": E_min, "E_max": E_max}

            elif position is None and pending is not None:
                # wait pullback into E band (no chase)
                side = pending["side"]
                E_min, E_max, S = pending["E_min"], pending["E_max"], pending["S"]
                E = None
                if side == "short":
                    # bounce up into band
                    if E_min <= b["o"] <= E_max and b["c"] <= vwap:
                        E = b["o"]
                    elif b["h"] >= E_min and b["l"] <= E_max and b["c"] < S:
                        E = min(b["h"], E_max)
                        if E < E_min:
                            E = None
                    # invalidate if reclaimed ORB strongly
                    if b["c"] > orb_h:
                        pending = None
                else:
                    if E_min <= b["o"] <= E_max and b["c"] >= vwap:
                        E = b["o"]
                    elif b["l"] <= E_max and b["h"] >= E_min and b["c"] > S:
                        E = max(b["l"], E_min)
                        if E > E_max:
                            E = None
                    if b["c"] < orb_l:
                        pending = None

                if E is not None:
                    if abs(E - day_o) > 1.0 * atr:
                        pending = None
                        continue
                    dm = d_max(E, atr, hv)
                    if abs(E - S) > dm or abs(E - S) / E >= 0.07:
                        continue
                    sess_ext = (
                        min(x["l"] for x in bars[: i + 1])
                        if side == "short"
                        else max(x["h"] for x in bars[: i + 1])
                    )
                    T1 = make_t1(E, S, atr_i, side, sess_ext)
                    ok, r1 = r1_ok(E, S, T1, side)
                    if not ok:
                        # T1 过近导致 R1 不足时，略放宽到 0.45*日ATR 再试一次
                        T1 = make_t1(E, S, max(atr_i, 0.45 * atr), side, sess_ext)
                        ok, r1 = r1_ok(E, S, T1, side)
                        if not ok:
                            continue
                    position = {
                        "side": side,
                        "E": E,
                        "S": S,
                        "T1": T1,
                        "r1": r1,
                        "entry": b,
                    }
                    pending = None

            if position is not None:
                side = position["side"]
                E, S, T1 = position["E"], position["S"], position["T1"]
                exit_px = reason = None
                if side == "short":
                    if b["h"] >= S:
                        exit_px, reason = S, "止损"
                    elif b["l"] <= T1:
                        exit_px, reason = T1, "止盈T1"
                else:
                    if b["l"] <= S:
                        exit_px, reason = S, "止损"
                    elif b["h"] >= T1:
                        exit_px, reason = T1, "止盈T1"
                if exit_px is None and i == len(bars) - 1:
                    exit_px, reason = b["c"], "收盘退出"
                if exit_px is not None:
                    # skip exit on same bar as entry if only using close — allow OHLC path
                    if b is position["entry"] and reason == "收盘退出":
                        continue
                    if b is position["entry"] and reason in ("止损", "止盈T1"):
                        # same-bar stop/target: allow
                        pass
                    pnl = (exit_px - E) / E if side == "long" else (E - exit_px) / E
                    trades.append(
                        Trade(
                            symbol=symbol,
                            side=side,
                            entry_bj=position["entry"]["dt_bj"].strftime("%Y-%m-%d %H:%M"),
                            exit_bj=b["dt_bj"].strftime("%Y-%m-%d %H:%M"),
                            E=round(E, 4),
                            S=round(S, 4),
                            T1=round(T1, 4),
                            exit_px=round(exit_px, 4),
                            reason=reason,
                            R1=round(position["r1"], 2),
                            risk_pct=round(abs(E - S) / E * 100, 2),
                            pnl_px_pct=round(pnl * 100, 2),
                            equity_5x_pct=round(pnl * 500, 2),
                            mode="15m",
                        )
                    )
                    break
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    wins = [t for t in trades if t.pnl_px_pct > 0]
    t1s = [t for t in trades if t.reason == "止盈T1"]
    stops = [t for t in trades if t.reason == "止损"]
    eq = [t.equity_5x_pct for t in trades]
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "t1_exits": len(t1s),
        "stop_exits": len(stops),
        "close_exits": len(trades) - len(t1s) - len(stops),
        "avg_R1": round(statistics.mean(t.R1 for t in trades), 2),
        "avg_equity_5x": round(statistics.mean(eq), 2),
        "sum_equity_5x": round(sum(eq), 2),
        "long": sum(1 for t in trades if t.side == "long"),
        "short": sum(1 for t in trades if t.side == "short"),
    }


def copy_agent_json(src_name: str, dest: Path):
    agent = Path("/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools")
    # find newest matching by reading known paths passed as env-like mapping
    return


def main():
    mapping = {
        "SNDK": {
            "1d": Path(
                "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools/83703d48-64bb-4067-9055-3c5d436b8d73.txt"
            ),
            "hv": True,
        },
        "MU": {
            "1d": Path(
                "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools/50fcf1b5-d618-414e-8771-5e325ec4a409.txt"
            ),
            "hv": False,
        },
        "NVDA": {
            "1d": Path(
                "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools/e539ed3d-32e4-4030-8c30-a1231e923f1d.txt"
            ),
            "hv": False,
        },
    }

    fifteen = {
        "SNDK": Path(
            "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools/6bacf62e-244b-4d8d-b529-c9c1224efb2f.txt"
        ),
        "MU": Path(
            "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools/171d1f98-29e0-4886-aaf2-4b8f294162d6.txt"
        ),
        "NVDA": Path(
            "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/agent-tools/929114db-33d6-4d4e-9d99-633bc52f856c.txt"
        ),
    }

    report = []
    all_trades = []

    for sym, cfg in mapping.items():
        rows, meta = load_chart(cfg["1d"])
        # persist copy
        dest = CACHE / f"{sym}_1d.json"
        dest.write_text(cfg["1d"].read_text())
        print(f"\n=== {sym} daily n={len(rows)} first={rows[0]['dt_bj'].date()} last={rows[-1]['dt_bj'].date()} ===")
        atrs = atr_series(rows)
        last_atr = next((atrs[i] for i in range(len(atrs) - 1, -1, -1) if atrs[i]), None)
        print(f"last ATR14={last_atr:.2f}" if last_atr else "no atr")

        d_trades = backtest_daily(sym, rows, high_vol=cfg["hv"])
        s = summarize(d_trades)
        print("daily", s)
        report.append({"symbol": sym, "mode": "daily", **s})
        all_trades.extend(d_trades)

        # last 10 trades sample
        for t in d_trades[-5:]:
            print(
                f"  {t.entry_bj} {t.side} E={t.E} S={t.S} T1={t.T1} -> {t.reason}@{t.exit_px} "
                f"R1={t.R1} eq5x={t.equity_5x_pct}%"
            )

        if sym in fifteen:
            r15, _ = load_chart(fifteen[sym])
            # use median atr of last 20 daily
            recent_atr = statistics.mean([x for x in atrs[-30:] if x])
            t15 = backtest_15m(sym, r15, recent_atr, high_vol=cfg["hv"])
            s15 = summarize(t15)
            print("15m", s15)
            report.append({"symbol": sym, "mode": "15m", **s15})
            all_trades.extend(t15)
            for t in t15[-5:]:
                print(
                    f"  {t.entry_bj} {t.side} E={t.E} S={t.S} T1={t.T1} -> {t.reason}@{t.exit_px} "
                    f"R1={t.R1} eq5x={t.equity_5x_pct}%"
                )

    # write csv
    csv_path = OUT / "trades.csv"
    with open(csv_path, "w") as f:
        f.write(
            "symbol,mode,side,entry_bj,exit_bj,E,S,T1,exit_px,reason,R1,risk_pct,pnl_px_pct,equity_5x_pct\n"
        )
        for t in all_trades:
            f.write(
                f"{t.symbol},{t.mode},{t.side},{t.entry_bj},{t.exit_bj},{t.E},{t.S},{t.T1},"
                f"{t.exit_px},{t.reason},{t.R1},{t.risk_pct},{t.pnl_px_pct},{t.equity_5x_pct}\n"
            )
    summary_path = OUT / "summary.json"
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nwrote", csv_path, summary_path)


if __name__ == "__main__":
    main()
