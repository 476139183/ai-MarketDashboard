from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from crypto_ema_replay import normalize_candles, simulate, strategy_signals, Rules, buy_and_hold

STEP=14400000
SMALL=300000

def bar(t,o=100,h=101,l=99,c=100):return dict(t=t,o=o,h=h,l=l,c=c,v=1)

class CryptoReplayTests(unittest.TestCase):
    def test_raw_candles_validate_gaps_conflicts_and_ignore_unconfirmed(self):
        raw=[['0','100','101','99','100','1','1','100','1'],['300000','100','101','99','100','1','1','100','1']]
        self.assertEqual(len(normalize_candles(raw,SMALL,0,600000)),2)
        with self.assertRaises(ValueError):normalize_candles(raw[:1],SMALL,0,600000)
        with self.assertRaises(ValueError):normalize_candles(raw+[raw[0][:4]+['101']+raw[0][5:]],SMALL,0,600000)
        with self.assertRaises(ValueError):normalize_candles([raw[0],raw[1][:-1]+['0']],SMALL,0,600000)

    def test_entry_waits_next_execution_bar_and_ambiguous_stop_precedes_target(self):
        rows=[bar(0),bar(SMALL,h=105,l=97),bar(2*SMALL)]
        result=simulate({'BTC':rows},{'BTC':{0:1}},0,3*SMALL,rules=Rules(fee_rate=0,slippage_rate=0),window=STEP)
        trade=result['trades'][0]
        self.assertEqual(trade['entry_at'],SMALL)
        self.assertEqual(trade['exit_reason'],'stop_loss')
        self.assertEqual(trade['exit_reference'],98)
        self.assertEqual(len(result['trades']),1)
        self.assertAlmostEqual(result['net_profit'],-20)
        self.assertEqual(result['ambiguous_bars'],1)

    def test_short_target_and_gap_stop_apply_adverse_fills_and_fees(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL,o=90,h=91,l=89,c=90)]
        r=simulate({'BTC':rows},{'BTC':{0:-1}},0,3*SMALL,rules=Rules())
        trade=r['trades'][0]
        self.assertEqual(trade['exit_reason'],'take_profit')
        self.assertGreater(trade['net_pnl'],0)
        self.assertGreater(trade['fees'],0)
        self.assertLess(trade['entry_price'],100)
        self.assertGreater(trade['exit_price'],90)
        self.assertAlmostEqual(r['final_equity'],10000+sum(t['net_pnl'] for t in r['trades']))

    def test_neutral_closed_signal_exits_without_same_bar_reversal(self):
        rows=[bar(t) for t in range(0,STEP+3*SMALL,SMALL)]
        result=simulate({'BTC':rows},{'BTC':{0:1,STEP:0}},0,STEP+3*SMALL,rules=Rules(fee_rate=0,slippage_rate=0))
        self.assertEqual(len(result['trades']),1)
        self.assertEqual(result['trades'][0]['exit_reason'],'signal_invalid')
        self.assertEqual(result['trades'][0]['exit_at'],STEP)

    def test_strategy_uses_only_closed_past_bars(self):
        rows=[bar(i*STEP,o=100+i,h=102+i,l=99+i,c=101+i) for i in range(130)]
        full=strategy_signals(rows,120*STEP,130*STEP)
        changed=rows[:125]+[bar(i*STEP,o=1000,h=2000,l=1,c=2) for i in range(125,130)]
        other=strategy_signals(changed,120*STEP,130*STEP)
        self.assertEqual({t:v for t,v in full.items() if t<=125*STEP},{t:v for t,v in other.items() if t<=125*STEP})
        self.assertEqual(full[120*STEP],1)

    def test_combined_account_reconciles_three_symbols_and_end_liquidation(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL,o=101,h=101,l=100,c=101)]
        result=simulate({s:rows for s in ('BTC','ETH','SOL')},{s:{0:1} for s in ('BTC','ETH','SOL')},0,3*SMALL,rules=Rules(fee_rate=0,slippage_rate=0))
        self.assertEqual(len(result['trades']),3)
        self.assertTrue(all(t['exit_reason']=='end_of_test' for t in result['trades']))
        self.assertAlmostEqual(result['final_equity'],10030)
        self.assertLessEqual(result['max_positions'],3)

    def test_funding_is_paid_by_long_and_received_by_short(self):
        rows=[bar(0),bar(SMALL),bar(2*SMALL)]
        for side in (1,-1):
            r=simulate({'BTC':rows},{'BTC':{0:side}},0,3*SMALL,
                       rules=Rules(fee_rate=0,slippage_rate=0),funding={'BTC':{2*SMALL:.001}})
            self.assertAlmostEqual(r['funding_cost'],side)
            self.assertAlmostEqual(r['net_profit'],-side)

    def test_signal_formula_matches_production_rolling_closed_window(self):
        from contract_radar import analyze_contract
        rows=[bar(i*STEP,o=100+i,h=102+i,l=99+i,c=101+i) for i in range(130)]
        boundary=120*STEP
        raw=[[r['t'],r['o'],r['h'],r['l'],r['c'],r['v'],100] for r in rows[1:120]]
        production=analyze_contract('BTCUSDT','BTC','crypto',{'lastPr':220,'ts':boundary},raw,boundary)
        self.assertEqual(strategy_signals(rows,boundary,boundary+STEP)[boundary],{'bullish':1,'bearish':-1,'observe':0}[production['direction']])

    def test_intrabar_exit_cannot_free_slot_for_an_earlier_same_bar_entry(self):
        btc=[bar(0),bar(SMALL),bar(2*SMALL,h=101,l=97),bar(3*SMALL)]
        eth=[bar(i*SMALL) for i in range(4)]
        result=simulate({'BTC':btc,'ETH':eth},{'BTC':{0:1},'ETH':{0:1}},0,4*SMALL,
                        rules=Rules(max_positions=1,fee_rate=0,slippage_rate=0))
        eth_trade=next(t for t in result['trades'] if t['symbol']=='ETH')
        self.assertEqual(eth_trade['entry_at'],3*SMALL)

    def test_benchmark_uses_same_1000_per_symbol_and_keeps_unused_cash(self):
        rows=[bar(0),bar(SMALL,o=110,h=110,l=110,c=110)]
        r=buy_and_hold({'BTC':rows,'ETH':rows,'SOL':rows},0,2*SMALL,
                       rules=Rules(fee_rate=0,slippage_rate=0))
        self.assertAlmostEqual(r['net_profit'],300)
        self.assertAlmostEqual(r['return_pct'],3)
        self.assertAlmostEqual(r['final_equity'],10300)

if __name__=='__main__':unittest.main()
