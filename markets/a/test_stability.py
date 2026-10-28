import unittest
from stability import monthly_bootstrap


class StabilityTest(unittest.TestCase):
    def test_bootstrap_is_reproducible_and_respects_month_groups(self):
        trades = [{'signal_date': '2026-01-03', 'return_pct': -2},
                  {'signal_date': '2026-02-03', 'return_pct': 4}]
        a = monthly_bootstrap(trades, iterations=100)
        self.assertEqual(a, monthly_bootstrap(trades, iterations=100))
        self.assertEqual(a['months'], 2)
        self.assertAlmostEqual(a['mean_pct'], 1)
        self.assertLessEqual(a['p05_pct'], a['p95_pct'])


if __name__ == '__main__':
    unittest.main()
