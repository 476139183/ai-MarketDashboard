#!/usr/bin/env python3
"""基于保存的板块样本，回放收盘后形成的日线策略。"""
import json
from daily_strategy import from_minutes, run
from evaluate import SPLIT, stats

with open('markets/a/sector-results.json', encoding='utf-8') as f:
    source = json.load(f)
stocks, periods = {}, {'early': [], 'later': []}
for code, data in source['stocks'].items():
    days = from_minutes(data['bars'])
    result = run(days, .2 if data['board'] in ('科创板', '创业板') else .1,
                 min_entry_date=data['min_entry_date'])
    result.update(days=len(days), first=days[0]['date'] if days else None, last=days[-1]['date'] if days else None)
    stocks[code] = result
    for trade in result['trades']:
        if trade['signal_date'] < SPLIT and trade['exit_date'] < SPLIT:
            periods['early'].append(trade)
        elif trade['signal_date'] >= SPLIT:
            periods['later'].append(trade)
sectors = []
for s in source['sectors']:
    codes = [x['code'] for x in s.get('selected', []) if x['code'] in stocks]
    trades = [t for code in codes for t in stocks[code]['trades']]
    sectors.append({'name': s['name'], 'category': s['category'], 'members': len(codes), **stats(trades)})
report = {'method': '收盘后用MA5、MA20与过去20日最高价生成突破信号，次日开盘买入；收盘低于MA10或低于买价5%后下一交易日开盘退出；单边0.15%成本',
          'split': SPLIT, 'early': stats(periods['early']), 'later': stats(periods['later']),
          'sectors': sectors, 'stocks': stocks}
with open('markets/a/daily-evaluation.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
    f.write('\n')
