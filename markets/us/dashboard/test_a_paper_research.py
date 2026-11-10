from datetime import datetime,timedelta
import unittest
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
        self.assertEqual([r['case'] for r in result['results']],['baseline','exit_ma20','cost_double','setups_v2','setups_v2_cost_double','candidate_expanded'])
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
