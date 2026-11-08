from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from crypto_ema_replay import Rules,simulate,SMALL,STEP
from test_crypto_ema_replay import bar
from crypto_trade_review import allow_entry,audit_trade,adjusted_models
from crypto_strategy_experiments import strategy_catalog

class TradeReviewTests(unittest.TestCase):
    def test_reversion_cannot_enter_after_middle_has_been_passed(self):
        snap=dict(room=-.001,adx=15,adx_old=16,daily=1)
        self.assertFalse(allow_entry('room_positive',1,snap))
        self.assertFalse(allow_entry('cost_room',1,snap))
        snap['room']=.007
        self.assertTrue(allow_entry('cost_room',1,snap))

    def test_daily_and_adx_are_separate_entry_filters(self):
        snap=dict(room=.01,adx=19,adx_old=17,daily=-1)
        self.assertFalse(allow_entry('adx_not_rising',-1,snap))
        self.assertTrue(allow_entry('daily_aligned',-1,snap))
        self.assertFalse(allow_entry('daily_aligned',1,snap))

    def test_audit_rejects_fee_tampering_and_future_exit_bar_extremes(self):
        rows=[bar(i*SMALL,h=100,l=100) for i in range(4)]
        rows[-1]=bar(3*SMALL,o=101,h=180,l=90,c=170)
        rules=Rules(fee_rate=.0006,slippage_rate=.0005)
        seed=dict(state={0:1,3*SMALL:0},entry={0:1,3*SMALL:0},risk={})
        result=simulate({'BTC':rows},{'BTC':seed['state']},0,4*SMALL,rules=rules,window=3*SMALL)
        trade=result['trades'][0]
        data=dict(fast=rows,marks=rows,spec={},funding=dict(rates={}))
        checked=audit_trade(trade,data,seed,rules,4*SMALL,window=3*SMALL)
        self.assertEqual(checked['issues'],[])
        self.assertLess(checked['mfe_pct'],1.1)
        changed=dict(trade,fees=trade['fees']+1)
        self.assertIn('fees',audit_trade(changed,data,seed,rules,4*SMALL,window=3*SMALL)['issues'])

    def test_adjustments_do_not_read_future_and_entry_filters_keep_exit_state(self):
        rows=[bar(i*STEP,o=100+i%17,h=102+i%17,l=98+i%17,c=101+i%17) for i in range(330)]
        seed=strategy_catalog(rows)['bollinger_20']
        first=adjusted_models(rows,seed)
        changed=rows[:300]+[bar(i*STEP,o=200,h=202,l=190,c=195) for i in range(300,330)]
        second=adjusted_models(changed,strategy_catalog(changed)['bollinger_20'])
        self.assertEqual(len(first),7)
        for name in first:
            for key in ('state','entry','risk'):
                self.assertEqual({t:v for t,v in first[name][key].items() if t<=300*STEP},{t:v for t,v in second[name][key].items() if t<=300*STEP})
        for name in ('room_positive','cost_room','adx_not_rising','daily_aligned'):
            self.assertEqual(first[name]['state'],seed['state'])

if __name__=='__main__':unittest.main()
