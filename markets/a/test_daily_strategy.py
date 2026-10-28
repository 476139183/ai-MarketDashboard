import unittest
from daily_strategy import entry_signal, run


class DailyStrategyTest(unittest.TestCase):
    def test_signal_uses_prior_close_and_exit_respects_t_plus_one(self):
        bars = []
        for i in range(35):
            price = 10 + i * .1 if i < 25 else 8
            bars.append({'date': f'{i:03d}', 'open': price, 'high': price + .05,
                         'low': price - .05, 'close': price, 'volume': 100})
        result = run(bars, .2)
        self.assertTrue(result['trades'])
        for trade in result['trades']:
            self.assertLess(trade['signal_date'], trade['entry_date'])
            self.assertLess(trade['entry_date'], trade['exit_date'])
            self.assertLess(trade['exit_signal_date'], trade['exit_date'])

    def test_truncated_history_cannot_create_future_signal(self):
        bars = [{'date': f'{i:03d}', 'open': 10 + i*.1, 'high': 10.05 + i*.1,
                 'low': 9.95 + i*.1, 'close': 10 + i*.1, 'volume': 100} for i in range(22)]
        self.assertFalse(run(bars[:20], .1)['trades'])

    def test_later_bars_do_not_change_already_closed_trades(self):
        prices = [10 + i * .1 for i in range(25)] + [8] * 8
        bars = [{'date': f'{i:03d}', 'open': price, 'high': price + .05,
                 'low': price - .05, 'close': price, 'volume': 100} for i, price in enumerate(prices)]
        closed = run(bars[:30], .2)['trades']
        self.assertTrue(closed)
        self.assertEqual(closed, run(bars, .2)['trades'][:len(closed)])

    def test_rule_names_are_validated(self):
        with self.assertRaises(ValueError):
            run([], .1, entry_rule='unknown')

    def test_market_filter_can_block_all_new_entries(self):
        bars = [{'date': f'{i:03d}', 'open': 10 + i*.1, 'high': 10.05 + i*.1,
                 'low': 9.95 + i*.1, 'close': 10 + i*.1, 'volume': 100} for i in range(25)]
        self.assertIsNotNone(run(bars, .1)['open_position'])
        self.assertIsNone(run(bars, .1, allowed_signal_dates=set())['open_position'])

    def test_buy_signal_expires_after_next_market_day(self):
        bars = [{'date': f'{i:03d}', 'open': 10 + i*.1, 'high': 10.05 + i*.1,
                 'low': 9.95 + i*.1, 'close': 10 + i*.1, 'volume': 100} for i in range(21)]
        bars.append({'date': '023', 'open': 12.3, 'high': 12.35, 'low': 12.25, 'close': 12.3, 'volume': 100})
        result = run(bars, .2, trading_calendar=[f'{i:03d}' for i in range(24)])
        self.assertIsNone(result['open_position'])

    def test_entry_signal_is_available_independently_of_position(self):
        bars = [{'date': f'{i:03d}', 'open': 10 + i*.1, 'high': 10.05 + i*.1,
                 'low': 9.95 + i*.1, 'close': 10 + i*.1, 'volume': 100} for i in range(21)]
        self.assertTrue(entry_signal(bars, 'breakout'))
        self.assertFalse(entry_signal(bars[:20], 'breakout'))


if __name__ == '__main__':
    unittest.main()
