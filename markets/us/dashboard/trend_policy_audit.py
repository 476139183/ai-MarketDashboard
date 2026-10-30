#!/usr/bin/env python3
"""Offline chronological direction audit; no orders, forecasts or parameter search."""
import importlib.util
import json
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from engine import BJ, NY, atr14, compute_intraday_signal, confirm_trend
from backtest_trend_policy import BASELINE_COMMIT, baseline_source

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent / ".cache/session-bars.sqlite3"
OUT = ROOT / "cache/trend-policy-audit.json"


def audit(baseline):
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM bars WHERE source='Massive historical aggregates' "
                            "AND session='regular' ORDER BY symbol,timestamp").fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["symbol"], row["trading_date"])].append(row)
    symbols = sorted({s for s, _ in grouped})
    stats = {version: dict(predictions=0, correct=0, plans=0, ready=0, conflict=0)
             for version in ("before", "after")}
    by_symbol = {}
    total = days = 0
    for symbol in symbols:
        daily = []
        history = []
        symbol_stats = {v: dict(predictions=0, correct=0) for v in stats}
        for _, day in sorted(k for k in grouped if k[0] == symbol):
            minute_rows = grouped[(symbol, day)]
            buckets = defaultdict(list)
            for row in minute_rows:
                buckets[row["timestamp"] // 900 * 900].append(row)
            bars = []
            for t, group in sorted(buckets.items()):
                dt = datetime.fromtimestamp(t, NY)
                bars.append(dict(t=t, dt_ny=dt, dt_bj=dt.astimezone(BJ),
                                 o=group[0]["open"], c=group[-1]["close"],
                                 h=max(r["high"] for r in group), l=min(r["low"] for r in group),
                                 v=sum(float(r["volume"] or 0) for r in group)))
            if not bars:
                continue
            start = int(bars[0]["dt_ny"].replace(hour=9, minute=30).timestamp())
            complete = {b["t"] for b in bars} == set(range(start, start + 23400, 900))
            atr = atr14(daily)
            if complete and atr and len(daily) >= 25:
                days += 1
                for i in range(4, len(bars) - 4):
                    sub = bars[:i + 1]
                    volume = sum(b["v"] for b in sub)
                    if not volume or any(b["v"] <= 0 for b in sub):
                        continue
                    now = datetime.fromtimestamp(bars[i]["t"] + 900, NY)
                    context = confirm_trend(daily, history[-156:] + sub, now)
                    vwap = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in sub) / volume
                    args = dict(px=sub[-1]["c"], atr=atr, orb_l=min(b["l"] for b in sub[:2]),
                                orb_h=max(b["h"] for b in sub[:2]), vwap=vwap, day_o=sub[0]["o"],
                                path_l=min(b["l"] for b in sub), path_h=max(b["h"] for b in sub), bars=sub)
                    before = baseline.compute_intraday_signal(**args)
                    after = compute_intraday_signal(**args, trend_context=context)
                    future = bars[i + 4]["c"] - sub[-1]["c"]
                    total += 1
                    for version, result in (("before", before), ("after", after)):
                        bias = result.get("trend")
                        if bias in ("上涨", "下跌"):
                            correct = (future > 0 if bias == "上涨" else future < 0)
                            stats[version]["predictions"] += 1
                            stats[version]["correct"] += int(correct)
                            symbol_stats[version]["predictions"] += 1
                            symbol_stats[version]["correct"] += int(correct)
                        if result.get("advice") in ("做多", "做空"):
                            stats[version]["plans"] += 1
                            stats[version]["ready"] += int(bool(result.get("actionable")))
                            side = "up" if result["advice"] == "做多" else "down"
                            stats[version]["conflict"] += int(context["bias"] != side)
            # 当前交易日收盘后才进入下一天的日线和小时历史，绝不借用同日完整数据。
            if complete:
                daily.append(dict(t=start, dt_ny=bars[0]["dt_ny"], o=bars[0]["o"],
                                  c=bars[-1]["c"], h=max(b["h"] for b in bars), l=min(b["l"] for b in bars)))
                history.extend(bars)
            history = history[-156:]
        by_symbol[symbol] = symbol_stats
    for row in stats.values():
        row["direction_accuracy_pct"] = round(row["correct"] / row["predictions"] * 100, 2) if row["predictions"] else None
        row["coverage_pct"] = round(row["predictions"] / total * 100, 2) if total else 0
    return dict(generated_bj=datetime.now(BJ).isoformat(), days=days, evaluated_bars=total,
                stats=stats, by_symbol=by_symbol,
                method="Prior closed daily ATR/MA20; 1h and 15m visible at observation time; direction scored against close 4 bars later. No trade fills or portfolio returns calculated.",
                limitations="Exploratory, previously observed 4-stock data only; overlapping 1h observations are not independent; not a fresh holdout; does not prove profitability or extend to all 82 stocks.")


def main():
    source = baseline_source()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "baseline.py"
        path.write_text(source)
        spec = importlib.util.spec_from_file_location("baseline", path)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        report = audit(baseline)
    report["baseline_commit"] = BASELINE_COMMIT
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
