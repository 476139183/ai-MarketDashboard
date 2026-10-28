import unittest
from candidate_monitor import record_timing, signal_category


class CandidateMonitorTest(unittest.TestCase):
    def test_candidate_and_watch_are_not_silenced_by_open_positions(self):
        self.assertEqual(signal_category(['trend'], True), '策略候选')
        self.assertEqual(signal_category(['trend'], False), '观察池')
        self.assertEqual(signal_category(['breakout'], True), '观察池')

    def test_later_recording_is_not_described_as_prospective(self):
        self.assertEqual(record_timing('2026-10-27', '2026-10-28T12:00:00+08:00'), '事后记录')
        self.assertEqual(record_timing('2026-10-27', '2026-10-27T18:00:00+08:00'), '信号日记录')


if __name__ == '__main__':
    unittest.main()
