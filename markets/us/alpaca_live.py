#!/usr/bin/env python3
"""Poll Alpaca free IEX latest minute bars for local personal use."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from collect_sessions import BJ, SYMBOLS, store_bars

DEFAULT_DB = Path(__file__).resolve().parent / '.cache' / 'iex-live.sqlite3'
SOURCE = 'Alpaca IEX latest bar'


def credentials() -> tuple[str, str]:
    key = os.environ.get('APCA_API_KEY_ID')
    secret = os.environ.get('APCA_API_SECRET_KEY')
    if not key or not secret:
        raise ValueError('需要 APCA_API_KEY_ID 和 APCA_API_SECRET_KEY 两个环境变量')
    return key, secret


def fetch_latest(key: str, secret: str) -> dict:
    params = urllib.parse.urlencode({'symbols': ','.join(SYMBOLS), 'feed': 'iex'})
    request = urllib.request.Request('https://data.alpaca.markets/v2/stocks/bars/latest?' + params,
        headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Alpaca HTTP {exc.code}；请检查凭证和行情权限') from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Alpaca 网络请求失败: {exc.reason}') from None


def parse_bars(payload: dict) -> dict:
    result = {}
    for symbol, bar in (payload.get('bars') or {}).items():
        if symbol not in SYMBOLS or not bar:
            continue
        dt = datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('Alpaca 时间缺少时区')
        result[symbol] = {'t': int(dt.timestamp()), 'o': float(bar['o']), 'h': float(bar['h']),
                          'l': float(bar['l']), 'c': float(bar['c']), 'v': float(bar['v'])}
    return result


def collect_once(db: Path, key: str, secret: str, max_age: int = 300) -> dict:
    bars = parse_bars(fetch_latest(key, secret))
    now = datetime.now(timezone.utc).timestamp()
    out = {}
    for symbol in SYMBOLS:
        row = bars.get(symbol)
        if row is None:
            out[symbol] = {'status': 'missing'}
            continue
        age = now - row['t']
        if age > max_age or age < -60:
            out[symbol] = {'status': 'stale', 'age_seconds': round(age)}
            continue
        count = store_bars(db, symbol, [row], SOURCE)
        out[symbol] = {'status': 'stored' if count else 'outside_session',
                       'bar_time_bj': datetime.fromtimestamp(row['t'], BJ).isoformat(),
                       'age_seconds': round(age)}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if args.interval < 15:
        parser.error('interval 必须至少 15 秒')
    try:
        key, secret = credentials()
    except ValueError as exc:
        parser.error(str(exc))
    while True:
        try:
            result = collect_once(args.db, key, secret)
            print(json.dumps({'checked_bj': datetime.now(BJ).isoformat(), 'source': SOURCE,
                              'symbols': result}, ensure_ascii=False), flush=True)
        except (RuntimeError, ValueError) as exc:
            print(json.dumps({'error': str(exc)}, ensure_ascii=False), flush=True)
            if not args.watch:
                raise SystemExit(1)
        if not args.watch:
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
