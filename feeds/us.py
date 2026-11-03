"""美股日线提供者。"""
from __future__ import annotations

import os
from datetime import date
from time import time as wall_time
from typing import List, Tuple
from urllib.parse import urlencode

from .http_util import read_json
from .schema import last_n, normalize_bars

BarList = List[dict]


def _range_tag(days: int) -> str:
    if days <= 30:
        return '1mo'
    if days <= 90:
        return '3mo'
    if days <= 180:
        return '6mo'
    if days <= 365:
        return '1y'
    return '2y'


def from_yahoo(symbol: str, days: int = 180) -> Tuple[BarList, str]:
    symbol = symbol.upper().strip()
    range_ = _range_tag(days)
    last_err = None
    for host in ('query1', 'query2'):
        url = (
            f'https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}'
            f'?range={range_}&interval=1d&includePrePost=false'
        )
        try:
            payload = read_json(url, timeout=12)
            result = payload['chart']['result'][0]
            ts = result['timestamp']
            q = result['indicators']['quote'][0]
            rows = []
            for i, t in enumerate(ts):
                o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
                if None in (o, h, l, c):
                    continue
                d = date.fromtimestamp(t).isoformat()
                rows.append(dict(date=d, open=o, high=h, low=l, close=c, volume=q['volume'][i] or 0))
            return last_n(normalize_bars(rows, min_bars=5), days), 'yahoo_chart'
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f'Yahoo Chart 失败: {last_err}')


def from_yfinance(symbol: str, days: int = 180) -> Tuple[BarList, str]:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError('未安装 yfinance：pip install yfinance') from exc
    period = _range_tag(days)
    ticker = yf.Ticker(symbol.upper().strip())
    df = ticker.history(period=period, interval='1d', auto_adjust=True)
    if df is None or getattr(df, 'empty', True):
        raise ValueError('yfinance 日线为空')
    rows = []
    for idx, row in df.iterrows():
        d = idx.date().isoformat() if hasattr(idx, 'date') else str(idx)[:10]
        rows.append(dict(date=d, open=row['Open'], high=row['High'], low=row['Low'],
                         close=row['Close'], volume=row.get('Volume', 0) or 0))
    return last_n(normalize_bars(rows, min_bars=5), days), 'yfinance'


def from_alphavantage(symbol: str, days: int = 180) -> Tuple[BarList, str]:
    key = os.environ.get('ALPHAVANTAGE_API_KEY', '').strip()
    if not key:
        raise RuntimeError('未配置 ALPHAVANTAGE_API_KEY')
    query = urlencode({
        'function': 'TIME_SERIES_DAILY_ADJUSTED',
        'symbol': symbol.upper().strip(),
        'outputsize': 'full' if days > 100 else 'compact',
        'apikey': key,
    })
    payload = read_json('https://www.alphavantage.co/query?' + query, timeout=30)
    series = payload.get('Time Series (Daily)')
    if not series:
        raise ValueError(payload.get('Note') or payload.get('Information') or 'Alpha Vantage 无日线')
    rows = []
    for d, item in series.items():
        rows.append(dict(
            date=d,
            open=item['1. open'],
            high=item['2. high'],
            low=item['3. low'],
            close=item.get('5. adjusted close') or item['4. close'],
            volume=item.get('6. volume') or item.get('5. volume') or 0,
        ))
    return last_n(normalize_bars(rows, min_bars=5), days), 'alphavantage_adj'


def from_finnhub(symbol: str, days: int = 180) -> Tuple[BarList, str]:
    key = os.environ.get('FINNHUB_API_KEY', '').strip()
    if not key:
        raise RuntimeError('未配置 FINNHUB_API_KEY')
    end_ts = int(wall_time())
    start_ts = end_ts - max(days, 30) * 86400 * 2
    query = urlencode({
        'symbol': symbol.upper().strip(),
        'resolution': 'D',
        'from': start_ts,
        'to': end_ts,
        'token': key,
    })
    payload = read_json('https://finnhub.io/api/v1/stock/candle?' + query, timeout=20)
    if payload.get('s') != 'ok':
        raise ValueError(f"Finnhub 状态: {payload.get('s')}")
    rows = []
    for i, t in enumerate(payload['t']):
        rows.append(dict(
            date=date.fromtimestamp(t).isoformat(),
            open=payload['o'][i],
            high=payload['h'][i],
            low=payload['l'][i],
            close=payload['c'][i],
            volume=payload['v'][i] or 0,
        ))
    return last_n(normalize_bars(rows, min_bars=5), days), 'finnhub'


def from_dsa(symbol: str, days: int = 180, client=None) -> Tuple[BarList, str]:
    if client is None:
        raise RuntimeError('DSA client 未注入')
    result = client.daily(symbol, days=days)
    return last_n(normalize_bars(result['data'], min_bars=5), days), 'dsa'


PROVIDERS = {
    'dsa': from_dsa,
    'yahoo': from_yahoo,
    'yfinance': from_yfinance,
    'alphavantage': from_alphavantage,
    'finnhub': from_finnhub,
}

DEFAULT_ORDER = ('dsa', 'yahoo', 'yfinance', 'alphavantage', 'finnhub')
