import unittest

from trend_exit import assess_exit


class TrendExitTest(unittest.TestCase):
    def bars(self):
        return [dict(date=f'{i:03d}', close=100 + i, high=101+i,
                     low=99+i, open=100+i, volume=100) for i in range(25)]

    def test_profitable_uptrend_is_held_even_with_outflow(self):
        bars = self.bars()
        flows = {x['date']: -100 for x in bars[-2:]}
        self.assertEqual(assess_exit(bars, 100, flows)['action'], '持有')

    def test_confirmed_outflow_and_price_weakness_exits(self):
        bars = self.bars()
        bars[-1]['close'] = 120
        flows = {x['date']: -100 for x in bars[-2:]}
        self.assertEqual(assess_exit(bars, 100, flows)['action'], '退出')

    def test_missing_flow_is_not_invented_as_outflow(self):
        bars = self.bars()
        bars[-1]['close'] = 120
        r = assess_exit(bars, 100, {})
        self.assertEqual(r['action'], '持有')
        self.assertEqual(r['flow_status'], '缺少连续两日资金流')

    def test_trend_break_can_exit_without_flow_and_ignores_future_flow(self):
        bars = self.bars()
        bars[-2]['close'], bars[-1]['close'] = 110, 108
        r = assess_exit(bars, 100, {'999': -100})
        self.assertEqual(r['action'], '退出')
        self.assertEqual(r['reason'], '跌破MA10且MA5转弱、价格继续走低')


if __name__ == '__main__':
    unittest.main()
