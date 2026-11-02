import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import alpaca_live

class AlpacaTests(unittest.TestCase):
    def test_parse_latest_bars_with_volume_and_timestamp(self):
        payload = {'bars': {'SKHY': {'t':'2026-10-28T13:30:00Z','o':10,'h':12,'l':9,'c':11,'v':123}}}
        rows = alpaca_live.parse_bars(payload)
        self.assertEqual(rows['SKHY']['v'], 123)
        self.assertEqual(rows['SKHY']['t'], 1793194200)

    def test_both_credentials_are_required(self):
        with patch.dict('os.environ', {'APCA_API_KEY_ID':'key'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'APCA_API_SECRET_KEY'):
                alpaca_live.credentials()

    def test_rejects_old_data_for_live_collection(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = {'bars': {'SKHY': {'t':'2020-01-01T14:30:00Z','o':10,'h':12,'l':9,'c':11,'v':123}}}
            with patch.object(alpaca_live, 'fetch_latest', return_value=payload):
                out = alpaca_live.collect_once(Path(folder) / 'test.sqlite3', 'id', 'secret', max_age=300)
            self.assertEqual(out['SKHY']['status'], 'stale')
            self.assertFalse((Path(folder) / 'test.sqlite3').exists())

if __name__ == '__main__':
    unittest.main()
