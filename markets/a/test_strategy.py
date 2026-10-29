import unittest
from datetime import datetime

from strategy import classify, intraday_signal, backtest


class StrategyTests(unittest.TestCase):
    def test_market_classification_rejects_us_and_unknown(self):
        self.assertEqual(classify('600519'), ('sh600519', '沪市主板'))
        self.assertEqual(classify('300750'), ('sz300750', '创业板'))
        self.assertEqual(classify('688981'), ('sh688981', '科创板'))
        for symbol in ('AAPL', '510300', '123456'):
            with self.assertRaises(ValueError):
                classify(symbol)

    def test_missing_volume_and_stale_data_never_buy(self):
        bars = [{'time': f'2026-10-28 {m}', 'high': 11, 'low': 10,
                 'close': 11, 'volume': 0} for m in ('09:45', '10:00')]
        self.assertEqual(intraday_signal(bars, datetime(2026, 10, 28, 10, 0), True)['action'], '观望')
        for b in bars:
            b['volume'] = 100
        self.assertEqual(intraday_signal(bars, datetime(2026, 10, 28, 10, 30), True, 12, 9)['action'], '观望')

    def test_new_position_requires_overnight_acceptance(self):
        bars = [{'time': f'2026-10-28 {m}', 'high': h, 'low': 10,
                 'close': c, 'volume': 100} for m, h, c in (('09:45', 10.7, 10.6), ('10:00', 11.2, 11.1))]
        self.assertEqual(intraday_signal(bars, datetime(2026, 10, 28, 10, 0), False)['action'], '观望')

    def test_breakout_requires_limits_and_completed_followup_bar(self):
        bars = [{'time': f'2026-10-28 {m}', 'high': h, 'low': 10,
                 'close': c, 'volume': 100} for m, h, c in (('09:45', 10.7, 10.6), ('10:00', 11.2, 11.1))]
        self.assertEqual(intraday_signal(bars, datetime(2026, 10, 28, 10, 0), True)['action'], '观望')
        self.assertEqual(intraday_signal(bars, datetime(2026, 10, 28, 10, 0), True, 12, 9)['action'], '观察做多')

    def test_backtest_does_not_trade_before_signal_or_sell_same_day(self):
        bars = []
        for i in range(35):
            price = 10 + i * .1
            bars.append({'date': f'{i:03d}', 'open': price, 'high': price + .1,
                         'low': price - .1, 'close': price, 'volume': 100})
        result = backtest(bars)
        self.assertTrue(result['trades'])
        for trade in result['trades']:
            self.assertLess(trade['signal_date'], trade['entry_date'])
            if trade.get('exit_date'):
                self.assertLess(trade['entry_date'], trade['exit_date'])


if __name__ == '__main__':
    unittest.main()
