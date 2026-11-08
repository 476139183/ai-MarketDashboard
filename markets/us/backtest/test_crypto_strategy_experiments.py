from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from crypto_strategy_experiments import indicators,strategy_catalog,factor_features,apply_factor
from crypto_ema_replay import STEP,SMALL
from test_crypto_ema_replay import bar

class ExperimentTests(unittest.TestCase):
    def test_flat_indicators_are_finite_and_no_false_trend(self):
        rows=[bar(i*STEP,h=100,l=100) for i in range(100)]
        values=indicators(rows)
        self.assertEqual(values['adx'][-1],0)
        self.assertEqual(values['atr'][-1],0)
        self.assertEqual(values['rsi'][-1],50)

    def test_catalog_has_9_price_families_and_causal_breakout(self):
        rows=[bar(i*STEP,o=100+i,h=102+i,l=99+i,c=101+i) for i in range(400)]
        base=strategy_catalog(rows)
        self.assertEqual(len(base),9)
        changed=rows[:300]+[bar(i*STEP,o=2,h=3,l=1,c=2) for i in range(300,400)]
        other=strategy_catalog(changed)
        for name in base:
            self.assertEqual({t:v for t,v in base[name]['state'].items() if t<=300*STEP},{t:v for t,v in other[name]['state'].items() if t<=300*STEP})

    def test_factor_gate_changes_entries_not_holding_state(self):
        original=dict(state={0:1,STEP:1},entry={0:1,STEP:1})
        features={0:dict(flow=-.3,oi_change=.01,rvol=2,turnover_relative=2,poc_delta=1,crowded_long=False,crowded_short=False),STEP:dict(flow=.3,oi_change=.01,rvol=2,turnover_relative=2,poc_delta=1,crowded_long=False,crowded_short=False)}
        variant=apply_factor(original,features,'flow')
        self.assertEqual(variant['state'],original['state'])
        self.assertEqual(variant['entry'],{0:0,STEP:1})

if __name__=='__main__':unittest.main()
