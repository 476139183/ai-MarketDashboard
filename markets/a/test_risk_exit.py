import unittest

from risk_exit import protective_fill


class RiskExitTest(unittest.TestCase):
    def test_buy_day_cannot_sell_even_if_stop_is_hit(self):
        p = {'entry_date': '2026-07-01', 'entry_price': 100}
        b = dict(date='2026-07-01', open=100, high=101, low=90, close=91, volume=10)
        self.assertIsNone(protective_fill(b, p, 100, 96, 100, .1))

    def test_gap_below_stop_fills_at_open_not_at_stop_price(self):
        p = {'entry_date': '2026-06-30', 'entry_price': 100}
        b = dict(date='2026-07-01', open=93, high=96, low=92, close=95, volume=10)
        self.assertEqual(protective_fill(b, p, 100, 96, 100, .1)['price'], 93)

    def test_prior_peak_stop_does_not_use_today_high(self):
        p = {'entry_date': '2026-06-30', 'entry_price': 100}
        b = dict(date='2026-07-01', open=100, high=120, low=98, close=119, volume=10)
        self.assertIsNone(protective_fill(b, p, 100, 96, 100, .2))

    def test_limit_down_open_does_not_assume_an_exit(self):
        p = {'entry_date': '2026-06-30', 'entry_price': 100}
        b = dict(date='2026-07-01', open=90, high=90, low=90, close=90, volume=10)
        self.assertIsNone(protective_fill(b, p, 100, 96, 100, .1))


if __name__ == '__main__':
    unittest.main()
