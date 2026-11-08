from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from crypto_ema_replay import Rules,simulate,STEP,SMALL,daily_trend_signals
from test_crypto_ema_replay import bar

class CryptoMarginTests(unittest.TestCase):
    def rules(self,**overrides):return Rules(leverage=10,margin_fraction=.2,fee_rate=0,slippage_rate=0,**overrides)

    def test_20_percent_margin_at_10x_means_20000_notional(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL)]
        result=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=self.rules())
        t=result['trades'][0]
        self.assertAlmostEqual(t['margin'],2000)
        self.assertAlmostEqual(t['notional'],20000)
        self.assertAlmostEqual(t['quantity'],200)
        self.assertAlmostEqual(result['final_equity'],10000)

    def test_two_percent_price_stop_costs_four_percent_of_wallet(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL,h=101,l=97)]
        r=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=self.rules())
        self.assertAlmostEqual(r['net_profit'],-400)
        self.assertAlmostEqual(r['return_pct'],-4)

    def test_funding_is_charged_on_leveraged_notional(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL)]
        r=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=self.rules(),funding={'BTC':{2*SMALL:.001}})
        self.assertAlmostEqual(r['funding_cost'],20)
        self.assertAlmostEqual(r['final_equity'],9980)

    def test_three_positions_use_60_percent_collateral_and_six_times_gross(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL)]
        r=simulate({s:rows for s in ('BTC','ETH','SOL')},{s:{0:1} for s in ('BTC','ETH','SOL')},0,3*SMALL,rules=self.rules())
        self.assertEqual(r['max_positions'],3)
        self.assertAlmostEqual(r['max_margin_utilization_pct'],60)
        self.assertAlmostEqual(r['max_gross_leverage'],6)
        self.assertAlmostEqual(r['final_equity'],10000)

    def test_mark_liquidation_boundary_is_not_silently_treated_as_normal_stop(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL,o=80,h=81,l=79,c=80)]
        with self.assertRaisesRegex(ValueError,'liquidation'):
            simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=self.rules())

    def test_daily_bias_only_uses_completed_days(self):
        rows=[bar(i*STEP,o=1000-i,h=1001-i,l=998-i,c=999-i) for i in range(500)]
        start=70*86400000
        bias=daily_trend_signals(rows,start,start+STEP)
        self.assertEqual(bias[start],-1)
        changed=[dict(r,c=2000,h=2001,o=2000,l=1999) if r['t']>=start else r for r in rows]
        self.assertEqual(daily_trend_signals(changed,start,start+STEP)[start],bias[start])

    def test_after_loss_next_entry_margin_is_recomputed_from_current_equity(self):
        rows=[bar(t) for t in range(0,STEP+3*SMALL,SMALL)]
        rows[2]=bar(2*SMALL,h=101,l=97)
        r=simulate({'BTC':rows},{'BTC':{0:1,STEP:1}},0,STEP+3*SMALL,rules=self.rules())
        self.assertEqual(len(r['trades']),2)
        self.assertAlmostEqual(r['trades'][0]['margin'],2000)
        self.assertAlmostEqual(r['trades'][1]['margin'],1920)
        self.assertAlmostEqual(r['trades'][1]['equity_at_entry'],9600)

    def test_opening_cap_clips_third_collateral_after_existing_positions_lose(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL,o=99,h=99,l=99,c=99),bar(3*SMALL,o=99,h=99,l=99,c=99)]
        sol=[bar(i*SMALL) for i in range(4)]
        signals={'BTC':{0:1,2*SMALL:1},'ETH':{0:1,2*SMALL:1},'SOL':{0:0,2*SMALL:1}}
        r=simulate({'BTC':rows,'ETH':rows,'SOL':sol},signals,0,4*SMALL,window=2*SMALL,
                   rules=self.rules(max_margin_fraction=.6))
        trade=next(t for t in r['trades'] if t['symbol']=='SOL')
        self.assertAlmostEqual(trade['initial_margin'],1760)
        self.assertLessEqual(trade['opening_margin_utilization_pct'],60+1e-7)

    def test_isolated_funding_changes_locked_margin_without_double_charge(self):
        rows=[bar(i*SMALL,h=100,l=100) for i in range(3)]
        r=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=self.rules(),funding={'BTC':{2*SMALL:.09}})
        t=r['trades'][0]
        self.assertAlmostEqual(t['initial_margin'],2000)
        self.assertAlmostEqual(t['margin'],200)
        self.assertAlmostEqual(r['final_equity'],8200)

    def test_entry_triggers_are_distinct_from_persistent_holding_state(self):
        rows=[bar(t) for t in range(0,STEP+3*SMALL,SMALL)]
        rows[2]=bar(2*SMALL,h=101,l=97)
        r=simulate({'BTC':rows},{'BTC':{0:1,STEP:1}},0,STEP+3*SMALL,rules=self.rules(),
                   entry_signals={'BTC':{0:1,STEP:0}})
        self.assertEqual(len(r['trades']),1)

    def test_atr_style_risk_schedule_can_disable_fixed_target(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL,o=101,h=105,l=100,c=104)]
        r=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=self.rules(),
                   risk_schedule={'BTC':{0:dict(stop_pct=.03,target_pct=None)}})
        self.assertEqual(r['trades'][0]['exit_reason'],'end_of_test')
        self.assertAlmostEqual(r['trades'][0]['stop_price'],97)

if __name__=='__main__':unittest.main()
