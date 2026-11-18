import unittest

from adaptive_strategy import candidate_features, profile_accepts, market_allowed, market_breadth, breadth_allowed, build_profiles, rank_adjusted


class AdaptiveStrategyTest(unittest.TestCase):
    def test_selected_pullback_profile_requires_reclaim_not_a_volume_spike(self):
        bars=self.bars()
        bars[-2].update(close=10.3,low=10.29)
        row=candidate_features(bars)
        self.assertTrue(profile_accepts(row,'trend_pullback_breadth_liquidity'))
        self.assertFalse(profile_accepts(row,'moderate_breakout_breadth_liquidity'))
        days=[b['date'] for b in bars]
        index=[{'date':d,'close':100+i} for i,d in enumerate(days)]
        profile='trend_pullback_breadth_liquidity'
        profiles,_=build_profiles({'000001':bars},{'000001':'平安银行'},days,index,start='000',
                                  profile_names=(profile,),breadth={d:1 for d in days})
        live=rank_adjusted({'000001':bars},{'000001':'平安银行'},days,index,profile,{d:1 for d in days})
        self.assertEqual([x['code'] for x in profiles[profile][-1]['stocks']],['000001'])
        self.assertEqual([x['code'] for x in live],['000001'])

    def test_historical_and_current_screening_agree_on_same_profile(self):
        bars=self.bars()
        bars[-1]['volume']=26_000_000
        days=[b['date'] for b in bars]
        index=[{'date':d,'close':100+i} for i,d in enumerate(days)]
        breadth={d:1 for d in days}
        profile='moderate_breakout_breadth_liquidity'
        profiles,_=build_profiles({'000001':bars},{'000001':'平安银行'},days,index,start='000',
                                  profile_names=(profile,),breadth=breadth)
        latest=rank_adjusted({'000001':bars},{'000001':'平安银行'},days,index,profile,breadth)
        self.assertEqual([x['code'] for x in profiles[profile][-1]['stocks']],['000001'])
        self.assertEqual([x['code'] for x in latest],['000001'])

    def test_breadth_counts_active_prices_above_ma20_not_fund_flow(self):
        bars=self.bars()
        other=[dict(b,close=10) for b in bars]
        other[-1]['close']=9
        inactive=[dict(b) for b in bars]
        inactive[-1]['volume']=0
        result=market_breadth({'a':bars,'b':other,'c':inactive},[b['date'] for b in bars])
        self.assertEqual(result['025'],.5)

    def test_breadth_gate_uses_only_prior_six_observations(self):
        days=[f'{i:03d}' for i in range(7)]
        values=dict(zip(days,[.5,.5,.5,.6,.6,.6,.2]))
        self.assertEqual(breadth_allowed(values,days[:6]),{'005'})
        self.assertEqual(breadth_allowed(values,days),{'005'})

    def bars(self):
        return [dict(date=f'{i:03d}',open=10+i*.02,close=10+i*.02,
                     high=10.01+i*.02,low=9.99+i*.02,volume=20_000_000) for i in range(26)]

    def test_moderate_breakout_is_accepted_but_excessive_jump_is_not(self):
        bars = self.bars()
        bars[-1]['volume'] = 26_000_000
        row = candidate_features(bars)
        self.assertIsNotNone(row)
        self.assertTrue(profile_accepts(row,'moderate_breakout'))
        bars[-1].update(close=12,high=12)
        self.assertIsNone(candidate_features(bars))

    def test_volume_is_not_called_capital_flow(self):
        row = candidate_features(self.bars())
        self.assertNotIn('fund_flow',row)
        self.assertIn('volume_ratio',row)

    def test_illiquid_or_flat_history_cannot_generate_a_candidate(self):
        bars = self.bars()
        for bar in bars:
            bar['volume']=100
        self.assertIsNone(candidate_features(bars))

    def test_market_filter_uses_only_past_index_prices(self):
        bars = [{'date':f'{i:03d}','close':100+i} for i in range(24)]
        earlier = market_allowed(bars[:22])
        later = market_allowed(bars)
        self.assertEqual(earlier,{d for d in later if d<='021'})


if __name__ == '__main__':
    unittest.main()
