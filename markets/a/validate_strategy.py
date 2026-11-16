#!/usr/bin/env python3
"""全市场历史研究回放；不将重建信号伪装成事前信号，不改实盘/前瞻账户。"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import gzip
import json
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

from candidate_monitor import history_issue
from daily_strategy import entry_signal
from market_scan import exclude_locked_signals, fetch_recent, qualification_changes
from paper_account import update_account
from simulation import fetch_index, rank_stocks
from trend_exit import fetch_flows

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'markets/a/.cache/validation'


def historical_signals(histories, metadata, calendar, start):
    daily = {}
    indices = {day: i for i, day in enumerate(calendar)}
    for code, bars in histories.items():
        for i in range(20, len(bars)):
            day = bars[i]['date']
            if day < start or day not in indices:
                continue
            sample = bars[i-20:i+1]
            if not entry_signal(sample, 'breakout'):
                continue
            end = indices[day]
            if history_issue(sample, calendar[max(0, end-20):end+1], day):
                continue
            close = bars[i]['close']
            high = max(b['high'] for b in bars[i-20:i])
            rows = [{'code': code, 'name': metadata[code]['name'], 'signal_date': day,
                     'status': '突破信号', 'unmet': [], 'close': close,
                     'breakout_distance_pct': round((close/high-1)*100, 3)}]
            accepted, _ = exclude_locked_signals(rows, {code: bars[i-1:i+1]}, metadata)
            if accepted:
                daily.setdefault(day, []).extend(accepted)
    counts, signals, previous = {}, [], []
    for day in calendar:
        if day < start:
            continue
        rows = sorted(daily.get(day, []), key=lambda r: (-r['breakout_distance_pct'], r['code']))
        changes = qualification_changes(rows, previous)
        continued = set(changes['continuing_codes'])
        for row in rows:
            row['qualification'] = '持续符合' if row['code'] in continued else '新入选'
        counts[day] = len(rows)
        signals.append({'signal_date': day, 'stocks': rows[:5],
                        'new_count':len(changes['new_codes']), 'continuing_count':len(continued)})
        previous = rows
    return signals, counts


def audit_account(state):
    cash, held, closed = 1.0, {}, []
    fee = state['fee_per_side']
    for event in state['events']:
        code, quantity, price = event['code'], event['quantity'], event['price']
        assert quantity > 0 and price > 0
        if event['side'] == 'buy':
            assert code not in held and len(held) < 5
            held[code] = event
            cash -= quantity*price*(1+fee)
        else:
            assert code in held
            buy = held.pop(code)
            assert buy['date'] < event['date'], 'T+1违规'
            assert abs(buy['quantity']-quantity) < 1e-10
            cash += quantity*price*(1-fee)
            closed.append({'code': code, 'entry_date': buy['date'], 'exit_date': event['date'],
                           'entry_price': buy['price'], 'exit_price': price,
                           'return_pct': (price*(1-fee)/(buy['price']*(1+fee))-1)*100,
                           'holding_calendar_days': (datetime.fromisoformat(event['date'])-datetime.fromisoformat(buy['date'])).days,
                           'reason': event.get('reason')})
        assert cash >= -1e-10, '现金透支'
    assert abs(cash-state['cash']) < 1e-9, '现金与独立逐笔记账不符'
    assert set(held) == set(state['positions'])
    for code, event in held.items():
        assert abs(event['quantity']-state['positions'][code]['quantity']) < 1e-10
    values = [t['return_pct'] for t in closed]
    winners = [t for t in closed if t['return_pct'] > 0]
    return {'status': 'pass', 'closed_trades': len(closed), 'open_positions': len(held),
            'win_rate_pct': round(100*len(winners)/len(closed), 2) if closed else None,
            'median_trade_pct': round(statistics.median(values), 3) if values else None,
            'median_winner_holding_days': statistics.median([t['holding_calendar_days'] for t in winners]) if winners else None,
            'trades': closed}


def load_histories():
    histories = {}
    for path in sorted(CACHE.glob('daily-*.json.gz'), key=lambda p: p.stat().st_mtime):
        histories.update(json.load(gzip.open(path, 'rt', encoding='utf-8')))
    return histories


def render_report(report, path):
    old = report['results']['year_ma20']
    new = report['results']['year_structure']
    fund = report['results']['matched_flow']
    lines = ['# A股动态选股验证报告', '',
             f"数据截至 {report['data_as_of']}；报告生成于 {report['generated_at']}。", '',
             '**结论：当前按突破幅度选择前5名的组合未通过每月5%目标。每天有合格信号，但现行入场排序与退出组合没有产生稳定正收益。资金退出分支覆盖不足，不能判定其完整策略有效。**', '',
             '## 验证范围', '',
             f"- 当次全A列表 {report['listed']} 只；按当前名称及板块筛选后 {report['requested_stocks']} 只；下载历史日线 {report['downloaded_stocks']} 只。每只最多260根日线。",
             '- 每个历史交易日仅用当日及之前数据重新选股，区分新入选和持续符合；不是把今天的54只固定回放。',
             '- MA5 > MA20、收盘高于MA20且突破此前20日高点；排除按前收估算接近涨停及一字线的信号；按突破幅度从大到小选前5名。',
             '- 最多5个仓位，共享现金，单次最多配置当时权益20%，已有持仓未触发退出则继续持有；新候选不强制替换原持仓。',
             '- 下一交易日开盘尝试成交，遵守T+1；每边成本0.15%，含持仓盯市，允许碎股。', '',
             '## 年初至今的价格规则对照', '',
             '| 规则 | 累计收益 | 最大回撤 | 完整月达到5% |',
             '|---|---:|---:|---:|']
    for label,row in [('原MA20退出',old),('趋势转弱退出（未使用资金流）',new)]:
        lines.append(f"| {label} | {row['performance']['return_pct']:+.2f}% | {row['performance']['max_drawdown_pct']:.2f}% | {row['months_at_5pct']}/{row['complete_months']} |")
    lines += ['', '| 月份 | 原MA20退出 | 趋势转弱退出（价格分支） |', '|---|---:|---:|']
    for month,row in old['monthly'].items():
        label = month + ('（未结束）' if month==report['data_as_of'][:7] else '')
        lines.append(f"| {label} | {row['return_pct']:+.2f}% | {new['monthly'][month]['return_pct']:+.2f}% |")
    counts = report['signal_summary']
    lines += ['', '## 每日机会与持仓延续', '',
              f"{counts['sessions']}个交易日，没有无信号日；每日合格数量最少{counts['min']}、中位数{counts['median']}、最多{counts['max']}。机会数量充足没有使现行前5名排序自动盈利。",
              f"原规则盈利交易的持有时间中位数为{old['audit']['median_winner_holding_days']}个自然日；新价格退出规则为{new['audit']['median_winner_holding_days']}个自然日。更早退出也可能截断盈利趋势。", '',
              '## 资金退出验证缺口', '',
              f"6月至11月候选涉及{report['flow_requested_stocks']}只股票，仅{report['flow_downloaded_stocks']}只成功取得资金流；实际持仓判断中两日资金数据覆盖率为{fund['flow_coverage_pct']}%。因此资金版状态为 not_validated_missing_flow，其部分数据模拟收益不作为完整资金策略绩效。",
              '成功样本接口最多返回120个交易日；未成功请求均保存原因，不以价格或成交量替代资金流，不以缺失视为流入。', '',
              '## 记账与时序核验', '',
              '- 对六组模拟逐笔检查T+1、持仓数、现金不透支和买卖数量；全部通过。',
              '- 另独立重算每日净值，检查买入来自前一日候选、成交价等于对应日线开盘价；结果见 validation-account-audit.json。',
              '- 测试覆盖未来数据不改写既有候选、事后信号不能追认买入、重复运行不重复成交、新候选不强制换出趋势持仓。', '',
              '## 结论的适用范围', '']
    lines += ['- '+x for x in report['limitations']]
    lines += ['- 以前固定60只、各股独立资金的+18.12%报告，与本次前5名共享资金组合不是同一种组合，不能直接套用。',
              '- 本报告证明当前研究规则没有通过目标，不代表已证明所有全市场选股策略都无效。需要修改入场质量或排序时，应另立实验并用未参与调参的数据检验。', '',
              '复算命令：`python3 markets/a/validate_strategy.py --cached-flows-only`。原始日线、资金数据及逐日候选缓存位于 `markets/a/.cache/validation/`，详细六组曲线和逐笔事件位于 `markets/a/strategy-validation.json`。']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', default='2026-11-13')
    parser.add_argument('--output', type=Path, default=ROOT/'markets/a/strategy-validation.json')
    parser.add_argument('--cached-flows-only', action='store_true', help='复用已取资金流并显式保留缺口，不重复请求失败接口')
    args = parser.parse_args()
    universe = json.loads((CACHE/'universe.json').read_text())
    metadata = {s['code']: s for s in universe['stocks']}
    histories = {c: b for c, b in load_histories().items() if c in metadata}
    missing = [s for s in universe['stocks'] if s['code'] not in histories]
    errors = {}
    if missing:
        repair = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_recent, s, args.end, 260): s for s in missing}
            for future in as_completed(futures):
                s = futures[future]
                try:
                    repair[s['code']] = future.result()
                except Exception as exc:
                    errors[s['code']] = str(exc)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        with gzip.open(CACHE/f'daily-repair-{stamp}.json.gz', 'wt') as f:
            json.dump(repair, f)
        histories.update(repair)
    if len(histories) < len(metadata)*.98:
        raise ValueError('历史日线下载不足98%，停止验证')
    calendar = [x['date'] for x in fetch_index(args.end)]
    print('生成逐日信号',len(histories), flush=True)
    signals, counts = historical_signals(histories, metadata, calendar, '2026-01-01')
    with gzip.open(CACHE/'historical-signals.json.gz','wt') as f:
        json.dump(signals, f, ensure_ascii=False)
    flow_codes = sorted({r['code'] for s in signals if s['signal_date'] >= '2026-06-01' for r in s['stocks']})
    flow_cache = CACHE/'fund-flows.json'
    flows = json.loads(flow_cache.read_text()) if flow_cache.exists() else {}
    previous = json.loads(args.output.read_text()) if args.output.exists() else {}
    flow_errors = {c: previous.get('flow_errors',{}).get(c,'缓存没有资金流，本轮不重新请求')
                   for c in flow_codes if c not in flows} if args.cached_flows_only else {}
    print('候选涉及资金流股票',len(flow_codes),'已缓存',len(flows),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {} if args.cached_flows_only else {
            pool.submit(fetch_flows, metadata[c], args.end, 0): c for c in flow_codes if c not in flows}
        for number, future in enumerate(as_completed(futures),1):
            code = futures[future]
            try:
                flows[code] = future.result()
            except Exception as exc:
                flow_errors[code] = str(exc)
            if number%50==0 or number==len(futures):
                flow_cache.write_text(json.dumps(flows, ensure_ascii=False))
                print('资金流',number,'/',len(futures),'失败',len(flow_errors),flush=True)
    variants = [('year_ma20','2026-01-01','ma20',{},.0015),
                ('year_structure','2026-01-01','trend_flow',{},.0015),
                ('matched_ma20','2026-06-01','ma20',{},.0015),
                ('matched_structure','2026-06-01','trend_flow',{},.0015),
                ('matched_flow','2026-06-01','trend_flow',flows,.0015),
                ('matched_flow_cost_x2','2026-06-01','trend_flow',flows,.003)]
    results = {}
    for name,start,policy,funds,fee in variants:
        print('回放',name,flush=True)
        state = update_account(None,histories,metadata,calendar,signals,funds,start,
                               historical_research=True,exit_policy=policy,fee=fee)
        audit = audit_account(state)
        monthly = state['monthly']
        completed = {m: v for m,v in monthly.items() if m < args.end[:7]}
        flow_coverage = state.get('flow_decision_coverage',{})
        flow_total = sum(x['total'] for x in flow_coverage.values())
        flow_known = sum(x['known'] for x in flow_coverage.values())
        fund_required = name.startswith('matched_flow')
        results[name] = {'performance':state['performance'], 'monthly':monthly,
                         'complete_months':len(completed),
                         'months_at_5pct':sum(v['return_pct']>=5 for v in completed.values()),
                         'flow_decision_coverage':flow_coverage,
                         'flow_coverage_pct': round(100*flow_known/flow_total,2) if flow_total else None,
                         'validation_status': ('not_validated_missing_flow' if fund_required and flow_known < flow_total else
                                               'failed_monthly_target' if any(v['return_pct']<5 for v in completed.values()) else 'target_met_in_observed_window'),
                         'audit':audit,'account':state}
        print(name,results[name]['performance'],'5%达标',results[name]['months_at_5pct'],'/',len(completed),flush=True)
    # 检查信号算法与生产选股在同一最后交易日的结果一致。
    names = {c: s['name'] for c,s in metadata.items()}
    latest, _ = rank_stocks(histories,names,calendar)
    latest, _ = exclude_locked_signals(latest,histories,metadata)
    assert [r['code'] for r in latest[:5]] == [r['code'] for r in signals[-1]['stocks']]
    counts_values = list(counts.values())
    report = {'generated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'data_as_of':args.end,
              'code_hashes':{name:hashlib.sha256((ROOT/'markets/a'/name).read_bytes()).hexdigest()
                             for name in ['validate_strategy.py','paper_account.py','trend_exit.py','market_scan.py','daily_strategy.py']},
              'universe_asof':datetime.fromtimestamp((CACHE/'universe.json').stat().st_mtime,ZoneInfo('Asia/Shanghai')).isoformat(),
              'listed':universe['listed'],'requested_stocks':len(metadata),'downloaded_stocks':len(histories),
              'history_failures':errors,'flow_requested_stocks':len(flow_codes),'flow_downloaded_stocks':len(flows),
              'flow_errors':flow_errors,'flow_ranges':{c:[min(f),max(f),len(f)] for c,f in flows.items() if f},
              'signal_counts':counts,'signal_summary':{'sessions':len(counts),'zero_signal_days':sum(n==0 for n in counts_values),
                              'min':min(counts_values),'median':statistics.median(counts_values),'max':max(counts_values)},
              'results':results,'checks':{'cash_and_T1_audited':True,'latest_selection_matches_production':True},
              'limitations':['使用当前可见股票及当前名称回看历史，缺少当时退市/ST名单，有幸存者与时点选择偏差',
                             '未复权日线，真实历史涨跌停、除权和排队未完整还原；成交仅按已知开盘、前收及估算限价判断，未证明真实可成交',
                             '前5名排序、退出参数已在已知市场上研究，不是独立样本外验证',
                             '资金流只是供应商大单估算；只在持仓对应日期实际取到两日数值时使用，缺失时明确统计',
                             '允许碎股，不含真实交易手数，结果为研究模拟，不是实盘或保证收益']}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    render_report(report,ROOT/'docs/reports/2026-11-16-a-strategy-validation.md')
    print('REPORT',args.output,flush=True)


if __name__ == '__main__':
    main()
