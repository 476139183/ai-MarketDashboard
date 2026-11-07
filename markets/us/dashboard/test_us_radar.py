import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from us_radar import session_of, stock_bars, analyze_stock, load_stock, massive_rows, USRadar
NY=ZoneInfo('America/New_York')
def stamp(text): return int(datetime.fromisoformat(text).replace(tzinfo=NY).timestamp()*1000)
def row(text, price=10): return dict(t=stamp(text)//1000,o=price,h=price+1,l=price-1,c=price+.5,v=100)

class USRadarTests(unittest.TestCase):
    def test_sessions_and_dst(self):
        for clock,expected in [('04:00','pre'),('09:29','pre'),('09:30','regular'),('16:00','post'),('20:00','closed')]:
            self.assertEqual(session_of(stamp('2026-11-06T'+clock)),expected)
        self.assertEqual(session_of(stamp('2026-11-07T10:00')),'closed')
        self.assertEqual(session_of(stamp('2026-07-06T09:30')),'regular')

    def test_ten_minute_session_alignment_and_incomplete_rejected(self):
        rows=[row('2026-11-06T09:30'),row('2026-11-06T09:35',12),row('2026-11-06T09:40')]
        bars=stock_bars(rows,5,10,stamp('2026-11-06T10:00'))
        self.assertEqual(len(bars),1)
        self.assertEqual(bars[0]['time'],stamp('2026-11-06T09:30'))
        self.assertEqual(bars[0]['volume'],200)
        self.assertEqual(stock_bars(rows[1:],5,10,stamp('2026-11-06T10:00')),[])

    def test_stale_and_sampled_data_cannot_be_actionable(self):
        now=stamp('2026-11-06T11:00')
        result=analyze_stock('NVDA',[],{},'Nasdaq sampled',15,'all',now,sampled=True)
        self.assertFalse(result['valid'])
        self.assertEqual(result['direction'],'observe')
        self.assertEqual(result['coverage']['pre']['count'],0)

    def test_yahoo_failure_falls_back_to_nasdaq_without_faking_candles(self):
        with patch('us_radar.engine.fetch_chart',side_effect=RuntimeError('429')), \
             patch('us_radar.engine.fetch_nasdaq_quote',return_value={'price':186.64,'ts':'Nov 6, 2026','market_status':'Closed'}), \
             patch('us_radar.engine.fetch_nasdaq_intraday_points',return_value=([{'t':stamp('2026-11-06T15:59')//1000,'c':186.64}],{})), \
             patch.dict('os.environ',{},clear=True):
            result=load_stock('NVDA',15,'all','yahoo',stamp('2026-11-07T10:00'))
        self.assertEqual(result['price'],186.64)
        self.assertEqual(result['candles'],[])
        self.assertTrue(result['sampled'])
        self.assertIsNone(result['quote_at'])
        self.assertIn('Yahoo',result['warning'])

    def test_sixty_minute_anchor_drops_last_half_hour_and_never_mixes_sessions(self):
        rows=[row('2026-11-06T09:30'),row('2026-11-06T10:00'),
              row('2026-11-06T15:30'),row('2026-11-06T16:00'),row('2026-11-06T16:30')]
        bars=stock_bars(rows,30,60,stamp('2026-11-06T18:00'))
        self.assertEqual([b['time'] for b in bars],[stamp('2026-11-06T09:30'),stamp('2026-11-06T16:00')])
        self.assertEqual([b['session'] for b in bars],['regular','post'])

    def test_massive_auth_is_header_only_and_real_ohlcv_is_preserved(self):
        raw={'t':stamp('2026-11-06T09:30'),'o':10,'h':12,'l':9,'c':11,'v':300}
        with patch.dict('os.environ',{'MASSIVE_API_KEY':'test-secret'}), \
             patch('us_radar.engine._http_json',return_value={'results':[raw],'status':'OK'}) as request:
            rows=massive_rows('NVDA',5,stamp('2026-11-06T12:00'))
            self.assertEqual(rows[0]['v'],300)
            self.assertNotIn('test-secret',request.call_args.args[0])
            self.assertEqual(request.call_args.kwargs['extra_headers']['Authorization'],'Bearer test-secret')

    def test_repeated_page_polls_do_not_restart_upstream_jobs(self):
        service=USRadar()
        with patch('us_radar.threading.Thread') as worker:
            one=service.payload(['NVDA'],15,'all','nasdaq')
            two=service.payload(['NVDA'],15,'all','nasdaq')
            self.assertEqual(one['status'],'running')
            self.assertEqual(two['status'],'running')
            worker.assert_called_once()
        for minutes in ('240','2'):
            with self.assertRaises(ValueError):service.payload(['NVDA'],minutes)

    def test_cached_signal_expires_at_session_boundary_without_new_collection(self):
        now=stamp('2026-11-06T16:00')
        service=USRadar()
        key=(('NVDA',),15,'all','nasdaq')
        item=analyze_stock('NVDA',[],{},'test',15,'all',now)
        item.update(valid=True,valid_until=now,quote_at=now-1000,direction='bullish',label='看涨倾向')
        service.states[key]=dict(items=[item],generated_at=now-1000,status='idle')
        with patch('us_radar.time.time',return_value=now/1000):
            result=service.payload(['NVDA'],15,'all','nasdaq')
        self.assertFalse(result['items'][0]['valid'])
        self.assertEqual(result['items'][0]['direction'],'observe')
        self.assertTrue(service.states[key]['items'][0]['valid'])

    def test_auto_uses_real_sina_volume_and_labels_bar_reference_without_quote(self):
        rows=[row('2026-11-06T09:30'),row('2026-11-06T09:35')]
        with patch.dict('os.environ',{},clear=True),patch('us_radar.engine.fetch_nasdaq_quote',return_value=None), \
             patch('us_radar.sina_rows',return_value=rows):
            result=load_stock('SNDK',5,'all','auto',stamp('2026-11-06T09:37'))
        self.assertEqual(result['chart_candles'][-1]['volume'],100)
        self.assertFalse(result['chart_candles'][-1]['complete'])
        self.assertFalse(result['sampled'])
        self.assertIn('新浪',result['quote_source'])
        self.assertIsNone(result['quote_at'])

if __name__=='__main__':unittest.main()
