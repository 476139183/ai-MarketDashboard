from datetime import datetime,timedelta
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from copy import deepcopy
import a_paper_research as R


def sample():
    start=datetime(2026,8,3)
    dates=[]
    while len(dates)<34:
        if start.weekday()<5:dates.append(start.date().isoformat())
        start+=timedelta(days=1)
    prices=[100.]*30+[101.,100.15,107.,107.]
    daily=[dict(date=d,open=p,high=p+.1,low=p-.1,close=p,volume=10000000) for d,p in zip(dates,prices)]
    minute=[]
    for d,price in [(dates[31],100.1),(dates[32],107.)]:
        for clock in R.END_TIMES:
            minute.append(dict(time=d+' '+clock,open=price,high=price+.1,low=price-.1,close=price,volume=10000))
    benchmark=[dict(date=d,close=2000+i) for i,d in enumerate(dates)]
    return dict(daily={'600000':daily},minutes={'600000':minute},benchmark=benchmark,names={'600000':'测试股票'},start=dates[31],end=dates[32],limitations=[])


class ResearchTests(unittest.TestCase):
    def test_fixed_execution_cost_stress_can_turn_small_gross_profit_into_loss(self):
        cfg=R.P.DEFAULTS;qty=1000;entry=10.;exit=10.03
        fees=R.P.ASharePaperAccount.costs(entry*qty,True,cfg)['total']+R.P.ASharePaperAccount.costs(exit*qty,False,cfg)['total']
        pnl=R.P.money((exit-entry)*qty-fees)
        result=dict(summary=dict(initial_capital=100000.,end_equity=100000+pnl),open_positions=[],
            trades=[dict(code='600000',shares=qty,entry_price=entry,exit_price=exit,fees=fees,net_pnl=pnl,config=dict(cfg))])
        before=deepcopy(result);stress=R.fixed_execution_stress(result)
        self.assertGreater(pnl,0);self.assertLess(stress['return_pct'],0)
        self.assertEqual(stress['closed_trades'],1);self.assertEqual(result,before)

    def test_fixed_execution_stress_marks_open_positions_without_fake_exit_fees(self):
        cfg=dict(R.P.DEFAULTS)
        result=dict(summary=dict(initial_capital=100000.,end_equity=100194.9),trades=[],
            open_positions=[dict(code='600000',shares=1000,entry_price=10.,entry_fee=5.1,costs=cfg)])
        stress=R.fixed_execution_stress(result)
        self.assertAlmostEqual(stress['end_equity'],100179.8)
        self.assertEqual(stress['closed_trades'],0)

    def test_reversal_factory_uses_only_prior_daily_prefix(self):
        from test_oversold_reversal import setup_history
        rows,bench=setup_history();d=rows[-1]['date']
        s=dict(daily={'600000':rows},names={'600000':'测试'},benchmark=bench)
        result=R.reversal_radar(s,d)
        self.assertEqual(result['strategy_candidates'][0]['trade_plan']['entry_mode'],'oversold_reversal')
        self.assertTrue(result['oversold_reversal_enabled'])
        s['daily']['600000']=rows+[dict(rows[-1],date='2027-01-01',close=999)]
        self.assertEqual(R.reversal_radar(s,d),result)

    def test_cached_old_replay_is_marked_for_rerun_without_rewriting_it(self):
        with TemporaryDirectory() as tmp,patch.object(R,'CACHE',Path(tmp)):
            path=Path(tmp)/'latest-result.json';old='{"version":"old","results":[]}'
            path.write_text(old);jobs=R.ResearchJobs()
            try:
                result=jobs.snapshot()
                self.assertTrue(result['needs_rerun']);self.assertEqual(result['current_replay_version'],R.REPLAY_VERSION)
                self.assertEqual(path.read_text(),old)
            finally:jobs.pool.shutdown()

    def test_open_event_never_reads_current_bar_close_high_low_or_volume(self):
        rows=[dict(date='2026-11-11',close=10.)]
        first=dict(time='2026-11-12 09:45',open=10.,high=10.2,low=9.9,close=10.1,volume=1000)
        current=dict(time='2026-11-12 10:00',open=10.1,high=10.3,low=10.,close=10.2,volume=2000)
        mapping={('600000',b['time']):b for b in (first,current)}
        a=R.bar_observations({'600000':rows},mapping,{'600000':'测试'},current['time'])
        current.update(high=999.,low=.01,close=999.,volume=99999999)
        b=R.bar_observations({'600000':rows},mapping,{'600000':'测试'},current['time'])
        self.assertEqual(a,b)

    def test_price_limit_observation_preserves_sell_at_upper_and_blocks_buy(self):
        first=dict(time='2026-11-12 09:45',open=10.,high=11.,low=10.,close=11.,volume=1000)
        current=dict(time='2026-11-12 10:00',open=11.,high=11.,low=11.,close=11.,volume=1000)
        mapping={('600000',b['time']):b for b in (first,current)}
        _,observations=R.bar_observations({'600000':[dict(date='2026-11-11',close=10.)]},mapping,{},current['time'])
        self.assertEqual(observations['600000']['quote']['bid'],11.)
        self.assertIsNone(observations['600000']['quote']['ask'])
        current['open']=9.
        _,observations=R.bar_observations({'600000':[dict(date='2026-11-11',close=10.)]},mapping,{},current['time'])
        self.assertIsNone(observations['600000']['quote']['bid'])

    def test_trend_reclaim_uses_next_open_t_plus_one_and_preserves_daily_exit_rule(self):
        from test_continuation import history
        from test_trend_reclaim import bars
        rows,bench=history();today='2026-03-09';minute=bars()
        for t in R.END_TIMES[2:]:minute.append(dict(time=today+' '+t,open=13.18,high=13.25,low=13.1,close=13.2,volume=1000000))
        s=dict(daily={'600000':rows},names={'600000':'测试'},minutes={'600000':minute},
               benchmark=bench+[dict(date=today,close=100.)],start=today,end=today)
        result=R.replay(s,R.P.DEFAULTS,radar_factory=R.reclaim_radar)
        self.assertEqual(len(result['open_positions']),1)
        p=result['open_positions'][0]
        self.assertEqual(p['plan']['entry_mode'],'trend_reclaim');self.assertEqual(p['costs']['exit_ma'],20)
        self.assertIsNone(p['target']);self.assertEqual(result['trades'],[])
        self.assertEqual(datetime.fromtimestamp(p['entry_at']/1000,R.P.BJ).strftime('%H:%M'),'10:00')
        self.assertLessEqual(p['plan']['estimated_stop_risk'],p['plan']['risk_budget'])

    def test_observe_completed_close_then_execute_next_open_without_extra_bar_delay(self):
        from test_stock_setups import rising
        rows=rising();today='2026-03-09'
        bench=[dict(date=r['date'],close=100+i) for i,r in enumerate(rows)]+[dict(date=today,close=166.)]
        bars=[]
        for i,t in enumerate(R.END_TIMES):
            price=13. if i<2 else 14.
            bars.append(dict(time=today+' '+t,open=price,high=max(price,13.2)+.1,low=price-.1,
                             close=13. if i==0 else 14.,volume=1000))
        s=dict(daily={'600000':rows},names={'600000':'测试'},minutes={'600000':bars},benchmark=bench,start=today,end=today)
        result=R.replay(s,R.P.DEFAULTS,radar_factory=R.setups_radar)
        self.assertEqual(len(result['open_positions']),1)
        p=result['open_positions'][0]
        self.assertEqual(p['entry_price'],13.01)
        self.assertEqual(datetime.fromtimestamp(p['entry_at']/1000,R.P.BJ).strftime('%H:%M'),'09:45')
        self.assertEqual(result['trades'],[])

    def test_dual_factory_adds_independent_continuation_and_is_prefix_only(self):
        from test_continuation import history
        rows,bench=history();s=dict(daily={'600000':rows},names={'600000':'测试'},benchmark=bench)
        d=rows[-1]['date'];a=R.dual_radar(s,d)
        self.assertFalse(a['market_pass'])
        self.assertEqual(a['strategy_candidates'][0]['trade_plan']['entry_mode'],'continuation')
        s['daily']['600000']=rows+[dict(rows[-1],date='2027-01-01',close=999.)]
        self.assertEqual(R.dual_radar(s,d),a)

    def test_historical_reuses_plan_t_plus_one_and_costs(self):
        result=R.replay(sample(),R.P.DEFAULTS)
        self.assertGreater(result['summary']['closed_trades'],0)
        t=result['trades'][0];self.assertLess(t['signal_date'],R.P.day(t['entry_at']));self.assertLess(R.P.day(t['entry_at']),R.P.day(t['exit_at']))
        self.assertGreater(t['fees'],0);self.assertGreater(result['summary']['win_rate_pct'],0)
    def test_future_edits_do_not_change_earlier_plan_or_trade(self):
        a=sample();b=sample();b['daily']['600000'][-1]['close']=999
        one=R.replay(a,R.P.DEFAULTS);two=R.replay(b,R.P.DEFAULTS)
        self.assertEqual(one['trades'],two['trades'])
    def test_metrics_separate_monthly_returns_and_geometric_equivalent(self):
        summary,months=R.metrics([{'date':'2026-08-01','equity':100000},{'date':'2026-08-31','equity':110000},{'date':'2026-09-30','equity':99000}],[],0)
        self.assertAlmostEqual(summary['return_pct'],-1);self.assertAlmostEqual(summary['max_drawdown_pct'],10)
        self.assertAlmostEqual(months[0]['return_pct'],10);self.assertAlmostEqual(months[1]['return_pct'],-10)
        self.assertIsNone(summary['win_rate_pct']);self.assertLess(summary['monthly_equivalent_pct'],0)
    def test_compare_does_not_modify_live_account_or_auto_select_strategy(self):
        result=R.compare(sample(),R.P.DEFAULTS)
        self.assertEqual([r['case'] for r in result['results']],['baseline','exit_ma20','cost_double','setups_v2','setups_v2_cost_double','candidate_expanded','continuation_v1','dual_v3','dual_v3_cost_double','trend_reclaim_v1','trend_reclaim_cost_double'])
        self.assertIn('旧版',result['results'][0]['label'])
        self.assertEqual(result['results'][3]['strategy_version'],'a-setups-v2')
        self.assertIn('limitations',result);self.assertIn('sample_sha256',result)

    def test_new_factory_uses_completed_prefix_and_separate_signal_types(self):
        from test_stock_setups import rising
        rows=rising();d=rows[-1]['date']
        s=dict(daily={'600000':rows},names={'600000':'测试'},benchmark=[dict(date=r['date'],close=100+i) for i,r in enumerate(rows)])
        r=R.setups_radar(s,d)
        self.assertEqual(r['strategy_candidates'][0]['trade_plan']['strategy_rule'],'trend_pullback')
        s['daily']['600000']=rows+[dict(rows[-1],date='2027-01-01',close=999.)]
        self.assertEqual(R.setups_radar(s,d),r)

if __name__=='__main__':unittest.main()
