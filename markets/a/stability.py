#!/usr/bin/env python3
"""对冻结规则做年度、滚动窗口、分组与集中度敏感性研究。"""
from collections import defaultdict
import hashlib
import json
import random
import statistics
from portfolio import equity_curve, period_metrics

RULE = 'breakout_ma20'  # 上一轮基于探索期选定；此处不重新优化。
YEARS = {'2023': ('2023-01-01', '2023-12-31'), '2024': ('2024-01-01', '2024-12-31'),
         '2025': ('2025-01-01', '2025-12-31'), '2026': ('2026-01-01', '2026-10-27')}


def monthly_bootstrap(trades, iterations=2000):
    groups = defaultdict(list)
    for t in trades:
        groups[t['signal_date'][:7]].append(t['return_pct'])
    months = sorted(groups)
    if len(months) < 2:
        return None
    rng = random.Random(20261028)
    estimates = []
    for _ in range(iterations):
        values = [x for month in rng.choices(months, k=len(months)) for x in groups[month]]
        estimates.append(statistics.mean(values))
    estimates.sort()
    return {'months': len(months), 'iterations': iterations, 'mean_pct': round(statistics.mean(t['return_pct'] for t in trades), 3),
            'p05_pct': round(estimates[int(iterations * .05)], 3), 'p95_pct': round(estimates[int(iterations * .95)], 3)}


def main():
    with open('markets/a/long-backtest.json', encoding='utf-8') as f:
        data = json.load(f)
    with open('markets/a/portfolio-evaluation.json', encoding='utf-8') as f:
        portfolio = json.load(f)
    with open('markets/a/benchmark-results.json', encoding='utf-8') as f:
        benchmark = json.load(f)
    bars, results = data['bars'], data['rules'][RULE]['by_stock']
    curve = portfolio['rules'][RULE]['curve']
    benchmark_curve = [{'date': x['date'], 'equity': x['close'] / benchmark['bars'][0]['close']} for x in benchmark['bars']]
    annual = {year: {'strategy': period_metrics(curve, start, end), 'index': period_metrics(benchmark_curve, start, end)}
              for year, (start, end) in YEARS.items()}
    windows = []
    for i in range(126, len(curve), 21):
        before, after = curve[i-126], curve[i]
        windows.append({'start': before['date'], 'end': after['date'],
                        'return_pct': round((after['equity'] / before['equity'] - 1) * 100, 3)})
    stock_returns = {}
    for code in bars:
        single = equity_curve({code: bars[code]}, {code: results[code]})
        stock_returns[code] = period_metrics(single, *YEARS['2026'])['return_pct']
    # 事后剔除该期贡献最大的股票，仅用于评估集中度。
    top = sorted(stock_returns, key=stock_returns.get, reverse=True)
    sensitivity = {}
    for n in (1, 5, 10):
        kept = {c: b for c, b in bars.items() if c not in set(top[:n])}
        reduced = equity_curve(kept, {c: results[c] for c in kept})
        sensitivity[str(n)] = {'excluded': [{'code': c, 'return_pct': stock_returns[c]} for c in top[:n]],
                               'period_2026': period_metrics(reduced, *YEARS['2026'])}
    groups = {}
    for group in range(5):
        codes = [c for c in bars if int(hashlib.sha256(c.encode()).hexdigest()[:8], 16) % 5 == group]
        sub = equity_curve({c: bars[c] for c in codes}, {c: results[c] for c in codes})
        groups[str(group)] = {'stocks': len(codes), 'period_2026': period_metrics(sub, *YEARS['2026'])}
    costs = {str(fee): period_metrics(equity_curve(bars, results, fee), *YEARS['2026'])
             for fee in (.0005, .0015, .003)}
    trades_2026 = [t for r in results.values() for t in r['trades']
                   if t['signal_date'] >= '2026-01-01' and t['exit_date'] <= '2026-10-27']
    returns = sorted(t['return_pct'] for t in trades_2026)
    trim_count = max(1, int(len(returns) * .05)) if returns else 0
    output = {'rule': RULE, 'annual': annual,
              'rolling_126_sessions': {'windows': len(windows), 'positive_windows': sum(x['return_pct'] > 0 for x in windows),
                                       'min_pct': min(x['return_pct'] for x in windows),
                                       'median_pct': round(statistics.median(x['return_pct'] for x in windows), 3),
                                       'max_pct': max(x['return_pct'] for x in windows), 'values': windows},
              'period_2026_stock_returns': stock_returns, 'concentration_sensitivity': sensitivity,
              'fixed_hash_groups': groups, 'fee_sensitivity_2026': costs, 'trades_2026': {'count': len(returns),
              'mean_pct': round(statistics.mean(returns), 3) if returns else None,
              'median_pct': round(statistics.median(returns), 3) if returns else None,
              'mean_without_top_5pct': round(statistics.mean(returns[:-trim_count]), 3) if returns else None,
              'month_cluster_bootstrap_90pct': monthly_bootstrap(trades_2026)},
              'limitations': ['当前成分事后回看，含幸存者和选择偏差', '五只股票仅近期未复权数据',
                              '缺少历史真实涨跌停、除权价和订单簿', '样本相互相关，滚动窗口重叠',
                              '剔除最强股票为事后敏感性，不是可交易规则']}
    with open('markets/a/stability-results.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
