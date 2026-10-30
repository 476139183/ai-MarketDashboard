#!/usr/bin/env python3
"""Chronological minute-fill replay with a small, predefined train/validation grid."""
import importlib.util
import json
import sqlite3
import subprocess
import tempfile
from html import escape
from collections import defaultdict
from datetime import datetime
from itertools import product
from pathlib import Path

from engine import BJ, NY, atr14, compute_intraday_signal, confirm_trend

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent / ".cache/session-bars.sqlite3"
OUT = ROOT / "cache/trend-trade-backtest.json"
BASELINE_COMMIT = "09a8a1f103f1ad51e4daf55f9a496b96640edb1a"


def baseline_source():
    return subprocess.run(["git", "show", f"{BASELINE_COMMIT}:markets/us/dashboard/engine.py"],
                          cwd=ROOT, check=True, capture_output=True, text=True).stdout


def replay_minutes(rows, signals, *, fee_bps=1, slip_bps=2, min_rr=1.5):
    """One minute after a closed-bar signal, fill at open only if band and RR still hold."""
    position = None
    trades = []
    slip, fee = slip_bps / 10000, fee_bps / 10000

    def finish(px, ts, reason):
        nonlocal position
        p = position
        sign = 1 if p["side"] == "做多" else -1
        exit_price = px * (1 - sign * slip)
        gross = sign * (exit_price - p["entry"])
        costs = fee * (exit_price + p["entry"])
        trades.append({**p, "exit": exit_price, "exit_t": ts, "reason": reason,
                       "gross_pct": gross / p["entry"] * 100,
                       "net_pct": (gross - costs) / p["entry"] * 100,
                       "net_R": (gross - costs) / p["risk"]})
        position = None

    for row in rows:
        ts = row["timestamp"]
        signal = signals.get(ts - 60)
        exited = False
        if position:
            long = position["side"] == "做多"
            stop, target = position["stop"], position["target"]
            stop_gap = row["open"] <= stop if long else row["open"] >= stop
            target_gap = row["open"] >= target if long else row["open"] <= target
            if stop_gap:
                finish(row["open"], ts, "stop_gap")
                exited = True
            elif target_gap:
                # 开盘已知触发止盈，不能再用该分钟稍后的低/高点反推为先止损。
                finish(target, ts, "target_gap")
                exited = True
        if position and signal:
            wanted = "上涨" if position["side"] == "做多" else "下跌"
            exit_action = "平多" if position["side"] == "做多" else "平空"
            exit_flag = "exit_long" if position["side"] == "做多" else "exit_short"
            if signal.get(exit_flag) or signal.get("advice") == exit_action:
                finish(row["open"], ts, "direction_lost")
                exited = True
        if not position and not exited and signal and signal.get("actionable"):
            side = signal.get("advice")
            if side not in ("做多", "做空"):
                continue
            sign = 1 if side == "做多" else -1
            entry = row["open"] * (1 + sign * slip)
            low, high = signal["entry_low"], signal["entry_high"]
            stop, target = signal["stop"], signal["target"]
            risk, reward = sign * (entry - stop), sign * (target - entry)
            if low <= entry <= high and risk > 0 and reward > 0 and reward / risk >= min_rr - 1e-6:
                position = dict(side=side, entry=entry, entry_t=ts, signal_t=ts - 60,
                                stop=stop, target=target, risk=risk)
        if position:
            long = position["side"] == "做多"
            stop, target = position["stop"], position["target"]
            hit_stop = row["low"] <= stop if long else row["high"] >= stop
            hit_target = row["high"] >= target if long else row["low"] <= target
            if hit_stop:
                px = min(row["open"], stop) if long else max(row["open"], stop)
                finish(px, ts, "stop")
            elif hit_target:
                finish(target, ts, "target")
    if position:
        finish(rows[-1]["close"], rows[-1]["timestamp"] + 60, "session_close")
    return trades


def summarize(trades):
    rs = [t["net_R"] for t in trades]
    positive = sum(r for r in rs if r > 0)
    negative = -sum(r for r in rs if r < 0)
    reasons = defaultdict(int)
    by_symbol = defaultdict(list)
    for trade in trades:
        reasons[trade["reason"]] += 1
        by_symbol[trade.get("symbol", "TEST")].append(trade["net_R"])
    return dict(trades=len(rs), win_rate_pct=round(sum(r > 0 for r in rs) / len(rs) * 100, 2) if rs else None,
                mean_net_R=round(sum(rs) / len(rs), 4) if rs else None,
                total_net_R=round(sum(rs), 4),
                mean_net_pct=round(sum(t["net_pct"] for t in trades) / len(rs), 4) if rs else None,
                profit_factor=round(positive / negative, 3) if negative else None,
                exits=dict(reasons), by_symbol={s: dict(trades=len(values), mean_net_R=round(sum(values) / len(values), 4))
                                               for s, values in by_symbol.items()})


def load_days():
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM bars WHERE source='Massive historical aggregates' "
                            "AND session='regular' ORDER BY symbol,timestamp").fetchall()
    groups = defaultdict(list)
    for row in rows:
        groups[(row["symbol"], row["trading_date"])].append(dict(row))
    days = []
    skipped = 0
    for (symbol, day), minutes in sorted(groups.items()):
        start_dt = datetime.fromisoformat(day).replace(hour=9, minute=30, tzinfo=NY)
        start = int(start_dt.timestamp())
        if {r["timestamp"] for r in minutes} != set(range(start, start + 23400, 60)):
            skipped += 1
            continue
        bars = []
        for i in range(0, 390, 15):
            group = minutes[i:i + 15]
            dt = datetime.fromtimestamp(group[0]["timestamp"], NY)
            bars.append(dict(t=group[0]["timestamp"], dt_ny=dt, dt_bj=dt.astimezone(BJ),
                             o=group[0]["open"], c=group[-1]["close"],
                             h=max(r["high"] for r in group), l=min(r["low"] for r in group),
                             v=sum(float(r["volume"] or 0) for r in group)))
        days.append(dict(symbol=symbol, day=day, minutes=minutes, bars=bars))
    return days, skipped


def prepare_signals(days, baseline):
    cache = []
    prior_daily, prior_bars = [], []
    last_symbol = None
    for record in days:
        symbol, bars = record["symbol"], record["bars"]
        if symbol != last_symbol:
            prior_daily, prior_bars = [], []
            last_symbol = symbol
        atr = atr14(prior_daily)
        signals = {}
        if atr and len(prior_daily) >= 25:
            for i in range(4, len(bars)):
                sub = bars[:i + 1]
                volume = sum(b["v"] for b in sub)
                if not volume or any(b["v"] <= 0 for b in sub):
                    continue
                ts = sub[-1]["t"] + 900
                context = confirm_trend(prior_daily, prior_bars[-156:] + sub, datetime.fromtimestamp(ts, NY))
                args = dict(px=sub[-1]["c"], atr=atr, orb_l=min(b["l"] for b in sub[:2]),
                            orb_h=max(b["h"] for b in sub[:2]),
                            vwap=sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in sub) / volume,
                            day_o=sub[0]["o"], path_l=min(b["l"] for b in sub),
                            path_h=max(b["h"] for b in sub), bars=sub)
                signals[ts] = dict(before=baseline.compute_intraday_signal(**args),
                                   after=compute_intraday_signal(**args, trend_context=context), context=context,
                                   inputs=args)
            cache.append({**record, "signals": signals})
        prior_daily.append(dict(t=bars[0]["t"], dt_ny=bars[0]["dt_ny"], o=bars[0]["o"], c=bars[-1]["c"],
                                h=max(b["h"] for b in bars), l=min(b["l"] for b in bars)))
        prior_bars = (prior_bars + bars)[-156:]
    return cache


def variant_signal(observation, candidate):
    if candidate["name"] == "original":
        return observation["before"]
    sig = dict(observation["after"])
    context = observation["context"]
    if abs(context.get("hourly_score", 0)) < candidate["hour_score"]:
        if context.get("bias") != "unknown":
            sig.update(trend="震荡", exit_long=True, exit_short=True)
        sig.update(actionable=False)
        if sig.get("advice") in ("做多", "做空"):
            sig["advice"] = "观望"
        return sig
    if sig.get("advice") in ("做多", "做空") and abs((sig.get("short_trend") or {}).get("score", 0)) < candidate["entry_score"]:
        sig.update(advice="观望", actionable=False)
    if sig.get("advice") in ("做多", "做空"):
        sign = 1 if sig["advice"] == "做多" else -1
        needed = sig["ref_price"] + sign * candidate["rr"] * abs(sig["ref_price"] - sig["stop"])
        sig["target"] = max(sig["target"], needed) if sign == 1 else min(sig["target"], needed)
    return sig


def evaluate(cache, candidate, *, fee_bps=1, slip_bps=2):
    results = []
    for record in cache:
        signals = {t: variant_signal(obs, candidate) for t, obs in record["signals"].items()}
        trades = replay_minutes(record["minutes"], signals, fee_bps=fee_bps,
                                slip_bps=slip_bps, min_rr=candidate["rr"])
        for trade in trades:
            results.append({**trade, "symbol": record["symbol"], "day": record["day"]})
    return results


def split(trades):
    return {"train": [t for t in trades if t["day"] < "2026-09-01"],
            "validation": [t for t in trades if "2026-09-01" <= t["day"] < "2026-10-01"],
            "review": [t for t in trades if t["day"] >= "2026-10-01"]}


def run(cache):
    candidates = [dict(name="original", hour_score=0, entry_score=0, rr=1.5)]
    for hour, entry, rr in product((2, 3), (2, 3), (1.5, 2.0)):
        candidates.append(dict(name=f"h{hour}_m{entry}_r{rr:g}", hour_score=hour, entry_score=entry, rr=rr))
    reports = {}
    details = {}
    for candidate in candidates:
        trades = evaluate(cache, candidate)
        details[candidate["name"]] = trades
        reports[candidate["name"]] = dict(parameters=candidate, all=summarize(trades),
                                         **{period: summarize(values) for period, values in split(trades).items()})
    default_name = "h2_m2_r1.5"
    eligible = [r for name, r in reports.items() if name != "original" and r["train"]["trades"] >= 30]
    winner = max(eligible, key=lambda r: r["train"]["mean_net_R"]) if eligible else reports[default_name]
    selected = winner["parameters"]["name"]
    stress = {period: summarize(values) for period, values in split(evaluate(cache, winner["parameters"], fee_bps=2, slip_bps=4)).items()}
    default_stress = {period: summarize(values) for period, values in split(evaluate(cache, reports[default_name]["parameters"], fee_bps=2, slip_bps=4)).items()}
    default = reports[default_name]
    validation_ok = (winner["validation"]["trades"] >= 10 and
                     (winner["validation"]["mean_net_R"] or -999) > max(0, default["validation"]["mean_net_R"] or 0) and
                     (stress["validation"]["mean_net_R"] or -999) > 0)
    review_ok = (winner["review"]["trades"] >= 10 and
                 (winner["review"]["mean_net_R"] or -999) > max(0, default["review"]["mean_net_R"] or 0) and
                 (stress["review"]["mean_net_R"] or -999) > 0)
    return dict(variants=reports, training_winner=selected, cost_stress=stress, default_cost_stress=default_stress,
                promote=bool(selected != default_name and validation_ok and review_ok),
                validation_passed=validation_ok, review_passed=review_ok,
                trades={name: details[name] for name in {"original", default_name, selected}},
                selection_rule="Only training data chooses among 8 preset variants, min 30 training trades. Promotion requires >=10 validation and review trades, positive mean R beating default in both periods, and positive doubled-cost mean R in both. No reselection after validation.")


def write_html_report(report):
    def number(value, suffix=""):
        return "—" if value is None else f"{value:.3f}{suffix}"

    rows = []
    for name, label in (("original", "旧策略"), ("h2_m2_r1.5", "当前趋势策略"),
                        (report["training_winner"], "训练期选出候选")):
        data = report["variants"][name]
        for period, title in (("train", "9月前：调参期"), ("validation", "9月：验证期"),
                              ("review", "10月：后续复核"), ("all", "全部历史")):
            stat = data[period]
            rows.append(f"<tr><td>{label}</td><td>{title}</td><td>{stat['trades']}</td>"
                        f"<td>{number(stat['mean_net_R'])}R</td><td>{number(stat['profit_factor'])}</td>"
                        f"<td>{number(stat['win_rate_pct'], '%')}</td></tr>")
    variants = []
    for name, data in report["variants"].items():
        if name == "original":
            continue
        params = data["parameters"]
        label = f"1h≥{params['hour_score']} / 15m≥{params['entry_score']} / {params['rr']:g}R"
        variants.append(f"<tr><td>{label}</td><td>{data['train']['trades']}</td>"
                        f"<td>{number(data['train']['mean_net_R'])}</td>"
                        f"<td>{number(data['validation']['mean_net_R'])}</td>"
                        f"<td>{number(data['review']['mean_net_R'])}</td></tr>")
    reasons = dict(stop="结构止损", stop_gap="跳空止损", target="止盈", target_gap="跳空止盈",
                   direction_lost="总体方向失效", session_close="收盘退出")
    trades = []
    for trade in sorted(report["trades"]["h2_m2_r1.5"], key=lambda t: t["entry_t"]):
        entry = datetime.fromtimestamp(trade["entry_t"], BJ).strftime("%m-%d %H:%M")
        exit_at = datetime.fromtimestamp(trade["exit_t"], BJ).strftime("%m-%d %H:%M")
        trades.append(f"<tr><td>{escape(trade['symbol'])}</td><td>{trade['side']}</td>"
                      f"<td>{entry}</td><td>{number(trade['entry'])}</td>"
                      f"<td>{exit_at}</td><td>{number(trade['exit'])}</td>"
                      f"<td>{reasons[trade['reason']]}</td><td>{number(trade['net_R'])}R</td></tr>")
    stress_rows = []
    for label, data in (("当前策略", report["default_cost_stress"]), ("训练候选", report["cost_stress"])):
        stress_rows.append(f"<tr><td>{label}</td><td>{number(data['train']['mean_net_R'])}</td>"
                           f"<td>{number(data['validation']['mean_net_R'])}</td>"
                           f"<td>{number(data['review']['mean_net_R'])}</td></tr>")
    decision = "候选通过时间分段复核，可审阅参数变更" if report["promote"] else "保留当前 1.5R 参数：训练候选未通过后续验证"
    winner = report["variants"][report["training_winner"]]["parameters"]
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>历史成交回放</title>
<link rel="stylesheet" href="/static/styles.css?v=22">
<style>.report{{max-width:1120px}}h1{{font-size:2rem}}h2{{margin-top:2rem}}table{{width:100%;border-collapse:collapse;background:var(--card)}}th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line);white-space:nowrap}}.scroll{{overflow:auto}}p{{line-height:1.7}}.decision{{background:var(--wait-bg);padding:16px;border-radius:8px}}summary{{cursor:pointer;padding:12px 0}}a{{color:var(--accent)}}</style>
<main class="page report"><a href="/?only=0">← 美股看板</a> · <a href="/diagnostics">入场、止损、退出逐笔诊断</a><h1>历史成交回放</h1>
<p>历史分钟行情模拟成交 · SNDK / SKHY / MU / NVDA · {report['stock_days']} 个股交易日<br>生成于 {escape(report['generated_bj'])}（北京时间）</p>
<p class="decision">{decision}。候选为 1h 强度≥{winner['hour_score']}、15m 强度≥{winner['entry_score']}、目标≥{winner['rr']:g}R。</p>
<p>日线与 1 小时确认总体方向，15 分钟只找同向入场。已收盘信号后等待 1 分钟，开盘价仍在入场带且实际盈亏比足够才成交；已知开盘先处理跳空，分钟内先后不明时止损优先，收盘全部退出。新策略另按引擎的总体方向失效标志退出，旧策略保留原退出规则。</p>
<p>每边假设手续费 1 个基点、滑点 2 个基点，另做成本加倍压力检查。1R 为该笔初始止损风险；下表是逐笔成本后统计，不能当作账户收益。未计欧易资金费、借券费、汇率等。</p>
<h2>新旧策略与时间分段</h2><div class="scroll"><table><thead><tr><th>策略</th><th>时间段</th><th>模拟成交</th><th>平均每笔</th><th>盈利因子（R）</th><th>胜率</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>8 组小范围微调</h2><p>仅用调参期选择候选，至少 30 笔训练成交；验证期与后续复核各需至少 10 笔，平均 R 为正且优于当前参数，成本加倍后也须为正。不会用后续表现重新挑赢家。</p>
<div class="scroll"><table><thead><tr><th>参数</th><th>训练成交</th><th>训练平均 R</th><th>9月平均 R</th><th>10月平均 R</th></tr></thead><tbody>{''.join(variants)}</tbody></table></div>
<h2>成本加倍压力检查</h2><p>每边手续费 2 个基点、滑点 4 个基点后重新模拟成交，因此笔数可能减少。当前策略在 9 月转为负平均 R，说明优势对成本敏感，尚不能认定稳健。</p><div class="scroll"><table><thead><tr><th>方案</th><th>训练平均 R</th><th>9月平均 R</th><th>10月平均 R</th></tr></thead><tbody>{''.join(stress_rows)}</tbody></table></div>
<details><summary>展开当前策略的 {len(trades)} 笔模拟成交（北京时间）</summary><div class="scroll"><table><thead><tr><th>标的</th><th>方向</th><th>入场时间</th><th>入场价</th><th>退出时间</th><th>退出价</th><th>原因</th><th>成本后结果</th></tr></thead><tbody>{''.join(trades)}</tbody></table></div></details>
<p>仅为已反复观察的 4 只股票历史研究；时间分段并不等于全新样本外验证。分钟 OHLCV 不能证明真实可成交价，也不是欧易账户或合约成交记录。不能推广到全部 82 只个股或保证未来收益。</p></main></html>'''
    (ROOT / "cache/trend-trade-report.html").write_text(html, encoding="utf-8")


def main():
    source = baseline_source()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "baseline.py"
        path.write_text(source)
        spec = importlib.util.spec_from_file_location("baseline", path)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        days, skipped = load_days()
        cache = prepare_signals(days, baseline)
        result = run(cache)
    result.update(generated_bj=datetime.now(BJ).isoformat(), stock_days=len(cache), skipped_days=skipped,
                  baseline_commit=BASELINE_COMMIT,
                  execution="15m closed-bar signals; wait 1 minute; next open + adverse slippage; entry must remain in band and >= planned RR. Intraminute stop/target, same minute stop first; adverse gaps; session forced flat. Baseline exits only on explicit engine exit/stop/target/close; new policy also obeys explicit engine exit_long/exit_short on daily+1h direction loss, after same delay. One concurrent position per symbol.",
                  costs="Base per side: fee 1bp + slippage 2bp; stress doubles both. Assumptions, not actual OKX fees. No funding, financing, borrowing or FX costs.",
                  limitations="Historical stock minute OHLCV simulated fills, not account executions or contract quotes. Previously observed 4-stock sample; chronological separation is not a pristine holdout. Net R is trade-level, not portfolio return; no account risk sizing or leverage model.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    write_html_report(result)
    print(json.dumps({k: v for k, v in result.items() if k != "trades"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
