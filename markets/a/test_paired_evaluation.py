import unittest
from paired_evaluation import paired_summary, choose_rule


class PairedEvaluationTest(unittest.TestCase):
    def test_pair_requires_both_horizons_within_period(self):
        events = [
            {'signal_date': '2025-12-20', '3': {'status': '观察值', 'date': '2025-12-25', 'return_pct': 2},
             '5': {'status': '观察值', 'date': '2026-01-02', 'return_pct': 3}},
            {'signal_date': '2025-11-01', '3': {'status': '观察值', 'date': '2025-11-06', 'return_pct': -1},
             '5': {'status': '观察值', 'date': '2025-11-10', 'return_pct': 2}},
        ]
        result = paired_summary(events, '2025-01-01', '2025-12-31')
        self.assertEqual(result['paired_count'], 1)
        self.assertEqual(result['day3']['wins'], 0)
        self.assertEqual(result['day5']['wins'], 1)

    def test_rule_selection_uses_only_exploration_and_requires_minimum(self):
        results = {'a': {'train': {'paired_count': 100, 'day3': {'positive_pct': 55}, 'day5': {'positive_pct': 50}}},
                   'b': {'train': {'paired_count': 10, 'day3': {'positive_pct': 99}, 'day5': {'positive_pct': 99}}}}
        self.assertEqual(choose_rule(results, minimum=30), ('a', 'day3'))


if __name__ == '__main__':
    unittest.main()
