#!/usr/bin/env python3
"""在同一固定样本上比较 T+1 的退出时点。"""
import json
from evaluate import SPLIT, stats
from replay import replay

with open('markets/a/sector-results.json', encoding='utf-8') as f:
    universe = json.load(f)
with open('markets/a/strategy-evaluation.json', encoding='utf-8') as f:
    report = json.load(f)
for exit_bar in ('11:30', '14:45'):
    intervals = {'exploration': [], 'validation': []}
    by_stock = {}
    for code, stock in universe['stocks'].items():
        r = replay(stock['bars'], .2 if stock['board'] in ('创业板', '科创板') else .1,
                   min_entry_date=stock['min_entry_date'], exit_bar=exit_bar)
        by_stock[code] = r
        for trade in r['trades']:
            signal_date, exit_date = trade['signal_time'][:10], trade['exit_bar_end'][:10]
            if signal_date < SPLIT and exit_date < SPLIT:
                intervals['exploration'].append(trade)
            elif signal_date >= SPLIT:
                intervals['validation'].append(trade)
    report['variants']['exit_' + exit_bar] = {'exploration': stats(intervals['exploration']),
                                             'validation': stats(intervals['validation']), 'by_stock': by_stock}
with open('markets/a/strategy-evaluation.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
    f.write('\n')
