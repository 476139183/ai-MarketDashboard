import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import futures_cn as F
import futures_paper as P


def clock(hour=10, minute=0, second=0, day=9):
    return int(datetime(2026,11,day,hour,minute,second,tzinfo=F.BJ).timestamp()*1000)


def packet(now=None, price=3000, direction=1, symbol='RB2701'):
    now=clock() if now is None else now
    today=datetime.fromtimestamp(now/1000,F.BJ).replace(hour=0,minute=0,second=0,microsecond=0)
    values=list(range(2900,2965)) if direction>0 else list(range(3065,3000,-1))
    bars=[dict(time=int((today-timedelta(days=65-i)).timestamp()*1000),open=v,high=v+2,low=v-2,close=v,volume=100,open_interest=100,complete=True) for i,v in enumerate(values)]
    bars.append(dict(time=int(today.timestamp()*1000),open=price,high=price+1,low=price-1,close=price,volume=100,open_interest=100,complete=False))
    return dict(symbol=symbol,source=F.SOURCE,period='day',candles=bars,quote=dict(F.identity(symbol),price=price,quote_at=now,volume=100,open_interest=100,error=None))


class FuturesPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'paper.sqlite3'
        self.account=P.FuturesPaperAccount(self.path)

    def tearDown(self):self.temp.cleanup()

    def enable(self):self.account.set_enabled(True,now=clock())

    def enter(self, direction=1):
        self.enable();self.account.step(packet(direction=direction),now=clock())
        self.account.step(packet(clock(second=5),direction=direction),now=clock(second=5))
        return self.account.snapshot(now=clock(second=5))

    def test_new_account_is_paused_cny_and_independent(self):
        state=self.account.snapshot(now=clock())
        self.assertFalse(state['enabled']);self.assertEqual(state['currency'],'CNY')
        self.assertEqual(state['equity'],100000);self.assertEqual(state['margin'],0)
        self.assertEqual(state['positions'],[]);self.assertEqual(state['config']['strategy'],'sma')
        self.assertEqual(state['config']['symbol'],'RB2701')

    def test_signal_waits_for_later_quote_and_uses_prior_complete_daily_bars(self):
        self.enable();data=packet();data['candles'][-1]['close']=1
        self.account.step(data,now=clock());self.assertEqual(self.account.snapshot()['positions'],[])
        self.account.step(data,now=clock(second=1));self.assertEqual(self.account.snapshot()['positions'],[])
        self.account.step(packet(clock(second=5)),now=clock(second=5))
        p=self.account.snapshot(now=clock(second=5))['positions'][0]
        self.assertEqual(p['side'],1);self.assertEqual(p['entry_price'],3001)
        self.assertLess(p['signal_at'],p['entry_at'])

    def test_long_pnl_multiplier_margin_and_fees_without_double_slippage(self):
        state=self.enter();self.assertEqual(state['balance'],99997)
        self.assertAlmostEqual(state['equity'],99987);self.assertAlmostEqual(state['margin'],4500)
        self.assertAlmostEqual(state['available'],95487)
        stop=state['positions'][0]['stop']
        self.account.step(packet(clock(second=10),price=stop-10),now=clock(second=10))
        state=self.account.snapshot(now=clock(second=10));trade=state['trades'][0]
        self.assertEqual(state['positions'],[])
        expected=(trade['exit_price']-3001)*10-6
        self.assertAlmostEqual(trade['net_pnl'],expected)
        self.assertAlmostEqual(state['equity'],100000+expected)
        self.assertEqual(state['fees'],6)

    def test_short_pnl_and_same_day_fee_factor(self):
        cfg=dict(self.account.snapshot()['config'],close_today_factor=2)
        self.account.configure(cfg,now=clock());self.enter(-1)
        self.account.step(packet(clock(second=10),price=3100,direction=-1),now=clock(second=10))
        state=self.account.snapshot(now=clock(second=10));trade=state['trades'][0]
        self.assertEqual(trade['entry_price'],2999);self.assertEqual(trade['exit_price'],3101)
        self.assertEqual(trade['exit_fee'],6);self.assertEqual(trade['net_pnl'],-1029)

    def test_stale_future_night_break_and_wrong_contract_never_fill(self):
        self.enable()
        for now in [clock(hour=21),clock(hour=10,minute=20),clock(day=8)]:
            self.account.step(packet(now),now=now)
        for quote_time in [clock()-31000,clock()+5000]:
            self.account.step(packet(quote_time),now=clock())
        self.account.step(packet(symbol='AU2701'),now=clock())
        state=self.account.snapshot();self.assertEqual(state['positions'],[]);self.assertIsNone(state['pending'])

    def test_no_new_positions_when_daily_anchor_or_complete_history_missing(self):
        self.enable()
        for data in [dict(packet(),candles=packet()['candles'][:-1]),dict(packet(),candles=packet()['candles'][-10:])]:
            self.account.step(data,now=clock());self.account.step(dict(data,quote=packet(clock(second=5))['quote']),now=clock(second=5))
        self.assertEqual(self.account.snapshot()['positions'],[])

    def test_pause_blocks_new_entries_but_existing_stops_keep_working(self):
        self.enter();self.account.set_enabled(False,now=clock(second=6))
        self.account.step(packet(clock(second=10),price=2900),now=clock(second=10))
        state=self.account.snapshot();self.assertFalse(state['enabled']);self.assertEqual(len(state['trades']),1)
        self.account.step(packet(clock(second=15)),now=clock(second=15));self.assertEqual(self.account.snapshot()['positions'],[])

    def test_repeated_signal_after_stop_does_not_reopen(self):
        self.enter();self.account.step(packet(clock(second=10),price=2900),now=clock(second=10))
        for sec in (15,20,25):self.account.step(packet(clock(second=sec)),now=clock(second=sec))
        state=self.account.snapshot();self.assertEqual(len(state['trades']),1);self.assertEqual(state['positions'],[])

    def test_restart_preserves_position_balance_and_ignores_pending(self):
        self.enter();restored=P.FuturesPaperAccount(self.path);restored.recover(now=clock(second=6))
        state=restored.snapshot(now=clock(second=6));self.assertTrue(state['enabled']);self.assertEqual(len(state['positions']),1)
        self.assertIsNone(state['pending']);self.assertEqual(state['balance'],99997)
        restored.step(packet(clock(second=5)),now=clock(second=10));self.assertEqual(restored.snapshot()['trades'],[])

    def test_bad_history_does_not_prevent_valid_quote_stop(self):
        self.enter();data=packet(clock(second=10),price=2900);data['candles']=[]
        self.account.step(data,now=clock(second=10));self.assertEqual(len(self.account.snapshot()['trades']),1)

    def test_config_only_while_paused_flat_and_never_resets_capital(self):
        original=self.account.snapshot()['config'];self.enable()
        with self.assertRaises(ValueError):self.account.configure(original,now=clock())
        self.enter();self.account.set_enabled(False)
        with self.assertRaises(ValueError):self.account.configure(original,now=clock())
        for update in [dict(symbol='RB0'),dict(lots=1.5),dict(margin_rate=0),dict(fee_fixed=-1),dict(tick_size=0),dict(strategy='crypto'),dict(capital=999)]:
            with self.assertRaises(ValueError):P.normalize_config(dict(original,**update))

    def test_delivery_month_and_insufficient_margin_reject_new_entries(self):
        cfg=self.account.snapshot()['config'];self.account.configure(dict(cfg,symbol='RB2611'),now=clock())
        self.enable()
        for sec in (0,5):self.account.step(packet(clock(second=sec),symbol='RB2611'),now=clock(second=sec))
        self.assertEqual(self.account.snapshot()['positions'],[])
        self.account.set_enabled(False);self.account.configure(dict(cfg,lots=100),now=clock());self.enable()
        for sec in (0,5):self.account.step(packet(clock(second=sec)),now=clock(second=sec))
        state=self.account.snapshot();self.assertEqual(state['positions'],[]);self.assertIn('保证金',state['error'])

    def test_corrupt_or_foreign_ledger_rejected(self):
        self.account.snapshot()
        with closing(sqlite3.connect(self.path)) as db:
            raw=db.execute('SELECT body FROM futures_paper_state WHERE id=1').fetchone()[0]
            state=json.loads(raw);state['market']='crypto';db.execute('UPDATE futures_paper_state SET body=?',(json.dumps(state),));db.commit()
        with self.assertRaises(ValueError):self.account.snapshot()

    def test_margin_breach_flattens_halts_new_entries_and_keeps_negative_equity(self):
        cfg=self.account.snapshot()['config']
        self.account.configure(dict(cfg,lots=10),now=clock())
        state=self.enter();self.assertEqual(len(state['positions']),1)
        self.account.step(packet(clock(second=10),price=1000),now=clock(second=10))
        state=self.account.snapshot(now=clock(second=10))
        self.assertEqual(state['positions'],[]);self.assertTrue(state['halted']);self.assertFalse(state['enabled'])
        self.assertLess(state['equity'],0);self.assertEqual(state['trades'][0]['reason'],'margin_exit')
        with self.assertRaises(ValueError):self.account.set_enabled(True)

    def test_pending_signal_cleared_on_pause_or_recovery(self):
        self.enable();self.account.step(packet(),now=clock());self.assertIsNotNone(self.account.snapshot()['pending'])
        self.account.recover(now=clock(second=4));self.assertIsNone(self.account.snapshot()['pending'])
        self.account.step(packet(clock(second=5)),now=clock(second=5))
        self.assertEqual(self.account.snapshot()['positions'],[])
        self.account.set_enabled(False);self.assertIsNone(self.account.snapshot()['pending'])

    def test_invalid_source_and_zero_volume_do_not_change_positions(self):
        self.enter()
        for change in [dict(source='OKX'),dict(quote=dict(packet()['quote'],volume=0)),dict(quote=dict(packet()['quote'],price=float('nan')))]:
            self.account.step(dict(packet(clock(second=10),price=2900),**change),now=clock(second=10))
        state=self.account.snapshot();self.assertEqual(len(state['positions']),1);self.assertEqual(state['trades'],[])

    def test_bad_ledger_startup_does_not_abort_other_dashboard_services(self):
        runner=P.FuturesPaperRunner(self.account)
        with patch.object(self.account,'recover',side_effect=ValueError('账本身份不一致')):
            runner.start()
        self.assertIsNone(runner.thread)
        self.assertIn('账本身份不一致',runner.error)

    def test_runner_reads_only_when_enabled_or_position_exists(self):
        calls=[]
        runner=P.FuturesPaperRunner(self.account,fetch=lambda symbol:calls.append(symbol) or packet())
        runner.tick(now=clock());self.assertEqual(calls,[])
        self.enable();runner.tick(now=clock());self.assertEqual(calls,['RB2701'])


if __name__=='__main__':unittest.main()
