import unittest

from market_scan import exclude_locked_signals, qualification_changes


class MarketScanTest(unittest.TestCase):
    def test_distinguishes_new_continuing_and_no_longer_qualified(self):
        changes = qualification_changes([{'code': '600001'}, {'code': '600002'}],
                                        [{'code': '600001'}, {'code': '600003'}])
        self.assertEqual(changes['new_codes'], ['600002'])
        self.assertEqual(changes['continuing_codes'], ['600001'])
        self.assertEqual(changes['no_longer_qualified_codes'], ['600003'])

    def test_limit_up_signal_is_excluded_instead_of_filling_top_five(self):
        rows = [{'code': '600001'}, {'code': '600002'}]
        histories = {
            '600001': [{'close': 10}, {'close': 11, 'high': 11, 'low': 10}],
            '600002': [{'close': 10}, {'close': 10.5, 'high': 10.6, 'low': 10}],
        }
        metadata = {r['code']: {'board': '沪市主板'} for r in rows}
        kept, excluded = exclude_locked_signals(rows, histories, metadata)
        self.assertEqual([r['code'] for r in kept], ['600002'])
        self.assertIn('600001', excluded)


if __name__ == '__main__':
    unittest.main()
