#!/usr/bin/env python3
"""下载复权日线，对独立的日线基准做研究；不代表盘中策略回测。"""
import argparse
import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from strategy import backtest, classify


def fetch(symbol, start, end):
    code, board = classify(symbol)
    query = urlencode({'param': f'{code},day,{start},{end},1200,qfq'})
    req = Request('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query,
                  headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=20) as response:
        payload = json.load(response)
    if payload.get('code') != 0:
        raise ValueError('行情接口返回错误')
    raw = payload.get('data', {}).get(code, {}).get('qfqday', [])
    if not raw:
        raise ValueError('未取得前复权日线')
    bars = [dict(date=x[0], open=float(x[1]), close=float(x[2]), high=float(x[3]),
                 low=float(x[4]), volume=float(x[5])) for x in raw]
    if any(a['date'] >= b['date'] for a, b in zip(bars, bars[1:])):
        raise ValueError('行情日期乱序或重复')
    return code, board, bars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', default='600519,601318,000333,000858,300750,300059,688981')
    parser.add_argument('--start', default='2023-01-01')
    parser.add_argument('--end', default='2026-09-30')
    parser.add_argument('--output', help='将结果写入 JSON 文件')
    args = parser.parse_args()
    results = []
    for symbol in args.symbols.split(','):
        try:
            code, board, bars = fetch(symbol, args.start, args.end)
            report = backtest(bars)
            report.update(symbol=code, board=board, first=bars[0]['date'], last=bars[-1]['date'],
                          sessions=len(bars), trade_count=len(report['trades']),
                          buy_hold_pct=round((bars[-1]['close'] / bars[0]['close'] - 1) * 100, 2),
                          bars=bars)
            results.append(report)
        except Exception as exc:
            results.append({'symbol': symbol, 'error': str(exc)})
    output = json.dumps({'method': '日线 20 日均线，收盘判定、次日开盘执行；与盘中策略不同',
                         'source': '腾讯公开前复权日线', 'start_requested': args.start,
                         'end_requested': args.end, 'results': results}, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output + '\n')
    else:
        print(output)


if __name__ == '__main__':
    main()
