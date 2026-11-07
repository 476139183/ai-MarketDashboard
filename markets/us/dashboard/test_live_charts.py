import unittest
from unittest.mock import patch
import contract_radar
import us_radar
from radar_periods import aggregate_rows
from okx_contracts import OKXFeed
from binance_contracts import BinanceFeed
from test_us_radar import stamp, row

class LiveChartTests(unittest.TestCase):
    def test_partial_ten_minute_bar_has_real_volume_but_never_enters_signal(self):
        rows=[[0,10,12,9,11,3,33],[300000,11,14,10,13,4,52],[600000,13,15,12,14,5,70]]
        bars=aggregate_rows(rows,source_minutes=5,target_minutes=10,now=720000,include_open=True)
        self.assertEqual(bars[-1],[600000,13,15,12,14,5,70])
        self.assertEqual(len(aggregate_rows(rows,source_minutes=5,target_minutes=10,now=720000)),1)
        self.assertEqual(aggregate_rows(rows[1:],source_minutes=5,target_minutes=10,now=720000,include_open=True)[0][0],600000)

    def test_contract_chart_open_candle_changes_without_changing_closed_signal(self):
        now=1800000000000//60000*60000+20000
        boundary=now//60000*60000
        raw=[[boundary-(70-i)*60000,100+i,102+i,99+i,101+i,10,1000] for i in range(70)]
        quote={'lastPr':171,'ts':now}
        closed=contract_radar.analyze_contract('BTCUSDT','BTC','crypto',quote,raw,now,minutes=1)
        live=contract_radar.analyze_contract('BTCUSDT','BTC','crypto',quote,raw+[[boundary,171,181,170,180,25,4500]],now,minutes=1)
        self.assertEqual(live['direction'],closed['direction'])
        self.assertEqual(live['ema20'],closed['ema20'])
        self.assertEqual(live['chart_candles'][-1]['volume'],25)
        self.assertFalse(live['chart_candles'][-1]['complete'])
        self.assertEqual(live['candles'][-1]['time'],boundary-60000)

    def test_stock_chart_contains_current_candle_and_signal_bars_stay_closed(self):
        rows=[row('2026-11-06T09:30'),row('2026-11-06T09:35')]
        now=stamp('2026-11-06T09:37')
        live=us_radar.stock_bars(rows,5,10,now,include_open=True)
        self.assertEqual(live[-1]['volume'],200)
        self.assertFalse(live[-1]['complete'])
        self.assertEqual(us_radar.stock_bars(rows,5,10,now),[])

    def test_okx_open_bars_are_opt_in_and_never_leak_to_closed_feed(self):
        rows=[['0','10','12','9','11','50','2','22','1'],['60000','11','13','10','12','20','1','12','0']]
        feed=OKXFeed(lambda path,params:rows)
        self.assertEqual(len(feed('candles',{'symbol':'BTCUSDT'})),1)
        self.assertEqual(len(feed('candles',{'symbol':'BTCUSDT','include_open':True})),2)

    def test_binance_open_bars_are_opt_in(self):
        rows=[[0,'10','12','9','11','2',59999,'22'],[60000,'11','13','10','12','1',119999,'12']]
        feed=BinanceFeed(lambda path,params:rows)
        with patch('binance_contracts.time.time',return_value=80):
            self.assertEqual(len(feed('candles',{'symbol':'BTCUSDT'})),1)
            self.assertEqual(len(feed('candles',{'symbol':'BTCUSDT','include_open':True})),2)

    def test_unconfirmed_old_okx_bar_is_not_used_as_closed_structure(self):
        now=1800000000000//60000*60000+20000
        boundary=now//60000*60000
        raw=[[boundary-(70-i)*60000,100+i,102+i,99+i,101+i,10,1000,True] for i in range(70)]
        raw[-1][-1]=False
        item=contract_radar.analyze_contract('BTCUSDT','BTC','crypto',{'lastPr':171,'ts':now},raw,now,minutes=1)
        self.assertFalse(item['structure_fresh'])

if __name__=='__main__':unittest.main()
