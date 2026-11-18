import unittest

from selection_study import ranking_key, signal_features, rank_current_original, build_candidates, ranked_signals


class SelectionStudyTest(unittest.TestCase):
    def test_original_full_market_screening_matches_historical_candidates(self):
        bars=[dict(date=f'2026-01-{i+1:02d}',open=10+i*.1,close=10+i*.1,
                   high=10.05+i*.1,low=9.95+i*.1,volume=100) for i in range(26)]
        days=[b['date'] for b in bars]
        meta={'000001':{'name':'平安银行','board':'深市主板'}}
        daily=build_candidates({'000001':bars},meta,days)
        historical=ranked_signals(daily,days,'breakout_desc')[-1]['stocks']
        current=rank_current_original({'000001':bars},{'000001':'平安银行'},days,'breakout_desc')
        self.assertEqual([r['code'] for r in historical],['000001'])
        self.assertEqual([r['code'] for r in current],['000001'])
        self.assertEqual(historical[0]['breakout_distance_pct'],current[0]['breakout_distance_pct'])

    def test_small_breakout_and_large_breakout_order_are_opposite(self):
        a={'code':'a','breakout_distance_pct':1,'turnover':100,'atr_pct':2,'efficiency':.5,'risk_momentum':1}
        b=dict(a,code='b',breakout_distance_pct=5)
        self.assertEqual(sorted([a,b],key=lambda r:ranking_key(r,'breakout_desc'))[0]['code'],'b')
        self.assertEqual(sorted([a,b],key=lambda r:ranking_key(r,'breakout_asc'))[0]['code'],'a')

    def test_signal_day_spike_does_not_create_a_past_efficiency_trend(self):
        bars=[dict(close=10 if i%2==0 else 10.1,open=10,high=10.2,low=9.9,volume=100) for i in range(21)]
        before=signal_features(bars)
        bars[-1].update(close=11,high=11.1)
        after=signal_features(bars)
        self.assertEqual(before['efficiency'],after['efficiency'])
        self.assertLess(after['efficiency'],.1)


if __name__=='__main__':
    unittest.main()
