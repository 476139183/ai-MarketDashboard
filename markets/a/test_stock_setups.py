from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
import unittest
import stock_setups as S


def rising():
    rows=[]
    for i in range(65):
        c=10+i*.05
        rows.append(dict(date=(date(2026,1,1)+timedelta(days=i)).isoformat(),open=c-.02,high=c+.15,low=c-.15,close=c,volume=1000.))
    rows[10]['high']=14.2
    # Recent pullback touches its own MA10 without requiring a MA20 reclaim.
    rows[-2]['low']=12.8
    return rows


class StockSetupTests(unittest.TestCase):
    def test_sustained_uptrend_can_qualify_without_ma20_cross(self):
        rows=rising();out=S.evaluate_setups(rows)
        self.assertIn('trend_pullback',[p['rule'] for p in out])
        plan=next(p['plan'] for p in out if p['rule']=='trend_pullback')
        self.assertTrue(plan['plan_ready'],plan)
        self.assertEqual(plan['strategy_version'],S.VERSION)

    def test_breakout_needs_volume_and_close_confirmation(self):
        rows=rising();rows[-1].update(open=13.2,close=13.7,high=13.72,low=13.18,volume=2000.)
        self.assertIn('volume_breakout',[p['rule'] for p in S.evaluate_setups(rows)])
        rows[-1]['volume']=1000.
        self.assertNotIn('volume_breakout',[p['rule'] for p in S.evaluate_setups(rows)])

    def test_reclaim_without_rising_ma20_is_not_confirmed(self):
        rows=rising()
        for i,r in enumerate(rows):
            c=20-i*.08;r.update(open=c,close=c,high=c+.1,low=c-.1)
        rows[-1].update(open=15.2,close=16.3,high=16.35,low=15.1,volume=3000.)
        self.assertNotIn('ma20_reclaim',[p['rule'] for p in S.evaluate_setups(rows)])

    def test_reclaim_needs_both_cross_and_volume_confirmation(self):
        rows=rising();rows[-2].update(open=12.38,close=12.4,high=12.55,low=12.3)
        rows[-1].update(open=13.18,close=13.2,high=13.22,low=12.9,volume=2000.)
        self.assertTrue(S.entry_signal(rows,'trend'))
        self.assertIn('ma20_reclaim',[p['rule'] for p in S.evaluate_setups(rows)])
        rows[-1]['volume']=1000.
        self.assertNotIn('ma20_reclaim',[p['rule'] for p in S.evaluate_setups(rows)])

    def test_prices_are_cent_executable_and_insufficient_upside_is_rejected(self):
        rows=rising();plan=S.build_setup_plan(rows,'trend_pullback')
        z=plan['sniper']
        for v in z.values():
            if isinstance(v,(float,int)):self.assertEqual(Decimal(str(v))*100,(Decimal(str(v))*100).to_integral_value())
        self.assertGreaterEqual(round(z['ideal_buy']-z['secondary_buy'],2),.01)
        self.assertGreaterEqual(plan['risk_reward'],1.5)
        rows[10]['high']=S.ma([r['close'] for r in rows],10)*1.01
        # Bound the past resistance just above the entry ceiling.
        for r in rows[:-1]:r['high']=max(r['open'],r['close'])+.001
        plan=S.build_setup_plan(rows,'trend_pullback')
        self.assertFalse(plan['plan_ready'])

    def test_bad_data_and_future_suffix_cannot_leak(self):
        rows=rising();before=S.evaluate_setups(rows)
        changed=deepcopy(rows);changed[-1]['volume']=float('nan')
        self.assertEqual(S.evaluate_setups(changed),[])
        future=deepcopy(rows);future.append(dict(rows[-1],date='2027-01-01',close=999.))
        self.assertEqual(S.evaluate_setups(future[:len(rows)]),before)
        self.assertEqual(S.evaluate_setups(list(reversed(rows))),[])


if __name__=='__main__':unittest.main()
