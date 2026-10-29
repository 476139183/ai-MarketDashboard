#!/usr/bin/env python3
"""在当前大市值股票池上比较日线规则与市场趋势过滤。"""
import gzip
import json
from daily_strategy import run
from long_backtest import metrics
from portfolio import equity_curve, period_metrics

PERIODS = {'2023_2024': ('2023-01-01', '2024-12-31'),
           '2025': ('2025-01-01', '2025-12-31'), '2026': ('2026-01-01', '2026-10-27')}


def market_above_ma20(index_bars):
    allowed = set()
    for i in range(19, len(index_bars)):
        current = float(index_bars[i]['close'])
        if current > sum(float(x['close']) for x in index_bars[i-19:i+1]) / 20:
            allowed.add(index_bars[i]['date'])
    return allowed


def main():
    with open('markets/a/large-universe.json', encoding='utf-8') as f:
        universe = json.load(f)
    with gzip.open('markets/a/hot-history.json.gz', 'rt', encoding='utf-8') as f:
        all_bars = json.load(f)
    with open('markets/a/hot-backtest.json', encoding='utf-8') as f:
        hot = json.load(f)
    with open('markets/a/benchmark-results.json', encoding='utf-8') as f:
        benchmark = json.load(f)
    selected = {x['code'] for x in universe['included']}
    bars = {c: all_bars[c] for c in selected}
    calendar = [x['date'] for x in benchmark['bars']]
    market_dates = market_above_ma20(benchmark['bars'])
    result = {}
    for entry in ('breakout', 'reclaim', 'trend'):
        for ma in (10, 20):
            for market_filter in (False, True):
                name = f'{entry}_ma{ma}' + ('_market' if market_filter else '')
                by_stock, period_trades = {}, {k: [] for k in PERIODS}
                for code, series in bars.items():
                    board = hot['metadata'][code]['board']
                    r = run(series, .3 if board == '北交所' else .2 if board in ('创业板', '科创板') else .1,
                            entry_rule=entry, exit_ma=ma, trading_calendar=calendar,
                            allowed_signal_dates=market_dates if market_filter else None)
                    by_stock[code] = r
                    for trade in r['trades']:
                        for period, (start, end) in PERIODS.items():
                            if start <= trade['signal_date'] and trade['exit_date'] <= end:
                                period_trades[period].append((code, trade))
                curve = equity_curve(bars, by_stock)
                portfolios = {k: period_metrics(curve, *v) for k, v in PERIODS.items()}
                result[name] = {'trades': {k: metrics(v) for k, v in period_trades.items()},
                                'portfolio': portfolios, 'by_stock': by_stock, 'curve': curve}
    # Only exploration period decides the experimental candidate; retain validation/test outputs unaltered.
    eligible = [(name, row) for name, row in result.items()
                if row['trades']['2023_2024']['trades'] >= 30 and row['portfolio']['2023_2024']]
    chosen = max(eligible, key=lambda v: v[1]['portfolio']['2023_2024']['return_pct'] +
                 v[1]['portfolio']['2023_2024']['max_drawdown_pct'])[0] if eligible else None
    out = {'scope': '当前热门股池中排除ST、退市和小流通市值股票的历史研究',
           'universe_generated_at': universe['generated_at'], 'universe_count': len(selected),
           'universe': sorted(selected), 'periods': PERIODS, 'rules': result,
           'candidate_selected_by_2023_2024': chosen,
           'selection_score': '探索期组合收益率加最大回撤（回撤为负数），且至少30笔已完成交易',
           'market_filter': '仅当收盘时沪深300收盘高于当日MA20，允许产生次日买入信号；退出不受此过滤',
           'limitations': ['当前板块热门榜和市值筛选历史，前视与幸存者偏差显著',
                           '仅排除当前ST/退市，缺少历史ST状态', '新浪日线未复权，不含完整除权、停牌、真实涨跌停和成交队列',
                           '筛选和策略探索使用已知结果，分期比较不代表独立样本外验证']}
    with open('markets/a/large-evaluation.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
