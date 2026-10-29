import unittest
from unittest.mock import patch
import session_replay as replay

class ReplayTests(unittest.TestCase):
    def test_15m_aggregation_preserves_ohlcv(self):
        rows = [{'timestamp': 900, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 4},
                {'timestamp': 960, 'open': 11, 'high': 13, 'low': 10, 'close': 12, 'volume': 6}]
        bars = replay.aggregate(rows)
        self.assertEqual((bars[0]['o'], bars[0]['h'], bars[0]['l'], bars[0]['c'], bars[0]['v']), (10, 13, 9, 12, 10))

    def test_session_requires_opening_and_closing_buckets(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ny = ZoneInfo('America/New_York')
        start = int(datetime(2026, 10, 28, 9, 30, tzinfo=ny).timestamp())
        self.assertFalse(replay.session_complete([{'t': start + 900}, {'t': start + 1800}], '2026-10-28', 'regular'))
        self.assertTrue(replay.session_complete([{'t': start + i * 900} for i in range(26)], '2026-10-28', 'regular'))

    def test_signal_at_close_enters_next_bar_open(self):
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':1} for i in range(5)]
        bars[3].update(o=102,h=103,l=101,c=102)
        signal = {'signal':'做多','entry_ready':True,'entry_low':99,'entry_high':103,'S':95,'T1':110}
        with patch.object(replay, 'compute_intraday_signal', return_value=signal):
            trade = replay.replay_session(bars, 3)
        self.assertEqual(trade['entry'], 102)
        self.assertEqual(trade['entry_t'], bars[3]['t'])

    def test_trend_filter_rejects_signal_against_trend(self):
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':1} for i in range(5)]
        signal = {'signal':'做多','entry_ready':True,'entry_low':99,'entry_high':101,'S':95,'T1':110}
        with patch.object(replay, 'compute_intraday_signal', return_value=signal), patch.object(replay, 'short_term_trend', return_value={'score': -3}):
            self.assertIsNone(replay.replay_session(bars, 3, trend_filter=True))

    def test_target_two_percent_calculated_from_actual_entry(self):
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':1} for i in range(5)]
        bars[3].update(h=103, l=99, c=102)
        signal = {'signal':'做多','entry_ready':True,'entry_low':99,'entry_high':101,'S':98,'T1':110}
        with patch.object(replay, 'compute_intraday_signal', return_value=signal), patch.object(replay, 'short_term_trend', return_value={'score': 3}):
            trade = replay.replay_session(bars, 3, trend_filter=True, target_pct=2)
        self.assertEqual(trade['target'], 102)
        self.assertEqual(trade['reason'], 'target')

    def test_stricter_trend_threshold_rejects_weak_direction(self):
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':1} for i in range(5)]
        signal = {'signal':'做多','entry_ready':True,'entry_low':99,'entry_high':101,'S':98,'T1':110}
        with patch.object(replay, 'compute_intraday_signal', return_value=signal), patch.object(replay, 'short_term_trend', return_value={'score': 2}):
            self.assertIsNone(replay.replay_session(bars, 3, trend_filter=True, trend_threshold=3))

    def test_minimum_risk_reward_filters_wide_stop(self):
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':1} for i in range(5)]
        signal = {'signal':'做多','entry_ready':True,'entry_low':99,'entry_high':101,'S':98,'T1':110}
        with patch.object(replay, 'compute_intraday_signal', return_value=signal), patch.object(replay, 'short_term_trend', return_value={'score': 3}):
            self.assertIsNone(replay.replay_session(bars, 3, trend_filter=True, target_pct=2, min_reward_risk=1.5))

    def test_simultaneous_stop_and_target_is_stop_first(self):
        bars = [{'t':i*900,'o':100,'h':101,'l':99,'c':100,'v':1} for i in range(5)]
        bars[3].update(o=100,h=111,l=94,c=100)
        signal = {'signal':'做多','entry_ready':True,'entry_low':99,'entry_high':101,'S':95,'T1':110}
        with patch.object(replay, 'compute_intraday_signal', return_value=signal):
            trade = replay.replay_session(bars, 3)
        self.assertEqual(trade['reason'], 'stop')
        self.assertEqual(trade['exit'], 95)

if __name__ == '__main__': unittest.main()
