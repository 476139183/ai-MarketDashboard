import unittest
from trade_plan import build_trade_plan


def bars(closes):
    out = []
    for i, c in enumerate(closes):
        out.append({'date': f'2026-01-{i+1:02d}', 'open': c, 'high': c + 1, 'low': c - 1, 'close': c, 'volume': 1000})
    return out


class TradePlanTests(unittest.TestCase):
    def test_bull_alignment_builds_sniper_and_checklist(self):
        # 缓慢上行，形成多头且乖离不大
        closes = [100 + i * 0.3 for i in range(25)]
        plan = build_trade_plan(bars(closes))
        self.assertIsNotNone(plan)
        self.assertIn('ideal_buy', plan['sniper'])
        self.assertEqual(len(plan['checklist']), 4)
        self.assertLessEqual(plan['bias_ma5'], 5)

    def test_chase_risk_when_far_above_ma5(self):
        closes = [100] * 20 + [120]
        plan = build_trade_plan(bars(closes))
        self.assertTrue(plan['chase_risk'])
        self.assertFalse(plan['plan_ready'])
        self.assertEqual(plan['checklist'][1]['status'], '不满足')


if __name__ == '__main__':
    unittest.main()
