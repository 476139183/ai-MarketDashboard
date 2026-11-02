import unittest
from unittest.mock import patch
from trend_breakout import breakout_signal, replay_breakout

class TrendBreakoutTests(unittest.TestCase):
    def test_requires_five_bars_and_direction_above_orb_and_vwap(self):
        bars = [{'t':i*900,'o':100+i,'h':101+i,'l':99+i,'c':100.5+i,'v':100,'pv':(100.5+i)*100} for i in range(8)]
        self.assertIsNone(breakout_signal(bars[:4], 102, 99))
        self.assertEqual(breakout_signal(bars, 102, 99)['side'], '做多')
        self.assertIsNone(breakout_signal(bars, 120, 99))
        with patch('trend_breakout.short_term_trend', return_value={'score':3}):
            self.assertIsNone(breakout_signal(bars, 102, 99, threshold=4))

    def test_signal_fills_next_open_with_stop_first(self):
        bars = [{'t':i*900,'o':100+i,'h':101+i,'l':99+i,'c':100.8+i,'v':100,'pv':(100.8+i)*100} for i in range(8)]
        bars[3]['l'] = 103
        bars[5].update(o=105, h=110, l=100, c=106)
        trade = replay_breakout(bars, 4)
        self.assertIsNotNone(trade)
        self.assertGreaterEqual(trade['entry_t'], 5*900)
        self.assertEqual(trade['reason'], 'stop')

    def test_minimum_reward_risk_rejects_wide_stop(self):
        from unittest.mock import patch
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':100,'pv':10000} for i in range(8)]
        signal = {'side':'做多','stop':98,'signal_t':0}
        with patch('trend_breakout.breakout_signal', return_value=signal):
            self.assertIsNone(replay_breakout(bars, 4, min_reward_risk=1.5))

if __name__ == '__main__': unittest.main()
