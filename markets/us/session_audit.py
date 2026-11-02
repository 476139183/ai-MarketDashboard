#!/usr/bin/env python3
"""Summarize collected sessions and whether their data can support VWAP research."""
import argparse
import json
import sqlite3
from contextlib import closing
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from collect_sessions import BJ, DEFAULT_DB, NY


def audit(db: Path) -> list[dict]:
    if not db.is_file():
        raise FileNotFoundError(f'No collection database: {db}')
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        bars = conn.execute('select * from bars order by symbol, trading_date, timestamp').fetchall()
    groups = defaultdict(list)
    for bar in bars:
        groups[(bar['symbol'], bar['trading_date'], bar['session'])].append(bar)
    result = []
    for (symbol, day, session), rows in sorted(groups.items()):
        missing = sum(row['volume'] is None or row['volume'] <= 0 for row in rows)
        sources = sorted({row['source'] for row in rows})
        times = [row['timestamp'] for row in rows]
        # Full VWAP requires actual volume; only real OHLCV sources are potentially suitable.
        result.append({'symbol': symbol, 'trading_date_ny': day, 'session': session,
            'count': len(rows), 'first_bj': datetime.fromtimestamp(times[0], BJ).isoformat(),
            'last_bj': datetime.fromtimestamp(times[-1], BJ).isoformat(),
            'sample_low': min(row['low'] for row in rows), 'sample_high': max(row['high'] for row in rows),
            'missing_volume': missing, 'sources': sources,
            'vwap_ready': missing == 0 and all(source in ('Yahoo Finance chart', 'Massive historical aggregates') for source in sources),
            'gaps_over_1m': sum(b-a > 60 for a, b in zip(times, times[1:]))})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps({'generated_bj': datetime.now(BJ).isoformat(), 'sessions': audit(args.db),
        'note': 'vwap_ready is a necessary data-quality check, not validation of completeness or profitability; sampled prices are not full OHLC.'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
