import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import massive_import as massive
import session_audit
from collect_sessions import store_bars
from datetime import datetime
from zoneinfo import ZoneInfo

class MassiveTests(unittest.TestCase):
    def test_pagination_and_timestamp_conversion(self):
        pages = [{'status':'OK','results':[{'t':1793194200000,'o':10,'h':12,'l':9,'c':11,'v':50}], 'next_url':'https://api.massive.com/v2/aggs/next?cursor=a'}, {'status':'OK','results':[]}]
        with patch.object(massive, 'request_page', side_effect=pages) as request:
            rows = massive.fetch_bars('SKHY', '2026-10-28', '2026-10-28', 'secret')
        self.assertEqual(rows[0]['t'], 1793194200)
        self.assertEqual(rows[0]['v'], 50)
        self.assertEqual(request.call_count, 2)

    def test_refuses_untrusted_pagination_url(self):
        with patch.object(massive, 'request_page', return_value={'status':'OK','results':[], 'next_url':'https://evil.example/steal'}):
            with self.assertRaises(ValueError):
                massive.fetch_bars('MU', '2026-10-28', '2026-10-28', 'secret')

    def test_massive_bars_not_overwritten_by_sampled_prices(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'bars.sqlite3'
            timestamp = int(datetime(2026, 10, 28, 9, 30, tzinfo=ZoneInfo('America/New_York')).timestamp())
            bar = {'t': timestamp, 'o': 10, 'h': 12, 'l': 9, 'c': 11, 'v': 50}
            store_bars(db, 'SKHY', [bar], massive.SOURCE)
            store_bars(db, 'SKHY', [{**bar, 'c': 99, 'v': None}], 'Nasdaq chart (sampled points; no volume)')
            report = session_audit.audit(db)[0]
            self.assertEqual(report['sample_high'], 12)
            self.assertEqual(report['sources'], [massive.SOURCE])
            self.assertTrue(report['vwap_ready'])

    def test_missing_key_is_rejected(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'API Key'):
                massive.api_key()

if __name__ == '__main__':
    unittest.main()
