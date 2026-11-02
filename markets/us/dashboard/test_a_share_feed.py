import json
import unittest
from datetime import datetime
from unittest.mock import patch

import a_share_feed as feed


class AShareFeedTests(unittest.TestCase):
    def test_provider_end_times_are_converted_to_starts_without_filling_lunch(self):
        rows=[dict(day='2026-10-30 '+clock, open='100',high='102',low='99',close='101',volume='123')
              for clock in ('11:30:00','13:15:00')]
        bars=feed.parse_minutes('var _('+json.dumps(rows)+');')
        self.assertEqual([datetime.fromtimestamp(b['t'],feed.BJ).strftime('%H:%M') for b in bars],['11:15','13:00'])
        self.assertEqual(bars[1]['v'],123)

    def test_quote_uses_actual_provider_limits_and_event_time(self):
        fields=['']*49
        for i,value in {1:'测试股票',2:'600519',3:'101',4:'100',30:'20261030101530',33:'103',34:'99',47:'110',48:'90'}.items():
            fields[i]=value
        with patch.object(feed,'_read',return_value='v_sh600519="'+'~'.join(fields)+'";'):
            quote=feed.fetch_quote('600519')
        self.assertEqual(quote['limits'],(110,90))
        self.assertEqual(quote['time'].hour,10)

    def test_unsupported_instruments_do_not_enter_us_or_generic_stock_rules(self):
        for code in ('510300','920748','900001'):
            with self.subTest(code=code),self.assertRaises(ValueError):
                feed.exchange_symbol(code)
