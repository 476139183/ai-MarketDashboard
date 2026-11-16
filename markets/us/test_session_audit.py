import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import collect_sessions as collector
import session_audit

class AuditTests(unittest.TestCase):
    def test_sampled_prices_are_not_vwap_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'bars.sqlite3'
            t = int(datetime(2026, 10, 28, 9, 30, tzinfo=ZoneInfo('America/New_York')).timestamp())
            collector.store_bars(db, 'SKHY', [{'t':t,'o':100,'h':100,'l':100,'c':100,'v':None}], 'Nasdaq chart (sampled points; no volume)')
            rows = session_audit.audit(db)
            self.assertEqual(rows[0]['session'], 'regular')
            self.assertFalse(rows[0]['vwap_ready'])
            self.assertEqual(rows[0]['missing_volume'], 1)

    def test_positive_volume_does_not_make_sampled_points_ohlc(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'bars.sqlite3'
            t = int(datetime(2026, 10, 28, 9, 30, tzinfo=ZoneInfo('America/New_York')).timestamp())
            collector.store_bars(db, 'SKHY', [{'t':t,'o':100,'h':100,'l':100,'c':100,'v':10}], 'Nasdaq chart (sampled points; no volume)')
            self.assertFalse(session_audit.audit(db)[0]['vwap_ready'])

if __name__ == '__main__':
    unittest.main()
