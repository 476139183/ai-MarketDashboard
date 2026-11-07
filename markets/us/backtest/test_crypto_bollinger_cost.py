"""Tests for the independently published strategy06 and its replay engine."""
from pathlib import Path
import math
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from crypto_bollinger_cost import entry_allowed,strategy_signals,replay_bundle
from crypto_replay_engine import Rules,simulate,STEP,SMALL


def bar(t,o=100,h=101,l=99,c=100):return dict(t=t,o=o,h=h,l=l,c=c,v=10)


class BollingerCostTests(unittest.TestCase):
    def test_cost_space_is_directional_and_inclusive_at_threshold(self):
        self.assertFalse(entry_allowed(1,100,99))
        self.assertFalse(entry_allowed(-1,100,101))
        self.assertFalse(entry_allowed(1,100,100.65))
        self.assertTrue(entry_allowed(1,100,100.66))
        self.assertTrue(entry_allowed(-1,100,99.34))
        self.assertFalse(entry_allowed(0,100,101))

    def test_signals_never_change_when_future_candles_change(self):
        rows=[]
        for i in range(330):
            c=93 if i%50==35 else 97 if i%50==36 else 100+2*math.sin(i*math.pi/6)
            rows.append(bar(i*STEP,o=c,h=c+1,l=c-1,c=c))
        original=strategy_signals(rows)
        self.assertEqual(sum(bool(v) for v in original['entry'].values()),6)
        changed=rows[:300]+[bar(i*STEP,o=200,h=210,l=190,c=205) for i in range(300,330)]
        altered=strategy_signals(changed)
        for key in ('state','entry','risk'):
            self.assertEqual({t:v for t,v in original[key].items() if t<=300*STEP},{t:v for t,v in altered[key].items() if t<=300*STEP})
        for t,direction in original['entry'].items():
            if direction:self.assertEqual(direction,original['state'][t])

    def test_flat_history_has_no_entry_and_gaps_are_rejected(self):
        rows=[bar(i*STEP,h=100,l=100) for i in range(80)]
        self.assertFalse(any(strategy_signals(rows)['entry'].values()))
        with self.assertRaisesRegex(ValueError,'Non-contiguous'):
            strategy_signals(rows[:50]+rows[51:])

    def test_engine_uses_10x_margin_and_reconciles_after_funding(self):
        rows=[bar(i*SMALL,h=100,l=100) for i in range(3)]
        rules=Rules(fee_rate=0,slippage_rate=0)
        result=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=rules,funding={'BTC':{2*SMALL:.001}})
        t=result['trades'][0]
        self.assertEqual(t['entry_at'],SMALL)
        self.assertAlmostEqual(t['initial_margin'],2000)
        self.assertAlmostEqual(t['notional'],20000)
        self.assertAlmostEqual(t['margin'],1980)
        self.assertAlmostEqual(result['final_equity'],9980)
        self.assertAlmostEqual(sum(x['net_pnl'] for x in result['trades']),result['net_profit'])

    def test_stop_and_target_same_bar_uses_stop_first(self):
        rows=[bar(0),bar(SMALL,h=105,l=97)]
        result=simulate({'BTC':rows},{'BTC':{0:1}},0,2*SMALL,rules=Rules(fee_rate=0,slippage_rate=0))
        self.assertEqual(result['trades'][0]['exit_reason'],'stop_loss')
        self.assertEqual(result['ambiguous_bars'],1)

    def test_stop_does_not_create_a_new_entry_without_trigger(self):
        rows=[bar(t) for t in range(0,STEP+3*SMALL,SMALL)];rows[2]=bar(2*SMALL,h=101,l=97)
        result=simulate({'BTC':rows},{'BTC':{0:1,STEP:1}},0,STEP+3*SMALL,
                        entry_signals={'BTC':{0:1,STEP:0}},rules=Rules(fee_rate=0,slippage_rate=0))
        self.assertEqual(result['trade_count'],1)

    def test_incomplete_mark_coverage_is_rejected(self):
        rows=[bar(i*STEP) for i in range(40)]
        payload=dict(start=30*STEP,end=31*STEP,data={'BTC':dict(slow=rows,fast=[bar(t) for t in range(30*STEP,31*STEP,SMALL)],marks=[],spec=dict(ctVal='.01',lotSz='.01',minSz='.01'),funding=dict(complete=True,rates={str(30*STEP):.0001}))})
        with self.assertRaisesRegex(ValueError,'mark'):
            replay_bundle(payload)

    def test_short_costs_and_trade_direction_are_symmetric(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL)]
        result=simulate({'BTC':rows},{'BTC':{0:-1}},0,3*SMALL)
        self.assertLess(result['net_profit'],0)
        self.assertTrue(math.isclose(result['final_equity'],10000+result['net_profit']))


if __name__=='__main__':unittest.main()
