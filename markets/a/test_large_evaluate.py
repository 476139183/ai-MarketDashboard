import unittest
from large_evaluate import market_above_ma20


class MarketFilterTest(unittest.TestCase):
    def test_no_future_data_changes_prior_day_filter(self):
        bars = [{'date': f'{i:03d}', 'close': 100 + i} for i in range(25)]
        previous = market_above_ma20(bars[:21])
        later = market_above_ma20(bars)
        self.assertEqual(previous, {x for x in later if x <= '020'})
        self.assertNotIn('018', later)
        self.assertIn('019', later)


if __name__ == '__main__':
    unittest.main()
