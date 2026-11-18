import unittest
import a_day_replay as D
from test_stock_setups import rising


def fixture():
    rows=rising();signal=rows[-1]['date'];today='2026-03-09'
    benchmark=[dict(date=r['date'],close=100+i) for i,r in enumerate(rows)]+[dict(date=today,close=166.)]
    # Both the observed close and following open must stay inside the plan.
    bars=[dict(time=today+' '+clock,open=13.,high=13.2,low=12.9,close=13.,volume=1000) for clock in ['09:45','10:00','14:45']]
    return dict(signal_date=signal,trade_date=today,daily={'600000':rows},names={'600000':'测试'},benchmark=benchmark,minutes={'600000':bars},failures={})


class DayReplayTests(unittest.TestCase):
    def test_out_of_plan_close_cancels_preparation_before_next_open(self):
        s=fixture();s['minutes']['600000'][0]['close']=13.1
        result=D.replay_day(s,s['trade_date']+'T10:05:00+08:00')
        self.assertEqual(result['account']['positions'],[])

    def test_partial_day_only_fills_after_later_sample_and_cannot_sell_same_day(self):
        s=fixture()
        early=D.replay_day(s,s['trade_date']+'T09:50:00+08:00')
        self.assertEqual(early['account']['positions'],[])
        r=D.replay_day(s,s['trade_date']+'T10:05:00+08:00')
        self.assertEqual(len(r['account']['positions']),1)
        self.assertEqual(r['account']['trades'],[])
        self.assertEqual(r['coverage_end'],s['trade_date']+' 10:00')
        self.assertTrue(r['counterfactual'])

    def test_future_daily_and_minute_values_cannot_change_past_actions(self):
        s=fixture();cutoff=s['trade_date']+'T10:05:00+08:00'
        a=D.replay_day(s,cutoff)
        s['daily']['600000'].append(dict(s['daily']['600000'][-1],date=s['trade_date'],close=999.))
        s['minutes']['600000'][-1]['open']=999.
        b=D.replay_day(s,cutoff)
        self.assertEqual(a['account']['positions'],b['account']['positions'])
        self.assertEqual(a['radar'],b['radar'])

    def test_market_filter_blocks_trades_but_keeps_intraday_observation(self):
        s=fixture();s['benchmark'][-2]['close']=100.
        r=D.replay_day(s,s['trade_date']+'T10:05:00+08:00')
        self.assertFalse(r['radar']['market_pass'])
        self.assertEqual(r['account']['positions'],[])
        self.assertGreater(r['observations'][0]['open_samples_in_plan'],0)

    def test_research_gate_comparison_keeps_identical_plans_and_does_not_change_default(self):
        s=fixture();s['benchmark'][-2]['close']=100.
        off=D.replay_day(s,s['trade_date']+'T10:05:00+08:00',market_filter=False)
        strict=D.replay_day(s,s['trade_date']+'T10:05:00+08:00')
        self.assertEqual(len(off['account']['positions']),1)
        self.assertEqual(strict['account']['positions'],[])
        self.assertEqual(off['observations'][0]['plan'],strict['observations'][0]['plan'])
        self.assertFalse(off['market_filter'])
        self.assertTrue(strict['market_filter'])


if __name__=='__main__':unittest.main()
