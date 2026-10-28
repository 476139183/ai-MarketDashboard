#!/usr/bin/env python3
"""A股 15 分钟探索性回放：次根开盘入场，T+1 最早开盘退出。"""
from datetime import datetime
from itertools import groupby
import argparse
import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from strategy import classify, intraday_signal


def replay(bars, limit_ratio, fee=0.0015, min_entry_date=None, variant='baseline', exit_bar='09:45'):
    grouped = [(day, list(rows)) for day, rows in groupby(bars, key=lambda x: x['time'][:10])]
    trades, position = [], None
    prev_close = None
    for day, daybars in grouped:
        exited_at = None
        exit_rows = [b for b in daybars if b['time'][11:] == exit_bar]
        if position and day > position['entry_bar_end'][:10] and exit_rows:
            exit_row = exit_rows[0]
            price = float(exit_row['open'])
            # 只有前一日完整收盘价才估算跌停，缺失时不模拟卖出。
            if prev_close and price > prev_close * (1 - limit_ratio) * 1.005:
                position['exit_bar_end'] = exit_row['time']
                position['exit_price'] = price
                position['return_pct'] = round(((price * (1 - fee)) / (position['entry_price'] * (1 + fee)) - 1) * 100, 3)
                trades.append(position)
                position = None
                exited_at = exit_row['time']
        if not position and prev_close and (min_entry_date is None or day >= min_entry_date):
            for i in range(1, len(daybars) - 1):
                b = daybars[i]
                if exited_at and b['time'] < exited_at:
                    continue
                now = datetime.strptime(b['time'], '%Y-%m-%d %H:%M')
                following = daybars[i + 1]
                next_time = datetime.strptime(following['time'], '%Y-%m-%d %H:%M')
                if (next_time - now).total_seconds() != 900 or next_time.time().strftime('%H:%M') > '14:45':
                    continue
                upper, lower = prev_close * (1 + limit_ratio), prev_close * (1 - limit_ratio)
                signal = intraday_signal(daybars[:i+1], now, True, upper, lower)
                if signal['action'] == '观察做多' and variant != 'baseline':
                    # 仅使用截至本根收盘已知的数据；开盘区间高点的突破幅度与量能为候选过滤器。
                    margin = float(b['close']) / signal['orb_high'] - 1
                    volume_ratio = float(b['volume']) / float(daybars[0]['volume'])
                    if variant in ('margin', 'combined') and margin < .003:
                        continue
                    if variant in ('volume', 'combined') and volume_ratio < 1.2:
                        continue
                price = float(following['open'])
                if signal['action'] == '观察做多' and lower * 1.005 < price < upper * .995:
                    position = {'signal_time': b['time'], 'entry_bar_end': following['time'],
                                'entry_price': price}
                    break
        prev_close = float(daybars[-1]['close']) if daybars[-1]['time'][11:] == '15:00' else None
    net = 1.0
    for trade in trades:
        net *= 1 + trade['return_pct'] / 100
    return {'closed_trades': len(trades), 'wins': sum(t['return_pct'] > 0 for t in trades),
            'compounded_return_pct': round((net - 1) * 100, 2), 'trades': trades,
            'open_position': position}


def fetch(symbol):
    code, board = classify(symbol)
    url = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketData.getKLineData?' + urlencode({'symbol': code, 'scale': 15, 'ma': 'no', 'datalen': 1023})
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
        text = response.read().decode('utf-8')
    start, end = text.find('(['), text.rfind('])')
    if start < 0 or end < 0:
        raise ValueError('行情返回格式异常')
    raw = json.loads(text[start + 1:end + 1])
    bars = [dict(time=x['day'][:16], open=float(x['open']), high=float(x['high']),
                 low=float(x['low']), close=float(x['close']), volume=float(x['volume']),
                 amount=float(x.get('amount', 0))) for x in raw]
    cutoff = datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    bars = [b for b in bars if datetime.strptime(b['time'], '%Y-%m-%d %H:%M') <= cutoff]
    if not bars or any(a['time'] >= b['time'] for a, b in zip(bars, bars[1:])):
        raise ValueError('行情为空、乱序或重复')
    return code, board, bars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', default='600519,601318,000333,000858,300750,300059,688981')
    parser.add_argument('--output', required=True)
    parser.add_argument('--exit-bar', choices=('09:45', '11:30', '14:45'), default='09:45',
                        help='次日退出K线结束时间；实际使用该K线开盘价')
    args = parser.parse_args()
    results = []
    for symbol in args.symbols.split(','):
        try:
            code, board, bars = fetch(symbol)
            ratio = .2 if board in ('创业板', '科创板') else .1
            result = replay(bars, ratio, exit_bar=args.exit_bar)
            result.update(symbol=code, board=board, first=bars[0]['time'], last=bars[-1]['time'], bars=bars)
            results.append(result)
        except Exception as exc:
            results.append({'symbol': symbol, 'error': str(exc)})
    report = {'source': '新浪公开15分钟未复权行情', 'method': f'15分钟开盘区间突破与估算均价，下一根K线开盘买入，最早下一交易日结束于 {args.exit_bar} 的K线开盘卖出，双边各0.15%成本；按普通股票前收估算涨跌停，非真实订单簿', 'results': results}
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
