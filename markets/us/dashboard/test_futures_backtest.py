import unittest
import threading
import time
import math
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
import futures_backtest as B


def bars(prices):
    start=datetime(2026,1,1,tzinfo=ZoneInfo('Asia/Shanghai'))
    return [dict(time=int((start+timedelta(days=i)).timestamp()*1000),open=p,high=p+2,low=p-2,close=p,volume=1000,open_interest=1000,complete=True) for i,p in enumerate(prices)]


def config(**kw):
    return B.normalize_config(dict(symbol='RB2701',capital=100000,lots=1,multiplier=10,tick_size=1,
                                  margin_rate=.15,max_margin_fraction=.5,fee_fixed=3,fee_rate=0,
                                  close_today_factor=2,slippage_ticks=1,**kw))


class BacktestTests(unittest.TestCase):
    def test_continuous_unknown_and_bad_parameters_rejected(self):
        for params in [dict(symbol='RB0'),dict(symbol='RB701'),dict(lots=1.5),dict(capital=float('nan')),
                       dict(fee_fixed=-1),dict(margin_rate=0),dict(start='2026-12-01',end='2026-01-01')]:
            with self.subTest(params=params),self.assertRaises(ValueError):B.normalize_config(params)

    def test_signal_no_future_and_donchian_excludes_current_high(self):
        data=bars([100]*60+[110])
        self.assertEqual(B.signal('donchian',data,0),1)
        self.assertEqual(B.signal('sma',bars(list(range(100,161))),0),1)
        self.assertEqual(B.signal('sma',bars(list(range(160,99,-1))),0),-1)

    def test_next_open_fills_fees_multiplier_and_slippage_once(self):
        data=bars([100]*60+[110,120,130,140])
        with patch.object(B,'signal',return_value=1):r=B.replay(data,config(),'sma')
        trade=r['trades'][0]
        self.assertEqual(trade['entry_at'],data[60]['time'])
        self.assertLess(trade['signal_at'],trade['entry_at'])
        self.assertEqual(trade['entry_price'],111)
        self.assertEqual(trade['exit_price'],139)
        self.assertEqual(trade['net_pnl'],274)
        self.assertEqual(r['summary']['end_equity'],100274)
        self.assertEqual(r['summary']['fees'],6)
        self.assertEqual(r['summary']['slippage_cost'],20)

    def test_short_pnl_and_funding_not_borrowed_from_crypto(self):
        data=bars([150]*60+[140,130,120,110])
        with patch.object(B,'signal',return_value=-1):r=B.replay(data,config(),'sma')
        t=r['trades'][0]
        self.assertEqual(t['net_pnl'],274)
        self.assertEqual(t['direction'],'short')

    def test_gap_stop_uses_worse_open_and_same_day_fee_factor(self):
        data=bars([100]*60+[110,80,90,90])
        with patch.object(B,'signal',return_value=1):r=B.replay(data,config(),'sma')
        t=r['trades'][0]
        self.assertEqual(t['reason'],'gap_stop');self.assertEqual(t['exit_price'],79)
        one=bars([100]*60+[110,110,110])
        one[60].update(low=90)
        with patch.object(B,'signal',return_value=1):r=B.replay(one,config(),'sma')
        self.assertTrue(r['trades'][0]['close_today'])
        self.assertEqual(r['trades'][0]['exit_fee'],6)

    def test_margin_does_not_reduce_equity_but_can_reject_orders(self):
        data=bars([100]*64)
        with patch.object(B,'signal',return_value=1):r=B.replay(data,config(),'sma')
        self.assertEqual(r['summary']['end_equity'],99974)
        small=B.normalize_config(dict(config(),capital=100, max_margin_fraction=.1))
        with patch.object(B,'signal',return_value=1):r=B.replay(data,small,'sma')
        self.assertEqual(r['summary']['trade_count'],0)
        self.assertGreater(r['summary']['rejected_entries'],0)

    def test_zero_volume_and_flat_bar_cannot_be_filled(self):
        data=bars([100]*64);data[60]['volume']=0
        data[61].update(open=100,high=100,low=100,close=100)
        with patch.object(B,'signal',return_value=1):r=B.replay(data,config(),'sma')
        self.assertEqual(r['trades'][0]['entry_at'],data[62]['time'])

    def test_forming_bars_ignored_and_no_result_for_too_short_sample(self):
        data=bars([100]*60+[110,120,130,140]);bad=dict(data[-1],time=data[-1]['time']+86400000,close=999999,complete=False)
        with patch.object(B,'signal',return_value=1):
            self.assertEqual(B.replay(data,config(),'sma'),B.replay(data+[bad],config(),'sma'))
        with self.assertRaises(ValueError):B.compare(bars([100]*65),config())

    def test_comparison_fixed_order_cost_stress_and_tail_start_empty(self):
        data=bars([100+i*.2 for i in range(180)])
        r=B.compare(data,config())
        self.assertEqual([x['strategy'] for x in r['results']],['sma','donchian'])
        for item in r['results']:
            self.assertGreater(item['tail']['summary']['start_at'],item['summary']['start_at'])
            self.assertEqual(item['tail']['summary']['initial_capital'],100000)
            self.assertAlmostEqual(item['summary']['end_equity']-100000,sum(t['net_pnl'] for t in item['trades']))

    def test_cost_stress_doubles_cost_components_without_mutating_inputs(self):
        data=bars([100]*60+[110,120,130,140]);cfg=config()
        with patch.object(B,'signal',return_value=1):
            base=B.replay(data,cfg,'sma');stress=B.replay(data,cfg,'sma',cost_scale=2)
        self.assertEqual(base['summary']['fees']*2,stress['summary']['fees'])
        self.assertEqual(base['summary']['slippage_cost']*2,stress['summary']['slippage_cost'])
        self.assertEqual(cfg['slippage_ticks'],1)

    def test_background_job_is_explicit_and_errors_do_not_turn_into_empty_success(self):
        gate=threading.Event();calls=[]
        def fetch(symbol):calls.append(symbol);gate.wait(1);raise OSError('source unavailable')
        jobs=B.BacktestJobs(fetch)
        try:
            self.assertEqual(calls,[])
            job=jobs.start(config());self.assertEqual(job['status'],'running');gate.set()
            for _ in range(100):
                state=jobs.snapshot(job['id'])
                if state['status']!='running':break
                time.sleep(.005)
            self.assertEqual(state['status'],'failed');self.assertIn('source unavailable',state['error'])
            with self.assertRaises(KeyError):jobs.snapshot('missing')
        finally:gate.set();jobs.pool.shutdown(wait=True)

    def test_expiry_month_excluded_and_date_range_retains_only_prior_warmup(self):
        data=bars(list(range(100,280)))
        cfg=B.normalize_config(dict(config(),symbol='RB2604',start='2026-03-10'))
        with patch.object(B,'signal',return_value=1):r=B.replay(data,cfg,'sma')
        self.assertGreaterEqual(r['summary']['start_at'],data[68]['time'])
        self.assertLess(r['summary']['end_at'],int(datetime(2026,4,1,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000))

    def test_future_changes_do_not_rewrite_earlier_completed_trades(self):
        data=bars([100+15*math.sin(i/5) for i in range(180)])
        modified=[dict(b) for b in data]
        for b in modified[140:]:
            for key in ('open','high','low','close'):b[key]+=50
        for strategy in B.STRATEGIES:
            a=B.replay(data,config(),strategy);b=B.replay(modified,config(),strategy)
            before=data[140]['time']
            self.assertEqual([t for t in a['trades'] if t['exit_at']<before],[t for t in b['trades'] if t['exit_at']<before])

    def test_margin_breach_on_locked_bar_exits_at_next_tradable_open_and_stops(self):
        data=bars([100]*60+[100,50,52,53]);data[61].update(open=50,high=50,low=50,close=50)
        cfg=B.normalize_config(dict(config(),capital=600,margin_rate=.5,max_margin_fraction=1))
        with patch.object(B,'signal',return_value=1):r=B.replay(data,cfg,'sma')
        self.assertTrue(r['summary']['margin_halted']);self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['trades'][0]['reason'],'margin_exit')
        self.assertEqual(r['trades'][0]['exit_at'],data[62]['time'])
        self.assertEqual(r['summary']['end_equity'],94)


if __name__=='__main__':unittest.main()
