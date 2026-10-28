import unittest
from forward_study import horizon_observation


class ForwardStudyTest(unittest.TestCase):
    def setUp(self):
        self.calendar = [f'2026-01-{d:02d}' for d in range(1, 9)]
        self.bars = [{'date': day, 'close': 10, 'volume': 100} for day in self.calendar]

    def test_measures_three_sessions_after_buy_with_costs(self):
        result = horizon_observation(self.bars, self.calendar, '2026-01-02', 10, 3, .1)
        self.assertEqual(result['date'], '2026-01-05')
        self.assertLess(result['return_pct'], 0)

    def test_incomplete_horizon_is_reported(self):
        result = horizon_observation(self.bars, self.calendar, '2026-01-07', 10, 3, .1)
        self.assertEqual(result['status'], '数据尚不足')

    def test_missing_session_is_not_replaced_with_later_price(self):
        bars = [b for b in self.bars if b['date'] != '2026-01-05']
        result = horizon_observation(bars, self.calendar, '2026-01-02', 10, 3, .1)
        self.assertEqual(result['status'], '目标日行情缺失')


if __name__ == '__main__':
    unittest.main()
