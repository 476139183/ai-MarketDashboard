from contextlib import closing
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
import sys
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
    def test_distant_yesterday_close_waits_for_in_band_quote_and_records_revision(self):
        from test_stock_setups import rising
        import stock_setups as S
        rows=rising();rows[-1].update(open=14.,close=14.1,high=14.15,low=13.9,volume=2000.)
        plan=S.build_setup_plan(rows,'trend_pullback');r=radar()
        r['strategy_candidates'][0].update(close=14.1,plan_ready=plan['plan_ready'],trade_plan=plan)
        self.account.set_enabled(True,now=clock())
        for sec,price in [(0,14.1),(5,14.1),(10,plan['sniper']['ideal_buy']-.01),(15,plan['sniper']['ideal_buy']-.01)]:
            pack=observation(clock(second=sec),price=price,previous_close=14.1,upper_limit=20.,lower_limit=5.)
            pack['daily'][-1]['close']=14.1
            self.account.step(r,self.calendar,{'600000':pack},now=clock(second=sec))
            if sec<15:self.assertEqual(self.account.snapshot()['positions'],[])
        position=self.account.snapshot()['positions'][0]
        self.assertEqual(position['plan']['plan_revision'],S.PLAN_REVISION)
        self.assertLessEqual(position['entry_price'],plan['sniper']['ideal_buy'])

    def test_runner_refreshes_old_plan_revision_only_after_close(self):
        old=dict(radar('2026-11-09'),entry_revision=P.scan_revision(),continuation_enabled=P.C.RELEASED,
                 oversold_reversal_enabled=False,plan_revision='old')
        calls=[];new=dict(old,plan_revision=P.SETUP_PLAN_REVISION)
        self.account.set_enabled(True,now=clock(hour=16))
        with self.account.transaction() as s:
            s.update(scan_attempt_day='2026-11-09',scan_entry_revision=P.scan_revision(),
                     scan_continuation_enabled=P.C.RELEASED,scan_reversal_enabled=False,scan_plan_revision='old')
        runner=P.ASharePaperRunner(self.account,load_radar=lambda:old,refresh=lambda:calls.append(1) or new)
        runner.tick(now=clock(hour=16));runner.tick(now=clock(hour=17))
        state=self.account.snapshot()
        self.assertEqual(calls,[1]);self.assertEqual(state['candidate_snapshot']['plan_revision'],P.SETUP_PLAN_REVISION)
        self.assertEqual(state['scan_plan_revision'],P.SETUP_PLAN_REVISION)

    def test_snapshot_explains_market_and_plan_blocks_without_turning_watch_into_orders(self):
        source=radar();source.update(universe_size=100,scanned=95,excluded_count=52,market_pass=False,strategy_candidates=[])
        source['watch_pool']=[dict(confirmed_rules=['trend_pullback'],plan_ready=True,trade_plan=dict(plan_ready=True)),
            dict(confirmed_rules=['trend_pullback'],plan_ready=False,trade_plan=dict(rejection_reasons=['距离回踩中心超过5%，不追高'])),
            dict(confirmed_rules=[],plan_ready=False,trade_plan=dict(summary='仅原始观察信号'))]
        runner=P.ASharePaperRunner(self.account,load_radar=lambda:source)
        state=runner.snapshot();d=state['source_diagnostics']
        self.assertEqual(d['universe'],100);self.assertEqual(d['confirmed'],2)
        self.assertEqual(d['ready_before_market'],1);self.assertEqual(d['market_blocked'],1)
        self.assertEqual(d['plan_rejections']['距离回踩中心超过5%，不追高'],1)
        self.assertEqual(state['plans'],[]);self.assertEqual(state['positions'],[])

    def test_reversal_requires_release_or_explicit_paper_trial_for_live_source(self):
        from test_oversold_reversal import setup_history,minutes
        r=radar();r.update(market_pass=False,oversold_reversal_enabled=True,oversold_reversal_market_pass=True)
        r['strategy_candidates'][0].update(close=12.15,trade_plan=P.M.build_plan(*setup_history(),'测试'))
        t=clock();pack=observation(t,price=12.1,previous_close=12.15,ask=12.1,upper_limit=14.,lower_limit=10.)
        for row in pack['daily']:row['close']=12.15
        pack['minutes']=[dict(b,time='2026-11-09'+b['time'][10:]) for b in minutes()]
        self.account.set_enabled(True,now=t-10000)
        with patch.object(P.M,'RELEASED',False),patch.object(P.M,'PAPER_TRIAL',False):
            self.account.step(r,self.calendar,{'600000':pack},now=t)
            self.assertEqual(self.account.snapshot()['plans'],[])
        stale=deepcopy(r);stale['strategy_candidates'][0]['trade_plan']['strategy_version']='a-oversold-reversal-v1'
        with patch.object(P.M,'RELEASED',False),patch.object(P.M,'PAPER_TRIAL',True):
            self.account.step(stale,self.calendar,{'600000':pack},now=t)
            self.assertEqual(self.account.snapshot()['plans'],[])
        with patch.object(P.M,'RELEASED',False),patch.object(P.M,'PAPER_TRIAL',True):
            for offset in (5000,10000):
                pack['quote']['quote_at']=t+offset;self.account.step(r,self.calendar,{'600000':pack},now=t+offset)
            self.assertEqual(len(self.account.snapshot()['positions']),1)

    def test_reversal_uses_its_mean_target_after_t_plus_one_and_not_legacy_ma10(self):
        from test_oversold_reversal import setup_history,minutes
        r=radar();plan=P.M.build_plan(*setup_history(),'测试')
        r.update(market_pass=False,oversold_reversal_enabled=True,oversold_reversal_market_pass=True)
        r['strategy_candidates'][0].update(close=12.15,trade_plan=plan)
        t=int(datetime.fromisoformat('2026-11-09T10:00:05+08:00').timestamp()*1000)
        pack=observation(t,price=12.1,previous_close=12.15,ask=12.1,bid=12.09,upper_limit=14.,lower_limit=10.)
        for row in pack['daily']:row['close']=12.15
        pack['minutes']=[dict(b,time='2026-11-09'+b['time'][10:]) for b in minutes()]
        self.account.set_enabled(True,now=t-10000)
        with patch.object(P.M,'RELEASED',True):
            for offset in (0,5000):
                pack['quote']['quote_at']=t+offset;self.account.step(r,self.calendar,{'600000':pack},now=t+offset)
            p=self.account.snapshot()['positions'][0]
            self.assertEqual(p['target'],12.55)
            self.assertLessEqual(p['plan']['estimated_stop_risk'],500.)
            pack['quote'].update(price=12.6,bid=12.59,quote_at=t+10000)
            self.account.step(r,self.calendar,{'600000':pack},now=t+10000)
            self.assertEqual(self.account.snapshot()['trades'],[])
            tomorrow=int(datetime.fromisoformat('2026-11-10T10:00:05+08:00').timestamp()*1000)
            newer=observation(tomorrow,price=12.4,previous_close=12.2,bid=12.39,upper_limit=14.,lower_limit=10.)
            for row in newer['daily']:row['close']=13.
            newer['daily'][-1]['close']=12.2
            # Last close is below MA10, but the reversal's own target/failure has not occurred.
            self.account.step({},self.calendar+['2026-11-10'],{'600000':newer},now=tomorrow)
            self.assertEqual(self.account.snapshot()['trades'],[])
            newer['quote'].update(price=12.6,bid=12.59,quote_at=tomorrow+5000)
            self.account.step({},self.calendar+['2026-11-10'],{'600000':newer},now=tomorrow+5000)
            self.assertEqual(self.account.snapshot()['trades'][0]['reason'],'target')

    def test_reversal_rejects_in_band_quote_when_net_mean_reward_cannot_cover_stop_risk(self):
        from test_oversold_reversal import setup_history,minutes
        r=radar();r.update(market_pass=False,oversold_reversal_enabled=True,oversold_reversal_market_pass=True)
        r['strategy_candidates'][0].update(close=12.15,trade_plan=P.M.build_plan(*setup_history(),'测试',version='a-oversold-reversal-v2'))
        t=int(datetime.fromisoformat('2026-11-09T10:00:05+08:00').timestamp()*1000)
        pack=observation(t,price=12.2,previous_close=12.15,ask=12.2,bid=12.19,upper_limit=14.,lower_limit=10.)
        for row in pack['daily']:row['close']=12.15
        pack['minutes']=[dict(b,time='2026-11-09'+b['time'][10:]) for b in minutes()]
        self.account.set_enabled(True,now=t-10000)
        with patch.object(P.M,'RELEASED',True),patch.object(P.M,'active_version',return_value='a-oversold-reversal-v2'):
            for offset in (0,5000):
                pack['quote']['quote_at']=t+offset;self.account.step(r,self.calendar,{'600000':pack},now=t+offset)
        s=self.account.snapshot();self.assertEqual(s['positions'],[])
        self.assertIn('净目标空间',s['error'])

    def test_v3_reversal_rejects_mean_target_that_is_positive_only_before_cost_stress(self):
        from test_oversold_reversal import setup_history,minutes
        r=radar();r.update(market_pass=False,oversold_reversal_enabled=True,oversold_reversal_market_pass=True)
        plan=P.M.build_plan(*setup_history(),'测试');plan['sniper'].update(ideal_buy=12.23,take_profit=12.25)
        r['strategy_candidates'][0].update(close=12.15,trade_plan=plan)
        t=int(datetime.fromisoformat('2026-11-09T10:00:05+08:00').timestamp()*1000)
        pack=observation(t,price=12.2,previous_close=12.15,ask=12.2,bid=12.19,upper_limit=14.,lower_limit=10.)
        for row in pack['daily']:row['close']=12.15
        pack['minutes']=[dict(b,time='2026-11-09'+b['time'][10:]) for b in minutes()]
        self.account.set_enabled(True,now=t-10000)
        with patch.object(P.M,'RELEASED',True):
            for offset in (0,5000):
                pack['quote']['quote_at']=t+offset;self.account.step(r,self.calendar,{'600000':pack},now=t+offset)
        self.assertEqual(self.account.snapshot()['positions'],[])

    def test_historical_prepare_only_cannot_fill_close_or_run_on_live_account(self):
        with self.assertRaisesRegex(ValueError,'历史'):
            self.account.step({},[],{},now=clock(),prepare_only=True)
        historical=P.ASharePaperAccount(Path(self.tmp.name)/'research.sqlite3',source='sina_historical_15m_open')
        historical.set_enabled(True,now=clock(second=0)-10000)
        pack=observation(clock(second=0));pack['quote']['source']='sina_historical_15m_open'
        historical.step(radar(),self.calendar,{'600000':pack},now=clock(second=0),prepare_only=True)
        pack['quote']['quote_at']=clock(second=5)
        historical.step(radar(),self.calendar,{'600000':pack},now=clock(second=5),prepare_only=True)
        self.assertEqual(historical.snapshot()['positions'],[])
        pack['quote']['quote_at']=clock(second=10)
        historical.step(radar(),self.calendar,{'600000':pack},now=clock(second=10))
        self.assertEqual(len(historical.snapshot()['positions']),1)

    def continuation_case(self):
        if str(P.RADAR_PATH.parent) not in sys.path:sys.path.append(str(P.RADAR_PATH.parent))
        from test_continuation import history,minutes
        import continuation as C
        plan=C.build_plan(*history(),'测试')
        r=radar();r.update(market_pass=False,continuation_market_pass=True,continuation_enabled=True)
        r['strategy_candidates'][0].update(close=13.2,trade_plan=plan)
        t=int(datetime.fromisoformat('2026-11-09T10:15:05+08:00').timestamp()*1000)
        pack=observation(t,price=13.4,previous_close=13.2,ask=13.4,bid=13.39,upper_limit=15.,lower_limit=11.)
        for row in pack['daily']:row['close']=13.2
        pack['minutes']=minutes('2026-11-09')
        return r,t,pack

    def trend_case(self):
        from test_continuation import history
        from test_trend_reclaim import bars
        r,t,pack=self.continuation_case();r.update(trend_reclaim_enabled=True,trend_reclaim_market_pass=True)
        r['strategy_candidates'][0]['trade_plan']=P.T.build_plan(*history(),'测试')
        pack['quote'].update(price=13.18,ask=13.18,bid=13.17)
        pack['minutes']=[dict(b,time='2026-11-09'+b['time'][10:]) for b in bars()]
        return r,t,pack

    def test_unreleased_trend_reclaim_blocks_live_buys_and_released_rule_keeps_ma20(self):
        r,t,pack=self.trend_case();self.account.set_enabled(True,now=t-10000)
        self.assertEqual(len(P.approved_plans(r,'2026-11-06',t)),1)
        with patch.object(P.T,'RELEASED',False):
            for offset in (0,5000):
                pack['quote']['quote_at']=t+offset;self.account.step(r,self.calendar,{'600000':pack},now=t+offset)
        self.assertEqual(self.account.snapshot()['positions'],[])
        with patch.object(P.T,'RELEASED',True):
            for offset in (10000,15000):
                pack['quote']['quote_at']=t+offset;self.account.step(r,self.calendar,{'600000':pack},now=t+offset)
        p=self.account.snapshot()['positions'][0]
        self.assertEqual(p['costs']['exit_ma'],20);self.assertIsNone(p['target'])

    def test_missing_trend_center_does_not_leave_partially_approved_plan(self):
        r,t,_=self.trend_case();r['strategy_candidates'][0]['trade_plan']['center']=None
        self.assertEqual(P.approved_plans(r,'2026-11-06',t),[])

    @patch.object(P.C,'RELEASED',True)
    def test_overnight_archive_keeps_experimental_admission_flags(self):
        r,t,pack=self.continuation_case();source=[r]
        self.account.set_enabled(True,now=clock(day=6,hour=16))
        runner=P.ASharePaperRunner(self.account,load_radar=lambda:source[0],refresh=lambda:r,
            fetch=lambda codes:{'600000':pack},calendar=lambda day:self.calendar)
        runner.tick(now=clock(day=6,hour=16))
        self.assertTrue(self.account.snapshot()['candidate_snapshot']['continuation_enabled'])
        source[0]=radar('2026-11-09')
        runner.tick(now=t);pack['quote']['quote_at']=t+5000;runner.tick(now=t+5000)
        self.assertEqual(len(self.account.snapshot()['positions']),1)

    @patch.object(P.C,'RELEASED',True)
    def test_continuation_requires_confirmation_and_limits_cash_and_costed_risk(self):
        r,t,pack=self.continuation_case()
        self.account.configure(dict(per_stock_budget=100000))
        self.account.set_enabled(True,now=t-10000)
        self.account.step(r,self.calendar,{'600000':pack},now=t)
        self.assertEqual(self.account.snapshot()['positions'],[])
        pack['quote']['quote_at']=t+5000
        self.account.step(r,self.calendar,{'600000':pack},now=t+5000)
        p=self.account.snapshot()['positions'][0]
        self.assertEqual(p['plan']['entry_mode'],'continuation')
        self.assertIsNone(p['target'])
        self.assertLessEqual(p['plan']['estimated_stop_risk'],250.)
        self.assertLessEqual(p['entry_price']*p['shares']+p['entry_fee'],50000.)
        pack['quote'].update(price=13.8,bid=13.79,quote_at=t+10000)
        self.account.step(r,self.calendar,{'600000':pack},now=t+10000)
        self.assertEqual(self.account.snapshot()['trades'],[])

    def test_continuation_cannot_buy_on_breakout_alone_or_bad_market_context(self):
        r,t,pack=self.continuation_case();self.account.set_enabled(True,now=t-10000)
        pack['minutes']=pack['minutes'][:2]
        self.account.step(r,self.calendar,{'600000':pack},now=t)
        pack['quote']['quote_at']=t+5000
        self.account.step(r,self.calendar,{'600000':pack},now=t+5000)
        self.assertEqual(self.account.snapshot()['positions'],[])
        r['continuation_market_pass']=False
        self.assertEqual(P.approved_plans(r,'2026-11-06',t),[])

    @patch.object(P.C,'RELEASED',True)
    def test_continuation_t_plus_one_then_small_profit_holds_until_structure_or_stop(self):
        r,t,pack=self.continuation_case();self.account.set_enabled(True,now=t-10000)
        self.account.step(r,self.calendar,{'600000':pack},now=t)
        pack['quote']['quote_at']=t+5000
        self.account.step(r,self.calendar,{'600000':pack},now=t+5000)
        p=self.account.snapshot()['positions'][0]
        pack['quote'].update(price=13.,bid=12.99,quote_at=t+10000)
        self.account.step(r,self.calendar,{'600000':pack},now=t+10000)
        self.assertEqual(self.account.snapshot()['trades'],[])
        tomorrow=int(datetime.fromisoformat('2026-11-10T10:15:05+08:00').timestamp()*1000)
        newer=observation(tomorrow,price=p['entry_price']+.03,previous_close=13.2,bid=p['entry_price']+.02,upper_limit=15.,lower_limit=11.)
        for row in newer['daily']:row['close']=13.2
        newer['minutes']=[]
        self.account.step({},self.calendar+['2026-11-10'],{'600000':newer},now=tomorrow)
        self.assertEqual(self.account.snapshot()['trades'],[])
        newer['quote'].update(price=13.,bid=12.99,quote_at=tomorrow+5000)
        self.account.step({},self.calendar+['2026-11-10'],{'600000':newer},now=tomorrow+5000)
        self.assertEqual(self.account.snapshot()['trades'][0]['reason'],'stop')

    def test_unreleased_channel_never_buys_in_live_account_even_if_snapshot_contains_it(self):
        r,t,pack=self.continuation_case();self.account.set_enabled(True,now=t-10000)
        with patch.object(P.C,'RELEASED',False):
            self.account.step(r,self.calendar,{'600000':pack},now=t)
            pack['quote']['quote_at']=t+5000
            self.account.step(r,self.calendar,{'600000':pack},now=t+5000)
        self.assertEqual(self.account.snapshot()['positions'],[])

    def test_stale_execution_daily_is_not_cached_as_success_and_can_retry(self):
        P.daily_rows.cache_clear()
        stale=[dict(date='2026-11-05',close=10)]
        fresh=[dict(date='2026-11-06',close=10)]
        from unittest.mock import Mock
        fallback=Mock(side_effect=[ValueError('日线未就绪'),(fresh,'fresh')])
        try:
            with patch('feeds.cn.from_sina',return_value=(stale,'stale')), \
                 patch.object(P,'market_calendar',return_value=self.calendar), \
                 patch.dict(sys.modules,radar=SimpleNamespace(_current_history=fallback)):
                with self.assertRaises(ValueError):P.daily_rows('600000','2026-11-09')
                self.assertEqual(P.daily_rows('600000','2026-11-09'),fresh)
                self.assertEqual(P.daily_rows('600000','2026-11-09'),fresh)
                self.assertEqual(fallback.call_count,2)
        finally:P.daily_rows.cache_clear()

    def test_incomplete_scan_retries_and_never_archives_invalid_empty_list(self):
        broken = dict(radar('2026-11-09'), data_ready=False, market_pass=False)
        calls = []
        source = [broken]
        runner = P.ASharePaperRunner(self.account, load_radar=lambda: source[0],
            refresh=lambda: calls.append('scan') or source[0], fetch=lambda codes: {})
        self.account.set_enabled(True, now=clock(hour=16))
        runner.tick(now=clock(hour=16))
        self.assertFalse(self.account.snapshot()['candidate_snapshot'])
        self.assertIn('日线', self.account.snapshot()['scan_error'])
        runner.tick(now=clock(hour=16, second=15))
        self.assertEqual(len(calls), 1)
        source[0] = dict(radar('2026-11-09'), data_ready=True)
        runner.tick(now=clock(hour=17))
        self.assertEqual(self.account.snapshot()['plan_date'], '2026-11-09')
        self.assertIsNone(self.account.snapshot()['scan_error'])

    def test_enabled_account_does_not_imply_live_worker(self):
        self.account.set_enabled(True, now=clock())
        runner = P.ASharePaperRunner(self.account, load_radar=radar)
        self.assertFalse(runner.snapshot()['runner_alive'])
        self.assertIsNone(runner.snapshot()['runner_last_tick_at'])

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

    def test_new_revision_regenerates_same_day_old_archive_once_without_reset(self):
        self.account.set_enabled(True,now=clock(hour=16))
        with self.account.transaction() as s:
            s.update(scan_attempt_day='2026-11-09',candidate_snapshot=radar('2026-11-09'),plan_date='2026-11-09')
        calls=[]
        r=dict(radar('2026-11-09'),entry_revision=P.C.ENTRY_REVISION,continuation_market_pass=True)
        runner=P.ASharePaperRunner(self.account,load_radar=lambda:radar('2026-11-09'),refresh=lambda:calls.append(1) or r)
        runner.tick(now=clock(hour=16));runner.tick(now=clock(hour=17))
        s=self.account.snapshot()
        self.assertEqual(calls,[1]);self.assertEqual(s['candidate_snapshot']['entry_revision'],P.C.ENTRY_REVISION)
        self.assertEqual(s['cash'],100000);self.assertTrue(s['enabled']);self.assertEqual(s['trades'],[])


if __name__=='__main__':unittest.main()
