from contextlib import closing
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import a_share_paper as P


def clock(day=9,hour=10,second=0):return int(datetime(2026,11,day,hour,0,second,tzinfo=P.BJ).timestamp()*1000)

def radar(signal='2026-11-06',code='600000'):
    return dict(signal_date=signal,generated_at=signal+'T16:00:00+08:00',market_pass=True,
      strategy_candidates=[dict(code=code,name='测试股票',category='策略候选',market_pass=True,close=10.2,plan_ready=True,
      trade_plan=dict(plan_ready=True,sniper=dict(ideal_buy=10,secondary_buy=9.5,stop_loss=9,take_profit=12)))],watch_pool=[])


def observation(now=None,price=9.99,symbol='600000',**updates):
    now=clock() if now is None else now
    prev='2026-11-06' if P.day(now)=='2026-11-09' else '2026-11-09'
    rows=[dict(date=(datetime.fromisoformat(prev)-timedelta(days=19-i)).date().isoformat(),close=10.2) for i in range(20)]
    q=dict(symbol=symbol,source=P.SOURCE,name='测试股票',price=price,previous_close=10.2,quote_at=now,volume=100,
           bid=round(price-.01,2),ask=round(price+.00,2),upper_limit=13,lower_limit=8)
    q.update(updates)
    return dict(quote=q,daily=rows)


class ASharePaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.account=P.ASharePaperAccount(Path(self.tmp.name)/'a.sqlite3')
        self.calendar=['2026-11-05','2026-11-06','2026-11-09']
    def tearDown(self):self.tmp.cleanup()
    def step(self,now=None,data=None,plans=None,calendar=None):
        now=clock() if now is None else now
        self.account.step(radar() if plans is None else plans,(self.calendar+(['2026-11-10'] if P.day(now)=='2026-11-10' else [])) if calendar is None else calendar,
                          {'600000':observation(now) if data is None else data},now=now)
    def buy(self):
        self.account.set_enabled(True,now=clock());self.step();self.step(clock(second=5))
        return self.account.snapshot(now=clock(second=5))
    def test_new_plan_version_and_rule_are_archived_on_position(self):
        r=radar();r['strategy_version']='a-setups-v2'
        r['strategy_candidates'][0]['trade_plan'].update(strategy_version='a-setups-v2',strategy_rule='trend_pullback')
        self.account.set_enabled(True,now=clock())
        self.step(plans=r);self.step(clock(second=5),plans=r)
        p=self.account.snapshot(now=clock(second=5))['positions'][0]
        self.assertEqual(p['plan']['strategy_version'],'a-setups-v2')
        self.assertEqual(p['plan']['strategy_rule'],'trend_pullback')
    def test_default_paused_separate_cny_account(self):
        s=self.account.snapshot();self.assertFalse(s['enabled']);self.assertEqual(s['cash'],100000)
        self.assertEqual(s['market'],'a_share');self.assertEqual(s['positions'],[])
    def test_buy_requires_plan_and_later_quote_and_round_lot_with_fees(self):
        self.account.set_enabled(True,now=clock());self.step();self.assertEqual(self.account.snapshot()['positions'],[])
        self.step(clock(second=5));s=self.account.snapshot(now=clock(second=5));p=s['positions'][0]
        self.assertEqual(p['entry_price'],10);self.assertEqual(p['shares'],1900)
        self.assertAlmostEqual(s['cash'],80994.11);self.assertEqual(p['entry_fee'],5.89)
        self.assertEqual(p['stop'],9);self.assertEqual(p['target'],12)
    def test_watch_pool_bad_market_and_not_ready_never_buy(self):
        self.account.set_enabled(True,now=clock())
        for kind in ['watch','market','ready']:
            r=radar()
            if kind=='watch':r['watch_pool']=r['strategy_candidates'];r['strategy_candidates']=[]
            elif kind=='market':r['market_pass']=False
            else:r['strategy_candidates'][0]['trade_plan']['plan_ready']=False
            self.step(plans=r);self.step(clock(second=5),plans=r)
        self.assertEqual(self.account.snapshot()['positions'],[])
    def test_stale_late_recorded_same_day_and_nonconsecutive_plan_rejected(self):
        self.account.set_enabled(True,now=clock())
        for r in [radar('2026-11-05'),radar('2026-11-09'),dict(radar(),generated_at='2026-11-09T09:00:00+08:00')]:
            self.step(plans=r);self.step(clock(second=5),plans=r)
        self.assertEqual(self.account.snapshot()['positions'],[])
    def test_outside_plan_and_filled_price_above_band_not_bought(self):
        self.account.set_enabled(True,now=clock())
        for price in [10.1,9.4,10]:
            self.step(data=observation(price=price));self.step(clock(second=5),data=observation(clock(second=5),price=price))
        self.assertEqual(self.account.snapshot()['positions'],[])
    def test_t_plus_one_blocks_stop_or_profit_and_next_day_rechecks(self):
        self.buy();self.step(clock(second=10),data=observation(clock(second=10),price=8.9))
        s=self.account.snapshot();self.assertEqual(len(s['positions']),1);self.assertEqual(s['trades'],[])
        self.assertIn('T+1',s['note'])
        self.step(clock(day=10),data=observation(clock(day=10),price=11),plans={})
        self.assertEqual(len(self.account.snapshot()['positions']),1)
        self.step(clock(day=10,second=5),data=observation(clock(day=10,second=5),price=12.1),plans={})
        s=self.account.snapshot();self.assertEqual(s['positions'],[]);self.assertEqual(s['trades'][0]['reason'],'target')
    def test_stop_uses_current_bid_and_full_sell_taxes_without_short(self):
        bought=self.buy();self.account.set_enabled(False)
        self.step(clock(day=10),data=observation(clock(day=10),price=8.9),plans={})
        s=self.account.snapshot();t=s['trades'][0]
        self.assertEqual(t['exit_price'],8.88);self.assertGreater(t['exit_costs']['stamp'],0)
        self.assertEqual(t['entry_costs']['stamp'],0)
        self.assertAlmostEqual(t['net_pnl'],(8.88-10)*1900-t['fees'])
        self.assertAlmostEqual(s['equity'],100000+t['net_pnl'])
        self.assertFalse(s['enabled']);self.assertEqual(s['positions'],[])
    def test_sell_can_wait_several_days_and_uses_complete_ma10(self):
        self.buy();data=observation(clock(day=10),price=10.5)
        data['daily'][-1]['close']=9.7;data['quote']['previous_close']=9.7
        self.step(clock(day=10),data=data,plans={})
        self.assertEqual(self.account.snapshot()['trades'][0]['reason'],'ma10_exit')
    def test_limits_missing_quotes_stale_lunch_st_and_foreign_codes_no_fill(self):
        self.account.set_enabled(True,now=clock())
        bad=[dict(quote_at=clock()-31000),dict(quote_at=clock()+1000),dict(source='OKX'),dict(name='*ST测试'),dict(ask=0),dict(volume=0),dict(upper_limit=9.99)]
        for change in bad:
            self.step(data=observation(**change));self.step(clock(second=5),data=observation(clock(second=5),**change))
        self.step(clock(hour=12));self.assertEqual(self.account.snapshot()['positions'],[])
        for code in ['510300','920001','689001','900001']:
            with self.assertRaises(ValueError):P.stock_code(code)
    def test_limit_down_does_not_forge_exit(self):
        self.buy();data=observation(clock(day=10),price=9,lower_limit=9,bid=0)
        self.step(clock(day=10),data=data,plans={});self.assertEqual(len(self.account.snapshot()['positions']),1)
    def test_adjustment_mismatch_preserves_old_valuation_and_blocks_fills(self):
        before=self.buy();data=observation(clock(day=10),price=8,previous_close=8)
        self.step(clock(day=10),data=data,plans={})
        s=self.account.snapshot(now=clock(day=10));self.assertEqual(s['positions'][0]['last_price'],before['positions'][0]['last_price'])
        self.assertFalse(s['valuation_fresh']);self.assertIn('除权',s['error'])
    def test_pause_and_restart_clear_pending_keep_plan_and_history(self):
        self.buy();self.account.recover(now=clock(second=6));s=self.account.snapshot()
        self.assertEqual(len(s['positions']),1);self.assertEqual(s['positions'][0]['signal_date'],'2026-11-06')
        self.account.set_enabled(False);self.assertEqual(self.account.snapshot()['pending'],{})
    def test_same_plan_not_rebought_after_exit(self):
        self.buy();self.step(clock(day=10),data=observation(clock(day=10),price=12.1))
        self.step(clock(day=10,second=5),data=observation(clock(day=10,second=5)))
        self.assertEqual(self.account.snapshot()['positions'],[])
    def test_star_board_minimum_200_shares_and_config_only_flat_paused(self):
        self.assertEqual(P.minimum_shares('688001'),200)
        self.account.configure(dict(P.DEFAULTS,per_stock_budget=1000))
        self.account.set_enabled(True,now=clock())
        with self.assertRaises(ValueError):self.account.configure(P.DEFAULTS)
        for bad in [dict(max_positions=1.5),dict(per_stock_budget=-1),dict(commission_rate=float('nan')),dict(capital=1)]:
            with self.assertRaises(ValueError):P.normalize_config(dict(P.DEFAULTS,**bad))
    def test_overnight_plan_archive_survives_next_morning_file_rewrite(self):
        current=[clock(hour=16)]
        self.account.set_enabled(True,now=current[0])
        source=[radar('2026-11-09')]
        runner=P.ASharePaperRunner(self.account,load_radar=lambda:source[0],refresh=lambda:source[0],
            fetch=lambda symbols:{c:observation(current[0],symbol=c) for c in symbols},calendar=lambda today:['2026-11-06','2026-11-09','2026-11-10'])
        runner.tick(now=current[0])
        source[0]=dict(radar('2026-11-09'),generated_at='2026-11-10T09:00:00+08:00')
        current[0]=clock(day=10);runner.tick(now=current[0])
        current[0]=clock(day=10,second=5);runner.tick(now=current[0])
        p=self.account.snapshot()['positions'][0]
        self.assertEqual(p['signal_date'],'2026-11-09');self.assertEqual(p['plan']['generated_at'],'2026-11-09T16:00:00+08:00')

    def test_unknown_trading_calendar_does_not_sell_even_after_calendar_date_changes(self):
        self.buy();self.step(clock(day=10),data=observation(clock(day=10),price=8.9),plans={},calendar=[])
        self.assertEqual(len(self.account.snapshot()['positions']),1)

    def test_real_runner_checks_quote_against_time_after_network_fetch(self):
        self.account.set_enabled(True,now=clock())
        runner=P.ASharePaperRunner(self.account,load_radar=radar,fetch=lambda symbols:{'600000':observation(clock(second=5))},calendar=lambda today:self.calendar)
        with patch.object(P,'milliseconds',side_effect=[clock(),clock(second=5)]):runner.tick()
        self.assertIn('600000',self.account.snapshot()['pending'])

    def test_runner_no_fetch_while_paused_and_nightly_scan_once(self):
        calls=[]
        runner=P.ASharePaperRunner(self.account,load_radar=lambda:{},refresh=lambda:calls.append('scan') or radar('2026-11-09'),fetch=lambda symbols:{},calendar=lambda today:self.calendar)
        runner.tick(now=clock(hour=16));self.assertEqual(calls,[])
        self.account.set_enabled(True,now=clock(hour=16));runner.tick(now=clock(hour=16));runner.tick(now=clock(hour=17))
        self.assertEqual(calls,['scan'])


if __name__=='__main__':unittest.main()
