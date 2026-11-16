import base64
import unittest
from unittest.mock import patch
from monitor_server import authorized, redact_stale, merge_quotes, fetch_quotes
from datetime import datetime, timedelta, timezone

class MonitorServerTests(unittest.TestCase):
    def test_quote_batch_preserves_other_symbols_on_failure(self):
        def fetch(symbol,force=False):
            if symbol=='MU':raise RuntimeError('offline')
            return {'price':1}
        with patch('monitor_server.fetch_nasdaq_quote',side_effect=fetch):
            result=fetch_quotes(['SNDK','MU','NVDA'])
        self.assertEqual(set(result),{'SNDK','NVDA'})

    def test_basic_auth_checks_password(self):
        encode=lambda text:'Basic '+base64.b64encode(text.encode()).decode()
        self.assertTrue(authorized(encode('viewer:secret'), 'secret'))
        self.assertFalse(authorized(encode('viewer:wrong'), 'secret'))
        self.assertFalse(authorized(None,'secret'))

    def test_fresh_quote_replaces_prior_analysis_price(self):
        now=datetime(2026,10,29,8,22,tzinfo=timezone.utc)
        data={'items':[{'symbol':'SNDK','current_price':10,'signal':'观望'}]}
        quote={'SNDK':{'price':12,'ts':'Oct 29, 2026 4:22 AM ET','market_status':'Pre-Market'}}
        out=merge_quotes(data,quote,now)
        self.assertEqual(out['items'][0]['current_price'],12)
        self.assertTrue(out['items'][0]['quote_fresh'])
        self.assertEqual(out['items'][0]['signal'],'观望')

    def test_previous_close_uses_price_minus_change(self):
        now=datetime(2026,10,29,8,22,tzinfo=timezone.utc)
        data={'items':[{'symbol':'MU'}]}
        quotes={'MU':{'price':948.85,'net_change':-34.38,'prev_close':949.982,'ts':'Oct 29, 2026 4:22 AM ET','market_status':'Pre-Market'}}
        result=merge_quotes(data,quotes,now)['items'][0]
        self.assertAlmostEqual(result['previous_close'],983.23)
        self.assertEqual(result['phase'],'盘前')

    def test_old_quote_is_not_shown_as_live(self):
        now=datetime(2026,10,29,8,22,tzinfo=timezone.utc)
        data={'items':[{'symbol':'SNDK','current_price':10}]}
        quote={'SNDK':{'price':12,'ts':'Oct 28, 2026 7:59 PM ET','market_status':'Pre-Market'}}
        out=merge_quotes(data,quote,now)
        self.assertIsNone(out['items'][0]['current_price'])

    def test_old_report_hides_price(self):
        now=datetime(2026,10,29,8,30,tzinfo=timezone.utc)
        data={'generated_bj':(now-timedelta(minutes=30)).isoformat(), 'items':[{'symbol':'SNDK','current_price':12,'signal':'做多','sample_high':99,'sample_low':90,'direction_sample':{'direction':'偏多'}}]}
        out=redact_stale(data,now)
        self.assertFalse(out['fresh'])
        self.assertIsNone(out['items'][0]['current_price'])
        self.assertEqual(out['items'][0]['signal'],'观望')
        self.assertIsNone(out['items'][0]['sample_high'])
        self.assertIsNone(out['items'][0]['sample_low'])
        self.assertIsNone(out['items'][0]['direction_sample'])

if __name__=='__main__':unittest.main()
