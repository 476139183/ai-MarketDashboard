#!/usr/bin/env python3
"""识别日线数据的明显错误及与指数日期不一致的地方。"""
import json

with open('markets/a/long-backtest.json', encoding='utf-8') as f:
    data = json.load(f)
with open('markets/a/benchmark-results.json', encoding='utf-8') as f:
    benchmark = json.load(f)
dates = [x['date'] for x in benchmark['bars']]
stocks = {}
for code, bars in data['bars'].items():
    observed = {b['date'] for b in bars}
    expected = [d for d in dates if bars[0]['date'] <= d <= bars[-1]['date']]
    missing = [d for d in expected if d not in observed]
    invalid = [b['date'] for b in bars if not (0 < b['low'] <= b['open'] <= b['high'] and
                                                b['low'] <= b['close'] <= b['high'] and b['volume'] >= 0)]
    stocks[code] = {'source': data['source_by_stock'][code], 'first': bars[0]['date'],
                    'last': bars[-1]['date'], 'days': len(bars), 'index_dates_missing': len(missing),
                    'missing_dates': missing, 'invalid_ohlcv': invalid}
report = {'note': '相对沪深300日期缺失可能是停牌、上市时间或数据缺口；不能仅据此判定原因。',
          'stock_count': len(stocks), 'stocks_with_missing_dates': sum(bool(x['index_dates_missing']) for x in stocks.values()),
          'missing_date_total': sum(x['index_dates_missing'] for x in stocks.values()),
          'invalid_ohlcv_count': sum(len(x['invalid_ohlcv']) for x in stocks.values()), 'stocks': stocks}
with open('markets/a/data-audit.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
    f.write('\n')
