#!/usr/bin/env python3
"""当前热榜固定股票池的历史研究；不是历史动态热榜回测。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from daily_strategy import from_minutes, run
from long_backtest import metrics
from portfolio import equity_curve, period_metrics
from research import fetch as daily_fetch
from replay import fetch as minute_fetch
from strategy import classify

RULES = [('breakout', 10), ('breakout', 20), ('reclaim', 10), ('reclaim', 20), ('trend', 10), ('trend', 20)]


def as_of_date(snapshot_time):
    # 17:00 后才允许采集当日收盘日线；盘中始终只用此前日期。
    return (snapshot_time.date() if snapshot_time.hour >= 17 else snapshot_time.date() - timedelta(days=1)).isoformat()


def prepare_symbols(stocks):
    accepted, rejected, seen = [], [], set()
    for stock in stocks:
        code = str(stock.get('code', '')).strip()
        if code in seen:
            continue
        seen.add(code)
        try:
            if stock.get('exchange') == 'BJ' and len(code) == 6 and code.isdigit() and code.startswith(('4', '8', '92')):
                board = '北交所'
            else:
                _, board = classify(code)
            accepted.append({**stock, 'code': code, 'board': board})
        except ValueError as exc:
            rejected.append({**stock, 'code': code, 'reason': str(exc)})
    return accepted, rejected


def tracking_extras(stocks, records):
    known = {x['code'] for x in stocks}
    candidates = []
    for row in records:
        if row['code'] in known:
            continue
        known.add(row['code'])
        candidates.append({'code': row['code'], 'name': row['name'],
                           'exchange': 'BJ' if row.get('board') == '北交所' else None})
    accepted, _ = prepare_symbols(candidates)
    return accepted


def load(code, end, board):
    try:
        symbol = 'bj' + code if board == '北交所' else classify(code)[0]
        params = urlencode({'symbol': symbol, 'scale': 240, 'ma': 'no', 'datalen': 1023})
        url = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketData.getKLineData?' + params
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
            text = response.read().decode('utf-8')
        start, stop = text.find('(['), text.rfind('])')
        if start < 0 or stop < 0:
            raise ValueError('新浪日线返回格式异常')
        raw = json.loads(text[start+1:stop+1])
        rows = [dict(date=x['day'][:10], open=float(x['open']), high=float(x['high']),
                     low=float(x['low']), close=float(x['close']), volume=float(x['volume']))
                for x in raw if '2023-01-01' <= x['day'][:10] <= end]
        if len(rows) < 21 or any(a['date'] >= b['date'] for a, b in zip(rows, rows[1:])):
            raise ValueError('新浪日线不足、重复或乱序')
        return rows, ('新浪北交所未复权日线；历史代码连续性待核实' if board == '北交所' else '新浪未复权日线'), []
    except Exception as exc:
        sina_error = str(exc)
    parts, failures = [], [{'source': '新浪未复权日线', 'reason': sina_error}]
    for start, stop in [('2023-01-01', '2024-12-31'), ('2025-01-01', end)]:
        if stop < start:
            continue
        try:
            _, _, rows = daily_fetch(code, start, stop)
            parts.extend(x for x in rows if start <= x['date'] <= stop)
        except Exception as exc:
            failures.append({'start': start, 'end': stop, 'reason': str(exc)})
    if len(parts) >= 21:
        if any(a['date'] >= b['date'] for a, b in zip(parts, parts[1:])):
            raise ValueError('日线重复或乱序')
        return parts, '腾讯前复权日线', failures
    try:
        _, _, minute = minute_fetch(code)
        rows = [b for b in from_minutes(minute) if b['date'] <= end]
        if len(rows) < 21:
            raise ValueError('不足21个完整交易日')
        return rows, '新浪近期未复权分钟聚合日线', failures
    except Exception as exc:
        raise ValueError(f'前复权日线不足；近期分钟回退失败：{exc}') from exc


def main():
    with open('markets/a/hot-universe.json', encoding='utf-8') as f:
        snapshot = json.load(f)
    with open('markets/a/benchmark-results.json', encoding='utf-8') as f:
        benchmark = json.load(f)
    end = as_of_date(datetime.fromisoformat(snapshot['generated_at']))
    if benchmark.get('requested_end', benchmark['bars'][-1]['date']) < end:
        raise ValueError('指数日线未更新到本次快照请求日期，请先运行 benchmark.py')
    calendar = [x['date'] for x in benchmark['bars'] if x['date'] <= end]
    if not calendar:
        raise ValueError('指数交易日期为空，请先更新指数数据')
    stocks, rejected = prepare_symbols(snapshot['stocks'])
    ledger = Path('markets/a/candidate-ledger.json')
    records = json.loads(ledger.read_text(encoding='utf-8')).get('records', []) if ledger.exists() else []
    extra_stocks = tracking_extras(stocks, records)
    histories, failures, sources = {}, {}, {}
    print(f'去重后支持股票 {len(stocks)}，额外追踪 {len(extra_stocks)}，不支持 {len(rejected)}', flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(load, stock['code'], end, stock['board']): stock for stock in stocks + extra_stocks}
        for i, future in enumerate(as_completed(futures), 1):
            stock = futures[future]
            code = stock['code']
            try:
                rows, source, missing_chunks = future.result()
                histories[code] = rows
                sources[code] = {'source': source, 'first': rows[0]['date'], 'last': rows[-1]['date'],
                                 'sessions': len(rows), 'failed_chunks': missing_chunks}
            except Exception as exc:
                failures[code] = {'name': stock.get('name'), 'error': str(exc)}
            if i % 25 == 0 or i == len(futures):
                print(f'行情 {i}/{len(futures)}，可用 {len(histories)}，失败 {len(failures)}', flush=True)
    metadata = {s['code']: s for s in stocks + extra_stocks}
    current_codes = {x['code'] for x in stocks}
    current_histories = {c: b for c, b in histories.items() if c in current_codes}
    periods = {'2023_2024': ('2023-01-01', '2024-12-31'),
               '2025': ('2025-01-01', '2025-12-31'), '2026': ('2026-01-01', end)}
    rules = {}
    for entry, ma in RULES:
        per_stock, per_period = {}, {k: [] for k in periods}
        for code, bars in current_histories.items():
            board = metadata[code]['board']
            result = run(bars, .3 if board == '北交所' else .2 if board in ('创业板', '科创板') else .1,
                         entry_rule=entry, exit_ma=ma, trading_calendar=calendar)
            per_stock[code] = result
            for trade in result['trades']:
                for name, (start, stop) in periods.items():
                    if start <= trade['signal_date'] and trade['exit_date'] <= stop:
                        per_period[name].append((code, trade))
        curve = equity_curve(current_histories, per_stock) if current_histories else []
        rules[f'{entry}_ma{ma}'] = {'periods': {k: metrics(v) for k, v in per_period.items()},
                                    'portfolio': {k: period_metrics(curve, *v) for k, v in periods.items()},
                                    'by_stock': per_stock, 'curve': curve}
    result = {'universe_snapshot': snapshot['generated_at'], 'scope': '当前人气榜与飙升榜固定股票池的历史回看；并非历史动态热榜',
              'as_of_date': end, 'snapshot_size': len(snapshot['stocks']), 'supported': len(stocks),
              'tested': len(current_histories), 'tracked_outside_snapshot': [s['code'] for s in extra_stocks],
              'unsupported': rejected, 'failed': failures, 'sources': sources,
              'metadata': metadata, 'periods': periods, 'rules': rules,
              'limitations': ['当前榜单事后选股导致前视与幸存者偏差', '各股数据范围与复权方式不同，北交所历史代码连续性待核实',
                              '市场日历仅覆盖所下载的指数日期；停牌与历史真实限价未核验',
                              '开盘跳空和涨跌停为粗略近似，未模拟队列、除权、成交冲击和买卖手数',
                              '固定初始等权各股独立现金，非动态热榜轮换组合']}
    raw = json.dumps(histories, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    with gzip.open('markets/a/hot-history.json.gz', 'wb') as f:
        f.write(raw)
    result['history_snapshot'] = {'file': 'hot-history.json.gz', 'sha256_uncompressed': hashlib.sha256(raw).hexdigest()}
    with open('markets/a/hot-backtest.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
