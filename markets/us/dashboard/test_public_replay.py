import sqlite3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
import replay_market_states as replay


class PublicReplayTests(unittest.TestCase):
    def test_explicit_source_uses_only_selected_feed_and_reports_missing_warmup(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        conn.execute('create table bars(symbol text,source text,session text,trading_date text,timestamp integer,open real,high real,low real,close real,volume real)')
        start=int(datetime(2020,1,2,9,30,tzinfo=ZoneInfo('America/New_York')).timestamp())
        for source,day in [('HF public OHLCV-1m','2020-01-02'),('Massive historical aggregates','2020-01-03')]:
            for i in range(15):conn.execute('insert into bars values(?,?,?,?,?,?,?,?,?,?)',('AAPL',source,'regular',day,start+i*60,100,101,99,100,500))
        result=replay.replay_stock(conn,'AAPL','2020-01-01',source='HF public OHLCV-1m')
        self.assertEqual(result['days'],1)
        self.assertEqual(result['source'],'HF public OHLCV-1m')
        self.assertEqual(result['daily_warmup_ready_days'],0)
        self.assertEqual(result['summary']['trades'],0)
        conn.close()

    def test_aggregation_preserves_feed_and_omits_incomplete_bucket(self):
        rows=[dict(timestamp=900+i*60,open=100,high=101,low=99,close=100,volume=500) for i in range(15)]
        result=replay.aggregate(rows,feed='HF public OHLCV-1m')
        self.assertEqual(result[0]['feed'],'HF public OHLCV-1m')
        self.assertEqual(replay.aggregate(rows[:-1],feed='HF public OHLCV-1m'),[])

    def test_disjoint_months_reset_daily_and_volume_warmup(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        conn.execute('create table bars(symbol text,source text,session text,trading_date text,timestamp integer,open real,high real,low real,close real,volume real)')
        for day in ('1992-01-02','2010-01-04'):
            start=int(datetime.fromisoformat(day).replace(hour=9,minute=30,tzinfo=ZoneInfo('America/New_York')).timestamp())
            conn.executemany('insert into bars values(?,?,?,?,?,?,?,?,?,?)',[('AAPL','HF public OHLCV-1m','regular',day,start+i*60,100,101,99,100,500) for i in range(15)])
        result=replay.replay_stock(conn,'AAPL','1992-01-01',source='HF public OHLCV-1m')
        self.assertEqual(result['history_gap_resets'],1)
        self.assertEqual(result['daily_warmup_ready_days'],0)
        conn.close()
