#!/usr/bin/env python3
"""固定训练/验证区间，比较预先定义的突破过滤规则。"""
import json
import statistics
from replay import replay

SPLIT = '2026-09-25'
VARIANTS = ('baseline', 'margin', 'volume', 'combined')


def stats(trades):
    values = [t['return_pct'] for t in trades]
    return {'trades': len(values), 'win_rate_pct': round(100 * sum(x > 0 for x in values) / len(values), 2) if values else None,
            'mean_trade_return_pct': round(statistics.mean(values), 3) if values else None,
            'median_trade_return_pct': round(statistics.median(values), 3) if values else None}


def main():
    with open('markets/a/sector-results.json', encoding='utf-8') as f:
        universe = json.load(f)
    report = {'split': SPLIT, 'parameters': {'margin': '突破开盘区间高点至少0.3%',
              'volume': '确认K线成交量至少开盘15分钟成交量的1.2倍',
              'combined': '同时满足两个过滤条件'}, 'variants': {}}
    for variant in VARIANTS:
        intervals = {'exploration': [], 'validation': []}
        by_stock = {}
        for code, stock in universe['stocks'].items():
            r = replay(stock['bars'], .2 if stock['board'] in ('创业板', '科创板') else .1,
                       min_entry_date=stock['min_entry_date'], variant=variant)
            by_stock[code] = r
            for trade in r['trades']:
                signal_date, exit_date = trade['signal_time'][:10], trade['exit_bar_end'][:10]
                if signal_date < SPLIT and exit_date < SPLIT:
                    intervals['exploration'].append(trade)
                elif signal_date >= SPLIT:
                    intervals['validation'].append(trade)
        report['variants'][variant] = {'exploration': stats(intervals['exploration']),
                                      'validation': stats(intervals['validation']), 'by_stock': by_stock}
    with open('markets/a/strategy-evaluation.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
