import unittest

from validate_strategy import historical_signals, audit_account


class StrategyValidationTest(unittest.TestCase):
    def fixture(self):
        bars = [dict(date=f'2026-01-{i+1:02d}', open=10+i*.1, high=10.05+i*.1,
                     low=9.95+i*.1, close=10+i*.1, volume=100) for i in range(25)]
        return bars, {'000001': {'name': '平安银行', 'board': '深市主板'}}

    def test_future_prices_cannot_change_past_candidate_lists(self):
        bars, meta = self.fixture()
        first, _ = historical_signals({'000001': bars[:22]}, meta, [x['date'] for x in bars[:22]], '2026-01-01')
        second, _ = historical_signals({'000001': bars}, meta, [x['date'] for x in bars], '2026-01-01')
        self.assertEqual(first, second[:len(first)])
        self.assertEqual(next(s for s in first if s['stocks'])['signal_date'], '2026-01-21')
        self.assertNotIn('recorded_at', first[0])

    def test_missing_daily_bar_excludes_a_signal(self):
        bars, meta = self.fixture()
        days = [x['date'] for x in bars]
        signals, counts = historical_signals({'000001': bars[:10]+bars[11:]}, meta, days, '2026-01-01')
        self.assertTrue(all(not s['stocks'] for s in signals))
        self.assertEqual(sum(counts.values()), 0)

    def test_audit_rejects_same_day_sell_even_if_return_is_positive(self):
        state = {'events': [dict(side='buy', code='000001', date='2026-01-01', price=10, quantity=.1),
                            dict(side='sell', code='000001', date='2026-01-01', price=11, quantity=.1)],
                 'fee_per_side': 0, 'cash': 1.1, 'positions': {}}
        with self.assertRaises(AssertionError):
            audit_account(state)


if __name__ == '__main__':
    unittest.main()
