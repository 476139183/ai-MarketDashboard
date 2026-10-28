#!/usr/bin/env python3
"""下载沪深300日线，生成与组合研究同区间的参考。"""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from portfolio import period_metrics

rows = []
for start, end in (('2023-01-01', '2024-12-31'), ('2025-01-01', '2026-10-27')):
    query = urlencode({'param': f'sh000300,day,{start},{end},1200,qfq'})
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as r:
        raw = json.load(r)['data']['sh000300']['day']
    rows.extend({'date': x[0], 'close': float(x[2])} for x in raw)
if not rows or any(x['date'] >= y['date'] for x, y in zip(rows, rows[1:])):
    raise ValueError('沪深300指数数据缺失、重复或乱序')
base = rows[0]['close']
curve = [{'date': x['date'], 'equity': x['close']/base} for x in rows]
with open('markets/a/long-backtest.json', encoding='utf-8') as f:
    periods = json.load(f)['periods']
out = {'name': '沪深300价格指数', 'source': '腾讯公开指数日线', 'bars': rows,
       'periods': {p: period_metrics(curve, *dates) for p, dates in periods.items()}}
with open('markets/a/benchmark-results.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
    f.write('\n')
