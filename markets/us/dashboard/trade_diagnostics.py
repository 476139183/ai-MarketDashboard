#!/usr/bin/env python3
"""Trade-level entry, structural stop and exit diagnostics. Research only."""
import importlib.util
import json
import statistics
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from backtest_trend_policy import BASELINE_COMMIT, ROOT, baseline_source, load_days, prepare_signals, replay_minutes, summarize, split, variant_signal
from engine import BJ, NY, compute_intraday_signal, _min_gap

DEFAULT = dict(name="h2_m2_r1.5", hour_score=2, entry_score=2, rr=1.5)
MODES = {"current": "当前规则", "before_1400": "14点后不新开仓", "swing_stop": "近3根结构止损，目标不变",
         "swing_target": "近3根结构止损，重新定1.5R目标", "confirm_exit": "方向失效连续2根15m才退出",
         "hard_exit": "仅价格止损/ORB失效/止盈/收盘退出", "minute_trigger": "固定15m计划，分钟收盘确认到价",
         "minute_replan": "已收盘15m定方向，分钟重算入场计划"}
OUT = ROOT / "cache/trade-diagnostics.json"


def transform_signals(record, mode):
    output = {}
    streaks = dict(exit_long=0, exit_short=0)
    previous_t = None
    for ts, obs in sorted(record["signals"].items()):
        sig = dict(variant_signal(obs, DEFAULT))
        if mode in ("confirm_exit", "hard_exit"):
            for flag, invalid, action in (("exit_long", "long_invalid", "平多"), ("exit_short", "short_invalid", "平空")):
                hard = bool(sig.get(invalid) or sig.get("advice") == action)
                if sig.get(flag) and not hard:
                    streaks[flag] = streaks[flag] + 1 if previous_t is not None and ts - previous_t == 900 else 1
                    sig[flag] = mode == "confirm_exit" and streaks[flag] >= 2
                else:
                    streaks[flag] = 0
            previous_t = ts
        if mode == "before_1400" and datetime.fromtimestamp(ts + 60, NY).hour >= 14:
            sig["actionable"] = False
        if mode in ("swing_stop", "swing_target") and sig.get("advice") in ("做多", "做空"):
            args = obs["inputs"]
            atr_i = max(args["atr"] * .35, args["px"] * .008)
            gap = _min_gap(args["px"], atr_i)
            bars = args["bars"][-3:]
            long = sig["advice"] == "做多"
            proposed = min(b["l"] for b in bars) - .1 * atr_i if long else max(b["h"] for b in bars) + .1 * atr_i
            proposed = max(sig["stop"], proposed) if long else min(sig["stop"], proposed)
            valid = proposed <= sig["entry_low"] - gap if long else proposed >= sig["entry_high"] + gap
            if valid:
                sig["stop"] = proposed
                if mode == "swing_target":
                    sign = 1 if long else -1
                    sig["target"] = sig["ref_price"] + sign * 1.5 * abs(sig["ref_price"] - proposed)
                if not (sig["stop"] < sig["entry_low"] <= sig["entry_high"] < sig["target"] if long else
                        sig["target"] < sig["entry_low"] <= sig["entry_high"] < sig["stop"]):
                    sig["actionable"] = False
        output[ts] = sig
    return output


def minute_signals(record, *, replan=False):
    """Only entry checks become more frequent; exit observations remain at 15m close."""
    output = {}
    observations = sorted(record["signals"].items())
    index = -1
    path_h, path_l = float("-inf"), float("inf")
    for row in record["minutes"]:
        path_h, path_l = max(path_h, row["high"]), min(path_l, row["low"])
        end = row["timestamp"] + 60
        while index + 1 < len(observations) and observations[index + 1][0] <= end:
            index += 1
        if index < 0:
            continue
        observed_at, obs = observations[index]
        args = {**obs["inputs"], "px": row["close"], "path_h": path_h, "path_l": path_l}
        if replan:
            sig = compute_intraday_signal(**args, trend_context=obs["context"])
        else:
            sig = dict(variant_signal(obs, DEFAULT))
            if sig.get("advice") in ("做多", "做空"):
                px = row["close"]
                atr_i = max(obs["inputs"]["atr"] * .35, obs["inputs"]["px"] * .008)
                chase = max(.25 * atr_i, .004 * obs["inputs"]["px"])
                orb_l, orb_h = obs["inputs"]["orb_l"], obs["inputs"]["orb_h"]
                long = sig["advice"] == "做多"
                valid = (px >= obs["inputs"]["vwap"] and px < path_h - chase and not (path_h > orb_h and px < orb_l)) if long else (
                    px <= obs["inputs"]["vwap"] and px > path_l + chase and not (path_l < orb_l and px > orb_h))
                sig["actionable"] = sig["at_entry"] = bool(valid and sig["entry_low"] <= px <= sig["entry_high"])
        if end != observed_at:
            sig.update(exit_long=False, exit_short=False)
            if sig.get("advice") in ("平多", "平空"):
                sig.update(advice="观望", actionable=False)
        else:
            sig["exit_long"] = obs["after"].get("exit_long", False)
            sig["exit_short"] = obs["after"].get("exit_short", False)
        # Same target precision/minimum convention as the existing replay.
        if replan and sig.get("advice") in ("做多", "做空"):
            sign = 1 if sig["advice"] == "做多" else -1
            needed = sig["ref_price"] + sign * 1.5 * abs(sig["ref_price"] - sig["stop"])
            sig["target"] = max(sig["target"], needed) if sign == 1 else min(sig["target"], needed)
        output[end] = sig
    return output


def diagnose_trade(trade, record):
    entry_t, exit_t = trade["entry_t"], trade["exit_t"]
    sign = 1 if trade["side"] == "做多" else -1
    prior = [m for m in record["minutes"] if entry_t <= m["timestamp"] < exit_t]
    # Exit-minute extrema have unknown ordering; only full earlier minutes and actual exit are counted.
    favourable = [sign * (trade["exit"] - trade["entry"]), 0]
    adverse = list(favourable)
    for row in prior:
        favourable.append(sign * ((row["high"] if sign == 1 else row["low"]) - trade["entry"]))
        adverse.append(sign * ((row["low"] if sign == 1 else row["high"]) - trade["entry"]))
    obs = record["signals"][trade["signal_t"]]
    wanted = "up" if sign == 1 else "down"
    first = next((t for t, o in sorted(record["signals"].items()) if t <= trade["signal_t"] and
                  o["context"].get("confirmed") and o["context"].get("bias") == wanted), None)
    dt = datetime.fromtimestamp(entry_t, NY)
    return {**trade, "entry_ny": dt.isoformat(), "entry_minute": dt.hour * 60 + dt.minute,
            "minutes_remaining": 960 - dt.hour * 60 - dt.minute,
            "hold_minutes": (exit_t - entry_t) / 60,
            "risk_pct": trade["risk"] / trade["entry"] * 100,
            "risk_atr": trade["risk"] / obs["inputs"]["atr"],
            "target_atr": sign * (trade["target"] - trade["entry"]) / obs["inputs"]["atr"],
            "mfe_R_before_exit": max(favourable) / trade["risk"],
            "mae_R_before_exit": min(adverse) / trade["risk"],
            "minutes_from_first_confirmed_direction": (entry_t - first) / 60 if first is not None else None}


def fixed_entry_exit_comparison(trade, record, mode):
    signals = transform_signals(record, mode)
    for sig in signals.values():
        sig["actionable"] = False
    opening = dict(transform_signals(record, "current")[trade["signal_t"]])
    opening["actionable"] = True
    signals[trade["signal_t"]] = opening
    replay = replay_minutes(record["minutes"], signals)
    assert len(replay) == 1 and replay[0]["entry_t"] == trade["entry_t"]
    shadow = replay[0]
    assert abs(shadow["entry"] - trade["entry"]) < 1e-8
    return dict(symbol=trade["symbol"], day=trade["day"], entry_t=trade["entry_t"],
                actual_exit_t=trade["exit_t"], alternative_exit_t=shadow["exit_t"],
                actual_R=trade["net_R"], alternative_R=shadow["net_R"],
                delta_R=shadow["net_R"] - trade["net_R"], alternative_reason=shadow["reason"])


def paired_price_comparisons(trials):
    baseline = {(t["symbol"], t["entry_t"], t["side"]): t for t in trials["current"]}
    report = {}
    for mode in ("swing_stop", "swing_target"):
        other = {(t["symbol"], t["entry_t"], t["side"]): t for t in trials[mode]}
        keys = baseline.keys() & other.keys()
        deltas = [other[k]["net_pct"] - baseline[k]["net_pct"] for k in keys]
        report[mode] = dict(same_entries=len(keys), extra_entries=len(other.keys() - baseline.keys()),
                            missing_entries=len(baseline.keys() - other.keys()),
                            mean_same_entry_price_pct_delta=statistics.mean(deltas) if deltas else None,
                            unchanged_price_results=sum(abs(d) < 1e-8 for d in deltas),
                            changed_stops=sum(abs(other[k]["stop"] - baseline[k]["stop"]) > 1e-8 for k in keys))
    return report


def write_html_report(result):
    def fmt(value, digits=3):
        return "—" if value is None else f"{value:.{digits}f}"

    rows = []
    for mode, data in result["summary"].items():
        rows.append(f"<tr><td>{escape(data['label'])}</td><td>{data['all']['trades']}</td>"
                    f"<td>{fmt(data['all']['mean_net_R'])}</td><td>{fmt(data['all']['mean_net_pct'])}%</td>"
                    f"<td>{fmt(data['train']['mean_net_R'])}</td><td>{fmt(data['validation']['mean_net_R'])}</td>"
                    f"<td>{fmt(data['review']['mean_net_R'])}</td><td>{fmt(data['stress']['validation']['mean_net_R'])}</td></tr>")
    entry_rows = []
    for key, title in (("before_noon", "12点前"), ("noon_to_1400", "12至14点"), ("after_1400", "14点后")):
        data = result["bins"][key]
        entry_rows.append(f"<tr><td>{title}</td><td>{data['trades']}</td><td>{fmt(data['mean_net_R'])}</td>"
                          f"<td>{fmt(data['mean_net_pct'])}%</td></tr>")
    paired_rows = []
    for mode, data in result["paired_price_comparisons"].items():
        paired_rows.append(f"<tr><td>{escape(MODES[mode])}</td><td>{data['same_entries']}</td>"
                           f"<td>{data['extra_entries']}/{data['missing_entries']}</td>"
                           f"<td>{data['unchanged_price_results']}</td><td>{fmt(data['mean_same_entry_price_pct_delta'], 5)} 个百分点</td></tr>")
    exit_rows, cases = [], []
    for mode, values in result["exit_counterfactuals"].items():
        exit_rows.append(f"<tr><td>{escape(MODES[mode])}</td><td>{len(values)}</td>"
                         f"<td>{fmt(statistics.mean(t['actual_R'] for t in values)) if values else '—'}</td>"
                         f"<td>{fmt(statistics.mean(t['alternative_R'] for t in values)) if values else '—'}</td>"
                         f"<td>{sum(t['delta_R'] > 0 for t in values)}/{sum(t['delta_R'] < 0 for t in values)}</td></tr>")
        if mode == "hard_exit":
            for t in values:
                cases.append(f"<tr><td>{escape(t['symbol'])}</td><td>{t['day']}</td><td>{fmt(t['actual_R'])}</td>"
                             f"<td>{fmt(t['alternative_R'])}</td><td>{fmt(t['delta_R'])}</td></tr>")
    details = []
    for t in sorted(result["trades"], key=lambda t: t["entry_t"]):
        entry = datetime.fromtimestamp(t["entry_t"], BJ).strftime("%m-%d %H:%M")
        details.append(f"<tr><td>{escape(t['symbol'])}</td><td>{entry}</td><td>{t['side']}</td>"
                       f"<td>{fmt(t['risk_pct'], 2)}%</td><td>{fmt(t['risk_atr'])}</td>"
                       f"<td>{fmt(t['hold_minutes'], 0)}</td><td>{fmt(t['mfe_R_before_exit'])}</td>"
                       f"<td>{fmt(t['mae_R_before_exit'])}</td><td>{fmt(t['net_R'])}</td></tr>")
    def table(headers, body):
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>逐笔诊断：入场、止损、退出</title><link rel="stylesheet" href="/static/styles.css?v=22">
<style>.report{{max-width:1200px}}h2{{margin-top:2rem}}p,li{{line-height:1.8}}table{{width:100%;border-collapse:collapse;background:var(--card)}}td,th{{padding:10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}.scroll{{overflow:auto}}.note{{background:var(--wait-bg);padding:16px;border-radius:8px}}summary{{cursor:pointer;padding:12px 0}}a{{color:var(--accent)}}</style>
<main class="page report"><a href="/backtest">← 新旧策略回放</a> · <a href="/?only=0">实时看板</a>
<h1>逐笔诊断：入场、止损、退出</h1><p>4只股票 · {result['stock_days']} 个股交易日 · {len(result['trades'])} 笔当前策略成交 · {escape(result['generated_bj'])}（北京时间）</p>
<p class="note">本次是诊断对照，未替换线上策略。所有历史时段都已被观察，不能再视作全新样本外；以下对照不是保证收益的调参结果。成交模型、手续费和滑点与原回放一致，成本加倍时重新判断是否成交。</p>
<h2>1. 入场是否太晚</h2><p>当前入场后剩余常规盘时间中位数为 {fmt(result['median_remaining_minutes'],1)} 分钟。下表按美东时间分组，不能仅因个别尾盘交易就一刀切禁止下午入场；各组股票与行情并不相同。</p>
{table(['入场时间（美东）','笔数','平均净 R','平均价格净收益'],entry_rows)}
<h2>2. 止损和目标是否与持仓时间匹配</h2><p>初始止损距离中位数为股价的 {fmt(result['median_risk_pct'],2)}%，约 {fmt(result['median_risk_atr'])} 倍日线 ATR；目标距离中位数为 {fmt(result['median_target_atr'])} 倍 ATR。缩短止损会改变 R 的分母及通过盈亏比检查的成交样本，不能仅凭平均 R 变大判定改善。</p>
{table(['止损对照','相同入场','新增/缺失入场','价格结果不变','相同入场平均价格净收益差'],paired_rows)}
<h2>3. 方向失效是否退出过早</h2><p>固定原来因方向失效退出的入场、价格和初始风险，比较延迟退出或仅保留硬退出。后验结果只用于解释退出影响，不用未来数据挑选入场。</p>
{table(['退出对照','固定入场数','原平均 R','对照平均 R','改善/恶化笔数'],exit_rows)}
<details><summary>展开保留硬退出的逐笔对照</summary>{table(['标的','交易日','原净 R','对照净 R','变化 R'],cases)}</details>
<h2>4. 规则对照与时间分段</h2><p>同时展示平均净 R 和价格净收益。固定计划的分钟触发仅改变检查到价频率；“分钟重算”还改变入场带和目标，不能把它的结果全部归因为入场更早。所有对照保留价格止损、跳空处理和收盘退出。</p>
{table(['规则','全期笔数','全期平均 R','全期平均价格净收益','9月前 R','9月 R','10月 R','9月双倍成本 R'],rows)}
<details><summary>展开 {len(details)} 笔入场、风险与浮盈浮亏明细（北京时间）</summary><p>浮盈/浮亏只使用退出前完整分钟与实际退出价，排除退出分钟未知先后的极值；这是保守可确认值，不是事先可成交的最佳收益。</p>{table(['标的','入场时间','方向','初始风险','风险/ATR','持有分钟','确认浮盈 R','确认浮亏 R','净 R'],details)}</details>
<p>本地分钟 OHLCV 模拟，不是账户或欧易合约真实成交；未计资金费、借券和汇率成本，未模拟账户仓位与杠杆。新增78只个股没有本次历史验证覆盖。</p></main></html>'''
    (ROOT / "cache/trade-diagnostics-report.html").write_text(html, encoding="utf-8")


def run(cache):
    trials = {mode: [] for mode in MODES}
    stress = {mode: [] for mode in MODES}
    diagnostics, counterfactuals = [], dict(hard_exit=[], confirm_exit=[])
    for n, record in enumerate(cache, 1):
        current = transform_signals(record, "current")
        for mode in MODES:
            signals = minute_signals(record, replan=mode == "minute_replan") if mode.startswith("minute_") else current if mode == "current" else transform_signals(record, mode)
            for destination, fees, slips in ((trials, 1, 2), (stress, 2, 4)):
                trades = replay_minutes(record["minutes"], signals, fee_bps=fees, slip_bps=slips)
                enriched = [{**t, "symbol": record["symbol"], "day": record["day"]} for t in trades]
                destination[mode].extend(enriched)
                if mode == "current" and destination is trials:
                    for trade in enriched:
                        diagnostics.append(diagnose_trade(trade, record))
                        if trade["reason"] == "direction_lost":
                            for exit_mode in counterfactuals:
                                counterfactuals[exit_mode].append(fixed_entry_exit_comparison(trade, record, exit_mode))
        if n % 100 == 0:
            print(f"[diagnosis] {n}/{len(cache)}", flush=True)
    summary = {mode: dict(label=MODES[mode], all=summarize(trades),
                          **{period: summarize(values) for period, values in split(trades).items()},
                          stress={period: summarize(values) for period, values in split(stress[mode]).items()})
               for mode, trades in trials.items()}
    def group(predicate):
        return summarize([t for t in diagnostics if predicate(t)])
    bins = dict(before_noon=group(lambda t: t["entry_minute"] < 720),
                noon_to_1400=group(lambda t: 720 <= t["entry_minute"] < 840),
                after_1400=group(lambda t: t["entry_minute"] >= 840),
                risk_under_2pct=group(lambda t: t["risk_pct"] < 2),
                risk_2_to_4pct=group(lambda t: 2 <= t["risk_pct"] < 4),
                risk_over_4pct=group(lambda t: t["risk_pct"] >= 4))
    return dict(generated_bj=datetime.now(BJ).isoformat(), stock_days=len(cache), summary=summary,
                median_risk_pct=statistics.median(t["risk_pct"] for t in diagnostics),
                median_risk_atr=statistics.median(t["risk_atr"] for t in diagnostics),
                median_target_atr=statistics.median(t["target_atr"] for t in diagnostics),
                median_remaining_minutes=statistics.median(t["minutes_remaining"] for t in diagnostics),
                bins=bins, trades=diagnostics, exit_counterfactuals=counterfactuals,
                trial_trades=trials, paired_price_comparisons=paired_price_comparisons(trials),
                limitations="All periods already observed; diagnostic trials cannot create a fresh holdout. No live policy promotion. Fixed-entry alternatives use known historical entries only for paired exit diagnosis, never as entry rules. Future extrema are retrospective descriptions, not actionable signals. No contract prices, funding, account sizing or leverage.")


def main():
    source = baseline_source()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "baseline.py"
        path.write_text(source)
        spec = importlib.util.spec_from_file_location("baseline", path)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        days, _ = load_days()
        cache = prepare_signals(days, baseline)
        result = run(cache)
    result["baseline_commit"] = BASELINE_COMMIT
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    write_html_report(result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("trades", "trial_trades")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
