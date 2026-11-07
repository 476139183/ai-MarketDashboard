import json
import unittest
from unittest.mock import patch
from sina_intraday import parse_bars, fetch_rows
from test_us_radar import stamp

class SinaIntradayTests(unittest.TestCase):
    def test_close_stamps_are_shifted_to_open_and_volume_is_shares(self):
        payload='/* comment */\nvar data=('+json.dumps([dict(d='2026-11-06 09:35:00',o='10',h='12',l='9',c='11',v='12345',a='130000')])+');'
        rows=parse_bars(payload,5)
        self.assertEqual(rows[0]['t'],stamp('2026-11-06T09:30')//1000)
        self.assertEqual(rows[0]['v'],12345)
        self.assertEqual(rows[0]['o'],10)

    def test_jsonp_is_parsed_as_data_and_invalid_ohlcv_rejected(self):
        for text in ('var data=(null);','var data=([]);alert(1);','var data=([{bad}]);'):
            with self.assertRaises(ValueError):parse_bars(text,5)
        bad='var data=('+json.dumps([dict(d='2026-11-06 09:35:00',o='10',h='8',l='9',c='11',v='1')])+');'
        with self.assertRaises(ValueError):parse_bars(bad,5)

    def test_unsupported_interval_not_silently_replaced(self):
        with self.assertRaises(ValueError):fetch_rows('SNDK',10)
        with self.assertRaises(ValueError):fetch_rows('../SNDK',5)

if __name__=='__main__':unittest.main()
