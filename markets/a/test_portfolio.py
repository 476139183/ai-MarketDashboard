import unittest
from portfolio import equity_curve, period_metrics


class PortfolioTest(unittest.TestCase):
    def test_open_position_is_marked_and_exit_includes_fee(self):
        bars = {'a': [{'date': '001', 'close': 10}, {'date': '002', 'close': 12}],
                'b': [{'date': '001', 'close': 5}, {'date': '002', 'close': 5}]}
        results = {'a': {'trades': [], 'open_position': {'entry_date': '001', 'entry_price': 10}},
                   'b': {'trades': [], 'open_position': None}}
        curve = equity_curve(bars, results, 0)
        self.assertAlmostEqual(curve[-1]['equity'], 1.1)
        self.assertAlmostEqual(period_metrics(curve, '001', '002')['return_pct'], 10)


if __name__ == '__main__':
    unittest.main()
