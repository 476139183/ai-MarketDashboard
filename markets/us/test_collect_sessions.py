import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import collect_sessions as collector

NY = ZoneInfo('America/New_York')

class SessionCollectionTests(unittest.TestCase):
    def test_four_requested_symbols_are_collected(self):
        self.assertEqual(set(collector.SYMBOLS), {'SNDK', 'SKHY', 'MU', 'NVDA'})

    def test_session_boundaries_and_weekend(self):
        for hour, minute, expected in [(3, 59, None), (4, 0, 'pre'), (9, 30, 'regular'), (16, 0, 'post'), (20, 0, None)]:
            self.assertEqual(collector.session_for(datetime(2026, 10, 26, hour, minute, tzinfo=NY)), expected)
        self.assertIsNone(collector.session_for(datetime(2026, 10, 25, 10, 0, tzinfo=NY)))

    def test_store_is_idempotent_and_keeps_session_and_source(self):
        stamp = int(datetime(2026, 10, 26, 8, 0, tzinfo=NY).timestamp())
        rows = [{'t': stamp, 'o': 10., 'h': 11., 'l': 9., 'c': 10.5, 'v': 100.}]
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'bars.sqlite3'
            self.assertEqual(collector.store_bars(db, 'SNDK', rows, 'yahoo'), 1)
            self.assertEqual(collector.store_bars(db, 'SNDK', rows, 'yahoo'), 1)
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(db)) as conn, conn:
                self.assertEqual(conn.execute('select symbol, session, source, count(*) from bars').fetchone(), ('SNDK', 'pre', 'yahoo', 1))

    def test_failed_symbol_does_not_prevent_other_symbol(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(collector, 'fetch_chart', side_effect=[RuntimeError('offline'), ([], {}, 'live'), ([], {}, 'live'), ([], {}, 'live')]), patch.object(collector, 'fetch_nasdaq_intraday_points', side_effect=RuntimeError('offline')):
            outcome = collector.collect_once(Path(folder) / 'bars.sqlite3')
            self.assertIn('error', outcome['SNDK'])
            self.assertEqual(outcome['SKHY']['count'], 0)

if __name__ == '__main__':
    unittest.main()
