#!/usr/bin/env python3
"""Import Massive free-tier historical minute OHLCV into the session database."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from collect_sessions import BJ, NY, DEFAULT_DB, SYMBOLS, store_bars

BASE = 'https://api.massive.com'
SOURCE = 'Massive historical aggregates'


def api_key() -> str:
    key = os.environ.get('MASSIVE_API_KEY') or os.environ.get('POLYGON_API_KEY')
    if not key:
        raise ValueError('请设置 MASSIVE_API_KEY 环境变量（Massive 免费账号 API Key）')
    return key


def request_page(url: str, key: str) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.massive.com' or not parsed.path.startswith('/v2/aggs/'):
        raise ValueError('拒绝不受信任的分页地址')
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k.lower() != 'apikey']
    query.append(('apiKey', key))
    request_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ''))
    try:
        with urllib.request.urlopen(urllib.request.Request(request_url, headers={'Accept':'application/json'}), timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Massive HTTP {exc.code}；请检查 Key、权限与免费档数据时间') from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Massive 网络请求失败: {exc.reason}') from None
    if data.get('status') not in ('OK', 'DELAYED'):
        raise RuntimeError('Massive 返回错误，请检查 API Key 和账户权限')
    return data


def fetch_bars(symbol: str, start: str, end: str, key: str) -> list[dict]:
    if symbol not in SYMBOLS:
        raise ValueError('不支持的标的')
    url = f'{BASE}/v2/aggs/ticker/{symbol}/range/1/minute/{start}/{end}?adjusted=true&sort=asc&limit=50000'
    rows = []
    seen = set()
    while url:
        if url in seen:
            raise RuntimeError('Massive 分页循环')
        seen.add(url)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'api.massive.com' or not parsed.path.startswith('/v2/aggs/'):
            raise ValueError('拒绝不受信任的分页地址')
        data = request_page(url, key)
        for bar in data.get('results') or []:
            rows.append({'t': int(bar['t']) // 1000, 'o': float(bar['o']), 'h': float(bar['h']),
                         'l': float(bar['l']), 'c': float(bar['c']), 'v': float(bar['v'])})
        url = data.get('next_url')
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    today_ny = datetime.now(NY).date()
    parser.add_argument('--start', default=(today_ny - timedelta(days=30)).isoformat())
    parser.add_argument('--end', default=(today_ny - timedelta(days=1)).isoformat())
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--symbols', default=','.join(SYMBOLS), help='逗号分隔的标的')
    args = parser.parse_args()
    try:
        start = datetime.strptime(args.start, '%Y-%m-%d').date()
        end = datetime.strptime(args.end, '%Y-%m-%d').date()
        if start > end or end >= today_ny:
            raise ValueError('免费档请选择有效日期，结束日期须早于当前美东日期')
        key = api_key()
    except ValueError as exc:
        parser.error(str(exc))
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    if not symbols or any(s not in SYMBOLS for s in symbols):
        parser.error('请提供受支持的股票代码')
    result = {}
    for symbol in symbols:
        try:
            rows = fetch_bars(symbol, args.start, args.end, key)
            count = store_bars(args.db, symbol, rows, SOURCE)
            result[symbol] = {'received': len(rows), 'stored': count}
        except Exception as exc:
            result[symbol] = {'error': str(exc)}
    print(json.dumps({'imported_bj': datetime.now(BJ).isoformat(), 'start_ny': args.start,
        'end_ny': args.end, 'source': SOURCE, 'symbols': result}, ensure_ascii=False, indent=2))
    if any('error' in item for item in result.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
