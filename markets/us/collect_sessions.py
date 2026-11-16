#!/usr/bin/env python3
"""Collect US extended-hours 1-minute bars into a local SQLite database."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'dashboard'))
from engine import BJ, NY, fetch_chart, fetch_nasdaq_intraday_points

SYMBOLS = ('SNDK', 'SKHY', 'MU', 'NVDA')
DEFAULT_DB = Path(__file__).resolve().parent / '.cache' / 'session-bars.sqlite3'


def session_for(dt: datetime) -> str | None:
    dt = dt.astimezone(NY)
    if dt.weekday() >= 5:
        return None
    minute = dt.hour * 60 + dt.minute
    if 240 <= minute < 570:
        return 'pre'
    if 570 <= minute < 960:
        return 'regular'
    if 960 <= minute < 1200:
        return 'post'
    return None


def store_bars(db: Path, symbol: str, rows: list[dict], source: str) -> int:
    db.parent.mkdir(parents=True, exist_ok=True)
    collected = datetime.now(BJ).isoformat()
    values = []
    for row in rows:
        dt = datetime.fromtimestamp(row['t'], NY)
        session = session_for(dt)
        if session:
            values.append((symbol, row['t'], dt.date().isoformat(), session, row['o'], row['h'], row['l'], row['c'], row['v'], source, collected))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT NOT NULL, timestamp INTEGER NOT NULL, trading_date TEXT NOT NULL,
            session TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume REAL, source TEXT NOT NULL, collected_at TEXT NOT NULL,
            PRIMARY KEY (symbol, timestamp))''')
        conn.executemany("""INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timestamp) DO UPDATE SET trading_date=excluded.trading_date,
            session=excluded.session, open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, source=excluded.source,
            collected_at=excluded.collected_at
            WHERE excluded.source = 'Massive historical aggregates'
            OR (bars.source != 'Massive historical aggregates' AND
                (bars.source != 'Yahoo Finance chart' OR excluded.source = 'Yahoo Finance chart'))""", values)
    return len(values)


def collect_once(db: Path = DEFAULT_DB) -> dict:
    result = {}
    for symbol in SYMBOLS:
        try:
            try:
                rows, _, provenance = fetch_chart(symbol, '5d', '1m')
                if provenance != 'live':
                    raise RuntimeError('Yahoo 仅有旧缓存')
                source = 'Yahoo Finance chart'
            except Exception as yahoo_error:
                try:
                    points, _ = fetch_nasdaq_intraday_points(symbol)
                    rows = [{'t': p['t'], 'o': p['c'], 'h': p['c'], 'l': p['c'], 'c': p['c'], 'v': None} for p in points]
                    if not rows:
                        raise RuntimeError('Nasdaq 未返回分时点')
                    source = 'Nasdaq chart (sampled points; no volume)'
                except Exception as nasdaq_error:
                    raise RuntimeError(f'Yahoo: {yahoo_error}; Nasdaq: {nasdaq_error}') from nasdaq_error
            count = store_bars(db, symbol, rows, source)
            latest = datetime.fromtimestamp(max(row['t'] for row in rows), BJ).isoformat() if rows else None
            result[symbol] = {'count': count, 'latest_bj': latest, 'source': source}
        except Exception as exc:
            result[symbol] = {'error': str(exc)}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true', help='持续采集，直到 Ctrl+C')
    parser.add_argument('--interval', type=int, default=300, help='持续采集的间隔秒数（默认 300）')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if args.interval < 60:
        parser.error('--interval 必须至少 60 秒')
    while True:
        result = collect_once(args.db)
        print(json.dumps({'collected_bj': datetime.now(BJ).isoformat(), 'symbols': result}, ensure_ascii=False), flush=True)
        if not args.watch:
            if any('error' in item for item in result.values()):
                raise SystemExit(1)
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
