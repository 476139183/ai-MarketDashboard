import json
import threading
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

import futures_cn as F

BJ = ZoneInfo('Asia/Shanghai')
NOW = datetime(2026, 11, 8, 12, tzinfo=BJ).timestamp() * 1000


def quote(symbol='RB0', price='3046', date='2026-11-06', clock='230000'):
    fields = ['螺纹钢连续', clock, '3045', '3051', '3036', '0', '3045', '3046', price,
              '0', '3041', '101', '384', '1674031', '217101', '沪', '螺纹钢', date, '1']
    return f'var hq_str_nf_{symbol}="'+','.join(fields)+'";'


class FuturesTests(unittest.TestCase):
    def test_quote_uses_previous_settlement_and_source_date(self):
        row = F.parse_quotes(quote(), ['RB0'], NOW)[0]
        self.assertAlmostEqual(row['change_pct'], (3046/3041-1)*100)
        self.assertEqual(row['open_interest'], 1674031)
        self.assertEqual(row['quote_at'], datetime(2026,11,6,23,tzinfo=BJ).timestamp()*1000)
        self.assertFalse(row['quote_fresh'])
        self.assertTrue(row['continuous'])

    def test_empty_or_invalid_quote_never_fabricates_price(self):
        for raw in ['var hq_str_nf_RB0="";', quote(price='NaN'), quote(price='0')]:
            with self.subTest(raw=raw):
                row=F.parse_quotes(raw,['RB0'],NOW)[0]
                self.assertIsNone(row['price'])
                self.assertTrue(row['error'])

    def test_bars_keep_source_end_time_and_night_date(self):
        raw=[dict(d='2026-11-07 00:15:00',o='10',h='12',l='9',c='11',v='3',p='50')]
        row=F.parse_bars('/* comment */=('+json.dumps(raw)+');','15',NOW)[0]
        self.assertEqual(row['time'],datetime(2026,11,7,0,15,tzinfo=BJ).timestamp()*1000)
        self.assertEqual(row['open_interest'],50)
        self.assertTrue(row['complete'])

    def test_daily_today_and_future_not_confirmed(self):
        raw=[dict(d=d,o='10',h='12',l='9',c='11',v='3',p='50') for d in ('2026-11-06','2026-11-08','2026-11-09')]
        rows=F.parse_bars('=('+json.dumps(raw)+');','day',NOW)
        self.assertEqual([b['complete'] for b in rows],[True,False,False])

    def test_invalid_ohlc_and_conflicting_duplicates_rejected(self):
        one=dict(d='2026-11-06',o='10',h='12',l='9',c='11',v='3',p='50')
        for rows in [[dict(one,c='NaN')],[dict(one,h='8')],[dict(one,v='-1')],[one,dict(one,c='10')]]:
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                F.parse_bars('=('+json.dumps(rows)+');','day',NOW)

    def test_missing_volume_and_oi_remain_unknown(self):
        row=F.parse_bars('=('+json.dumps([dict(d='2026-11-06',o='10',h='12',l='9',c='11')])+');','day',NOW)[0]
        self.assertIsNone(row['volume']); self.assertIsNone(row['open_interest'])

    def test_out_of_window_historical_corruption_does_not_block_current_chart(self):
        old=dict(d='2006-07-04',o='2302',h='2334',l='2316',c='2320',v='145992',p='267702')
        rows=[dict(d=str((datetime(2025,1,1)+timedelta(days=i)).date()),o='10',h='12',l='9',c='11') for i in range(300)]
        parsed=F.parse_bars('=('+json.dumps([old]+rows)+');','day',NOW)
        self.assertEqual(len(parsed),300)

    def test_analysis_ignores_forming_bar_and_describes_oi_without_inventing_flow(self):
        bars=[dict(time=i,open=100+i,high=102+i,low=99+i,close=101+i,volume=100,open_interest=200+i,complete=True) for i in range(65)]
        base=F.analyze(bars)
        with_open=F.analyze(bars+[dict(bars[-1],time=100,close=99999,complete=False)])
        self.assertEqual(base,with_open)
        self.assertEqual(base['trend'],'strong');self.assertEqual(base['oi_change'],1)
        self.assertEqual(base['volume_ratio'],1)
        self.assertNotIn('资金流入',' '.join(base['evidence']))
        self.assertEqual(F.analyze([])['trend'],'insufficient')

    def test_radar_isolates_failed_contracts_and_preserves_analysis_dates(self):
        def chart(symbol, period, *, include_quote):
            self.assertFalse(include_quote)
            if symbol == 'AU0':
                raise OSError('source offline')
            return dict(F.identity(symbol), analysis=dict(trend='strong', label='历史结构偏强',
                        asof=123, count=60, volume_ratio=1.2, oi_change=10), candles=[])
        quotes = [dict(F.identity(s), price=3000, quote_at=NOW, error=None) for s in F.SYMBOLS]
        with patch.object(F, 'fetch_quotes', return_value=quotes), patch.object(F, 'fetch_chart', side_effect=chart):
            result = F.fetch_radar('day')
        self.assertEqual(len(result['items']), len(F.SYMBOLS))
        rows = {r['symbol']: r for r in result['items']}
        self.assertEqual(rows['RB0']['analysis']['asof'], 123)
        self.assertEqual(rows['RB0']['price'], 3000)
        self.assertIn('source offline', rows['AU0']['analysis_error'])
        self.assertNotEqual((rows['AU0'].get('analysis') or {}).get('trend'), 'strong')
        self.assertNotIn('candles', rows['RB0'])

    def test_radar_quote_failure_does_not_hide_historical_structure(self):
        with patch.object(F, 'fetch_quotes', side_effect=OSError('quotes offline')), patch.object(F, 'fetch_chart', return_value={'analysis': {'trend': 'mixed', 'asof': 123}}):
            result = F.fetch_radar('15')
        self.assertEqual(len(result['items']), len(F.SYMBOLS))
        self.assertIsNone(result['items'][0]['price'])
        self.assertIn('quotes offline', result['items'][0]['error'])
        self.assertEqual(result['items'][0]['analysis']['trend'], 'mixed')

    def test_symbol_and_period_validation(self):
        self.assertEqual(F.validate_symbol('rb2701'),'RB2701')
        for symbol in ('IF0','BTCUSDT','../../config','RB0&x=1','XX0'):
            with self.subTest(symbol=symbol),self.assertRaises(ValueError):F.validate_symbol(symbol)
        with self.assertRaises(ValueError):F.validate_period('10')

    def test_cache_nonblocking_deduplicates_fetch_and_keeps_old_data_on_error(self):
        gate=threading.Event();calls=[]
        def fetch():calls.append(1);gate.wait(1);return {'items':[1]}
        service=F.FuturesService()
        try:
            a=service.cached('quotes',fetch,now=0)
            service.cached('quotes',fetch,now=0)
            self.assertEqual(a['status'],'loading');gate.set()
            for _ in range(100):
                result=service.cached('quotes',fetch,now=1)
                if result.get('items'):break
                time.sleep(.005)
            self.assertEqual(result['items'],[1]);self.assertEqual(len(calls),1)
            def fail():raise OSError('offline')
            service.cached('quotes',fail,now=100)
            for _ in range(100):
                result=service.cached('quotes',fail,now=101)
                if result.get('error'):break
                time.sleep(.005)
            self.assertEqual(result['items'],[1]);self.assertTrue(result['stale']);self.assertIn('offline',result['error'])
        finally:service.pool.shutdown(wait=True)

    def test_abandoned_completed_queries_release_capacity_for_next_symbol(self):
        service=F.FuturesService();gate=threading.Event()
        try:
            def fetch():gate.wait(1);return {'ok':True}
            for i in range(4):service.cached(str(i),fetch,now=0)
            gate.set()
            for _ in range(100):
                if all(v['future'].done() for v in service.cache.values()):break
                time.sleep(.005)
            service.cached('next',lambda:{'ok':True},now=1)
            self.assertIsNotNone(service.cache['next']['future'])
        finally:gate.set();service.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
