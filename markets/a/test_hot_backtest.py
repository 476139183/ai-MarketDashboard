import unittest
from datetime import datetime
from hot_backtest import as_of_date, prepare_symbols


class HotBacktestTest(unittest.TestCase):
    def test_snapshot_uses_only_prior_date_for_daily_data(self):
        self.assertEqual(as_of_date(datetime(2026, 10, 28, 11, 0)), '2026-10-27')

    def test_unique_codes_do_not_hide_unsupported_instruments(self):
        stocks = [{'code': '600519', 'name': '贵州茅台'}, {'code': '600519', 'name': '贵州茅台'},
                  {'code': '830001', 'name': '北交所样本'}, {'code': 'AAPL', 'name': '非A股'}]
        accepted, rejected = prepare_symbols(stocks)
        self.assertEqual([s['code'] for s in accepted], ['600519'])
        self.assertEqual(len(rejected), 2)

    def test_explicit_beijing_stock_is_kept(self):
        accepted, rejected = prepare_symbols([{'code': '920896', 'name': '星展测控', 'exchange': 'BJ'}])
        self.assertEqual(accepted[0]['board'], '北交所')
        self.assertEqual(rejected, [])


if __name__ == '__main__':
    unittest.main()
