import unittest
from unittest.mock import patch
import signal_hold

class SignalHoldTests(unittest.TestCase):
    def bars(self):
        return [{'t':i*900, 'o':100+i, 'h':101+i, 'l':99+i, 'c':100+i, 'v':100, 'pv':(100+i)*100} for i in range(8)]

    def test_holds_through_repeated_long_until_wait(self):
        signals = [{'signal':'做多'}, {'signal':'做多'}, {'signal':'观望'}, {'signal':'观望'}, {'signal':'观望'}, {'signal':'观望'}]
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trades=signal_hold.replay(self.bars(), 4)
        self.assertEqual(len(trades),1)
        self.assertEqual(trades[0]['entry_t'], 3*900)
        self.assertEqual(trades[0]['exit_t'], 5*900)
        self.assertEqual(trades[0]['reason'], '观望')
        self.assertAlmostEqual(trades[0]['pnl_pct'], (105/103-1)*100, places=4)

    def test_short_exits_when_long_signal_occurs(self):
        signals = [{'signal':'做空'}, {'signal':'做多'}, {'signal':'观望'}, {'signal':'观望'}, {'signal':'观望'}, {'signal':'观望'}]
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trades=signal_hold.replay(self.bars(), 4)
        self.assertEqual(trades[0]['side'], '做空')
        self.assertEqual(trades[0]['exit_t'], 4*900)
        self.assertEqual(trades[0]['reason'], '做多')

    def test_existing_long_holds_when_wait_with_bull_structure(self):
        bars=self.bars()
        signals=[{'signal':'做多','structure':'多头','S':95}, {'signal':'观望','structure':'多头'}, {'signal':'观望','structure':'震荡'}, {'signal':'观望','structure':'震荡'}, {'signal':'观望','structure':'震荡'}, {'signal':'观望','structure':'震荡'}]
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trade=signal_hold.replay(bars, 4, mode='structure')[0]
        self.assertEqual(trade['exit_t'], 5*900)
        self.assertEqual(trade['reason'], '结构失效')

    def test_long_stop_triggers_intrabar_with_gap_handling(self):
        bars=self.bars(); bars[3].update(o=103,l=94)
        signals=[{'signal':'做多','structure':'多头','S':95}] + [{'signal':'观望','structure':'震荡'}]*5
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trade=signal_hold.replay(bars,4,mode='structure')[0]
        self.assertEqual(trade['reason'], '结构止损')
        self.assertEqual(trade['exit'], 95)

    def test_long_stop_gap_exits_at_worse_open(self):
        bars=self.bars(); bars[3].update(o=103,l=100); bars[4].update(o=93,l=92)
        signals=[{'signal':'做多','structure':'多头','S':95}] + [{'signal':'观望','structure':'多头'}]*5
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trade=signal_hold.replay(bars,4,mode='structure')[0]
        self.assertEqual(trade['reason'], '结构止损')
        self.assertEqual(trade['exit'], 93)

    def test_signal_receives_prior_extremes_even_after_price_reverses(self):
        bars=self.bars()[:4]
        bars[2]['h']=150
        bars[2]['l']=80
        with patch.object(signal_hold, 'compute_intraday_signal', return_value={'signal':'观望'}) as compute:
            signal_hold.signal_at_close(bars,4)
        self.assertEqual(compute.call_args.kwargs['path_h'],150)
        self.assertEqual(compute.call_args.kwargs['path_l'],80)
        self.assertEqual(compute.call_args.kwargs['orb_h'],102)

    def test_bar_close_stop_exits_at_next_open(self):
        bars=self.bars(); bars[3].update(o=103,l=94);bars[4]['o']=97
        signals=[{'signal':'做多','structure':'多头','S':95}] + [{'signal':'观望','structure':'多头'}]*5
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trade=signal_hold.replay(bars,4,mode='structure',stop_mode='bar-close')[0]
        self.assertEqual(trade['reason'],'结构止损')
        self.assertEqual(trade['exit'],97)
        self.assertEqual(trade['exit_t'],4*900)

    def test_two_bar_confirmation_does_not_enter_on_one_off_signal(self):
        bars=self.bars()
        signals=[{'signal':'做多','structure':'多头','S':90}, {'signal':'观望','structure':'震荡'}, {'signal':'做多','structure':'多头','S':90}, {'signal':'做多','structure':'多头','S':90}, {'signal':'观望','structure':'多头'}, {'signal':'观望','structure':'震荡'}]
        with patch.object(signal_hold, 'signal_at_close', side_effect=signals):
            trade=signal_hold.replay(bars, 4, mode='structure', confirmations=2)[0]
        self.assertEqual(trade['entry_t'], 6*900)
        self.assertEqual(trade['entry_signal_t'], 5*900)

if __name__=='__main__': unittest.main()
