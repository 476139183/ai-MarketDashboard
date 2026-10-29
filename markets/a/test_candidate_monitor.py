import unittest
from candidate_monitor import history_issue, record_timing, signal_category


class CandidateMonitorTest(unittest.TestCase):
    def test_candidate_and_watch_are_not_silenced_by_open_positions(self):
        self.assertEqual(signal_category(['trend'], True), '策略候选')
        self.assertEqual(signal_category(['trend'], False), '观察池')
        self.assertEqual(signal_category(['breakout'], True), '观察池')

    def test_later_recording_is_not_described_as_prospective(self):
        self.assertEqual(record_timing('2026-10-27', '2026-10-28T12:00:00+08:00'), '事后记录')
        self.assertEqual(record_timing('2026-10-27', '2026-10-27T18:00:00+08:00'), '信号日记录')

    def test_missing_trading_day_or_zero_volume_blocks_new_signal(self):
        dates = [f'{i:03d}' for i in range(21)]
        bars = [{'date': x, 'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 1} for x in dates]
        self.assertIsNone(history_issue(bars, dates, dates[-1]))
        self.assertIsNotNone(history_issue(bars[:10] + bars[11:], dates, dates[-1]))
        bars[-1]['volume'] = 0
        self.assertIsNotNone(history_issue(bars, dates, dates[-1]))


if __name__ == '__main__':
    unittest.main()
