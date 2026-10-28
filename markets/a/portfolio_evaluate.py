#!/usr/bin/env python3
"""固定初始等权组合、未平仓逐日盯市及分期回撤。"""
import json
from portfolio import equity_curve, period_metrics

with open('markets/a/long-backtest.json', encoding='utf-8') as f:
    backtest = json.load(f)
report = {'method': '每只股票初始分配相同现金，互不调拨，不再平衡，允许碎股；含未平仓按收盘价盯市，单边费用0.15%',
          'chosen_by_exploration_mean': backtest['chosen_by_exploration_mean'], 'rules': {}}
for name, rule in backtest['rules'].items():
    curve = equity_curve(backtest['bars'], rule['by_stock'])
    report['rules'][name] = {'periods': {p: period_metrics(curve, *dates) for p, dates in backtest['periods'].items()},
                             'curve': curve}
with open('markets/a/portfolio-evaluation.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
    f.write('\n')
