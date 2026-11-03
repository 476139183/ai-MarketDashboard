import io
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone


class PublicHistoryTests(unittest.TestCase):
    def module(self):
        import public_history
        return public_history

    def test_finmind_returns_raw_prices_and_separate_adjusted_close(self):
        m=self.module()
        payload={'status':200,'data':[{'date':'2020-01-02','stock_id':'AAPL','Open':100,'High':110,
                    'Low':90,'Close':105,'Volume':1000,'Adj_Close':26.25}]}
        with patch.object(m,'get_json',return_value=payload) as get:
            bars=m.finmind_daily('AAPL','2020-01-01','2020-01-03')
        self.assertEqual(bars[0]['close'],105)
        self.assertEqual(bars[0]['adjusted_close'],26.25)
        self.assertEqual(bars[0]['open'],100)
        self.assertIn('USStockPrice',get.call_args.args[0])
        self.assertNotIn('token',get.call_args.args[0].lower())

    def test_finmind_wrong_symbol_invalid_ohlc_and_error_status_are_rejected(self):
        m=self.module()
        raw={'date':'2020-01-02','stock_id':'AAPL','Open':100,'High':110,'Low':90,'Close':105,'Volume':1000,'Adj_Close':100}
        cases=[{'status':402,'data':[]},{'status':200,'data':[dict(raw,stock_id='MU')]},
               {'status':200,'data':[dict(raw,High=80)]},{'status':200,'data':[dict(raw,Volume=-1)]},
               {'status':200,'data':[raw,raw]},{'status':200,'data':[dict(raw,date='2021-01-01')]}]
        for payload in cases:
            with self.subTest(payload=payload),patch.object(m,'get_json',return_value=payload):
                with self.assertRaises(ValueError):m.finmind_daily('AAPL','2020-01-01','2020-01-03')

    def test_minute_import_is_atomic_and_does_not_fill_missing_minutes(self):
        m=self.module();stamp=datetime(2020,1,2,14,30,tzinfo=timezone.utc)
        raw=dict(timestamp=stamp,ticker='AAPL',open=100,high=102,low=99,close=101,volume=200)
        later={**raw,'timestamp':stamp.replace(minute=32)}
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'history.sqlite3'
            result=m.import_minutes(db,[raw,later],symbols=['AAPL'],source=m.HF_SOURCE,month='2020-01')
            self.assertEqual(result['stored'],2)
            with closing(sqlite3.connect(db)) as c:self.assertEqual(c.execute('select count(*) from bars').fetchone()[0],2)
            audit=m.audit_history(db)
            self.assertEqual(audit['minute'][0]['complete_regular_days'],0)
            self.assertEqual(audit['minute'][0]['missing_regular_slots'],388)
            with self.assertRaises(ValueError):
                m.import_minutes(db,[{**raw,'close':100},{**later,'volume':-5}],symbols=['AAPL'],source=m.HF_SOURCE,month='2020-01')
            with closing(sqlite3.connect(db)) as c:self.assertEqual(c.execute('select close from bars order by timestamp limit 1').fetchone()[0],101)

    def test_minute_timezone_duplicates_and_wrong_month_rejected(self):
        m=self.module();stamp=datetime(2020,1,2,14,30,tzinfo=timezone.utc)
        raw=dict(timestamp=stamp,ticker='AAPL',open=100,high=102,low=99,close=101,volume=200)
        with tempfile.TemporaryDirectory() as folder:
            for rows in ([raw,raw],[{**raw,'timestamp':stamp.replace(tzinfo=None)}],[{**raw,'timestamp':stamp.replace(month=2)}]):
                with self.assertRaises(ValueError):m.import_minutes(Path(folder)/'x.sqlite3',rows,symbols=['AAPL'],source=m.HF_SOURCE,month='2020-01')

    def test_daily_store_and_minute_sources_remain_separate(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'h.sqlite3'
            m.store_daily(db,'AAPL',[dict(date='2020-01-02',open=100,high=102,low=99,close=101,volume=200,adjusted_close=25)],m.FINMIND_SOURCE)
            raw=dict(timestamp=datetime(2020,1,2,14,30,tzinfo=timezone.utc),ticker='AAPL',open=100,high=102,low=99,close=101,volume=200)
            m.import_minutes(db,[raw],symbols=['AAPL'],source=m.HF_SOURCE,month='2020-01')
            report=m.audit_history(db)
            self.assertEqual(report['daily'][0]['rows'],1)
            self.assertEqual(report['daily'][0]['source'],m.FINMIND_SOURCE)
            self.assertEqual(report['minute'][0]['source'],m.HF_SOURCE)

    def test_download_budget_rejects_before_network_and_hash_mismatch_cleans_partial(self):
        m=self.module()
        entry={'path':'data/ohlcv_2020-01.parquet','size':10,'lfs':{'oid':'0'*64}}
        with tempfile.TemporaryDirectory() as folder,patch.object(m,'urlopen',return_value=io.BytesIO(b'1234567890')) as open_:
            with self.assertRaisesRegex(ValueError,'预算'):
                m.download_month(entry,Path(folder),'a'*40,max_bytes=5)
            open_.assert_not_called()
            with self.assertRaisesRegex(ValueError,'SHA256'):
                m.download_month(entry,Path(folder),'a'*40,max_bytes=20)
            self.assertFalse(list(Path(folder).glob('*.partial')))

    def test_daily_study_uses_only_past_bars_and_keeps_horizons_separate(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'h.sqlite3'
            rows=[];day=datetime(2020,1,1)
            while len(rows)<85:
                if day.weekday()<5:
                    close=100+len(rows)
                    rows.append(dict(date=day.date().isoformat(),open=close,high=close+1,low=close-1,close=close,volume=1000,adjusted_close=close))
                day+=__import__('datetime').timedelta(days=1)
            m.store_daily(db,'AAPL',rows)
            report=m.study_daily(db,horizons=[5,20])
            five=next(row for row in report['groups'] if row['horizon']==5 and row['bias']=='up')
            twenty=next(row for row in report['groups'] if row['horizon']==20 and row['bias']=='up')
            self.assertEqual(five['observations'],21)
            self.assertEqual(twenty['observations'],6)
            self.assertEqual(five['directional_hit_pct'],100)

    def test_off_session_rows_are_counted_and_never_mixed_into_regular_minutes(self):
        m=self.module()
        raw=dict(timestamp=datetime(2010,1,7,1,1,tzinfo=timezone.utc),ticker='AAPL',open=100,high=102,low=99,close=101,volume=200)
        with tempfile.TemporaryDirectory() as folder:
            result=m.import_minutes(Path(folder)/'h.sqlite3',[raw],symbols=['AAPL'],source=m.HF_SOURCE,month='2010-01')
            self.assertEqual(result['off_session_rows'],1)
            report=m.audit_history(Path(folder)/'h.sqlite3')
            self.assertEqual(report['minute'],[])
            self.assertEqual(report['off_session_rows'],1)

    def test_parquet_filter_keeps_only_selected_stocks(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest('Parquet reader requires the optional research environment')
        m=self.module()
        row=dict(timestamp=datetime(2020,1,2,14,30,tzinfo=timezone.utc),ticker='AAPL',open=100.,high=102.,low=99.,close=101.,volume=200.)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sample.parquet'
            pq.write_table(pa.Table.from_pylist([row,{**row,'ticker':'MSFT'}]),path)
            result=list(m.parquet_rows(path,['AAPL']))
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['ticker'],'AAPL')
            self.assertIsNotNone(result[0]['timestamp'].tzinfo)
