#!/usr/bin/env python3
"""比较收盘信号同一批样本的3/5交易日观察结果；不是实盘成交回测。"""
from collections import Counter
import gzip
import json
import statistics
from daily_strategy import entry_signal
from forward_study import horizon_observation
from large_evaluate import market_above_ma20

PERIODS = {'train': ('2023-01-01', '2024-12-31'), 'validation': ('2025-01-01', '2025-12-31'),
           'test': ('2026-01-01', '2026-10-27')}
RULES = ('trend', 'reclaim', 'breakout')


def describe(values):
    return {'count': len(values), 'wins': sum(v > 0 for v in values),
            'positive_pct': round(sum(v > 0 for v in values) * 100 / len(values), 2) if values else None,
            'mean_pct': round(statistics.mean(values), 3) if values else None,
            'median_pct': round(statistics.median(values), 3) if values else None}


def paired_summary(events, start, end):
    pairs, rejected = [], Counter()
    for e in events:
        if not start <= e['signal_date'] <= end:
            continue
        first, second = e['3'], e['5']
        if first['status'] != '观察值' or second['status'] != '观察值':
            rejected[f"3日:{first['status']};5日:{second['status']}"] += 1
            continue
        if first['date'] > end or second['date'] > end:
            rejected['观察期跨界'] += 1
            continue
        pairs.append(e)
    return {'paired_count': len(pairs), 'day3': describe([e['3']['return_pct'] for e in pairs]),
            'day5': describe([e['5']['return_pct'] for e in pairs]), 'excluded': dict(rejected)}


def choose_rule(results, minimum=30):
    candidates = [(row['train'][h]['positive_pct'], rule, h)
                  for rule, row in results.items() if row['train']['paired_count'] >= minimum
                  for h in ('day3', 'day5') if row['train'][h]['positive_pct'] is not None]
    if not candidates:
        return None
    _, rule, horizon = max(candidates)
    return rule, horizon


def main():
    with open('markets/a/large-universe.json', encoding='utf-8') as f:
        universe = json.load(f)
    with gzip.open('markets/a/hot-history.json.gz', 'rt', encoding='utf-8') as f:
        histories = json.load(f)
    with open('markets/a/hot-backtest.json', encoding='utf-8') as f:
        hot = json.load(f)
    with open('markets/a/benchmark-results.json', encoding='utf-8') as f:
        benchmark = json.load(f)
    calendar = [x['date'] for x in benchmark['bars']]
    next_date = dict(zip(calendar, calendar[1:]))
    market_ok = market_above_ma20(benchmark['bars'])
    by_rule = {rule: [] for rule in RULES}
    rejected_entries = Counter()
    for stock in universe['included']:
        code = stock['code']
        bars = histories[code]
        dates = {x['date']: x for x in bars}
        board = hot['metadata'][code]['board']
        ratio = .3 if board == '北交所' else .2 if board in ('创业板', '科创板') else .1
        for i in range(20, len(bars)):
            signal = bars[i]
            day = signal['date']
            if day not in market_ok:
                continue
            rules = [r for r in RULES if entry_signal(bars[i-20:i+1], r)]
            if not rules:
                continue
            next_day = next_date.get(day)
            if not next_day or next_day not in dates:
                rejected_entries['次日行情不可用'] += 1
                continue
            nxt = dates[next_day]
            op, prev = float(nxt['open']), float(signal['close'])
            if op <= 0 or prev <= 0 or nxt['volume'] <= 0 or abs(op / prev - 1) >= .09 or op >= prev * (1 + ratio) * .995:
                rejected_entries['跳空或估算涨停等无法模拟买入'] += 1
                continue
            event = {'code': code, 'name': stock['name'], 'signal_date': day, 'entry_date': next_day,
                     'entry_price': op,
                     **{str(n): horizon_observation(bars, calendar, next_day, op, n, ratio) for n in (3, 5)}}
            for rule in rules:
                by_rule[rule].append(event)
    results = {rule: {period: paired_summary(events, *dates) for period, dates in PERIODS.items()}
               for rule, events in by_rule.items()}
    choice = choose_rule(results)
    report = {'universe_snapshot': universe['generated_at'], 'universe_size': len(universe['included']),
              'definitions': {'candidate': '当日收盘产生技术信号且沪深300收盘高于MA20；次一市场交易日开盘模拟入场',
                              'accuracy': '扣除假设单边0.15%成本后的观察收益大于零的比例',
                              'paired': '同一信号必须同时拥有3日和5日可观察价格且两者都在该时间段内',
                              'selection': '仅用2023–2024探索期选择正收益占比最高的规则与持有天数，至少30条配对观察'},
              'periods': PERIODS, 'results': results, 'selected_from_train': choice,
              'rejected_entries': dict(rejected_entries), 'events_by_rule': by_rule,
              'limitations': ['当前热门股和当前市值筛选历史，存在前视及幸存者偏差',
                              '未复权价格，不包含完整历史ST状态、除权、停牌及真实涨跌停',
                              '不可观察或接近估算跌停的结果被排除，可能高估正收益占比',
                              '事件和股票高度相关，不是独立样本；不同规则的信号样本也不相同',
                              '这些时段和候选规则已经过本会话观察，不能作为真正独立的样本外验证',
                              '信号记录允许同股重叠，观察价不保证成交，非组合收益']}
    with open('markets/a/paired-evaluation.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
