from copy import deepcopy
import unittest
from test_continuation import history,clock
import oversold_reversal as M


def setup_history():
    rows,bench=history()
    for r,c in zip(rows[-5:],[12.8,12.9,12.6,12.3,12.15]):
        r.update(open=c+.04,close=c,high=c+.1,low=c-.2)
    rows[-1].update(open=12.16,high=12.22,low=11.9,close=12.15)
    return rows,bench


def minutes():
    return [dict(time='2026-03-09 '+t,open=o,high=h,low=l,close=c,volume=1000000)
            for t,o,h,l,c in [('09:45',12.,12.1,11.98,12.05),('10:00',12.06,12.22,12.02,12.2)]]


class OversoldReversalTests(unittest.TestCase):
    def test_screen_explains_exclusions_without_loosening_rules(self):
        rows,bench=setup_history()
        result=M.screen(rows,bench,'测试');self.assertEqual(result['stage'],'candidate');self.assertEqual(result['plan'],self.plan())
        self.assertEqual(M.screen(rows,bench,'*ST测试')['stage'],'security')
        for row in rows:row['volume']=1
        self.assertEqual(M.screen(rows,bench,'测试')['stage'],'liquidity')
        rows,bench=history();self.assertEqual(M.screen(rows,bench,'测试')['stage'],'oversold')
    def plan(self):return M.build_plan(*setup_history(),'测试')
    def test_wilder_rsi_handles_flat_rise_fall_and_rebound(self):
        self.assertEqual(M.rsi2([10,10,10,10]),50.)
        self.assertEqual(M.rsi2([10,11,12,13]),100.)
        self.assertEqual(M.rsi2([10,9,8,7]),0.)
        self.assertAlmostEqual(M.rsi2([10,9,8,8.2]),100/6)
    def test_oversold_reversal_has_frozen_mean_target_and_real_low_stop(self):
        p=self.plan();self.assertIsNotNone(p)
        self.assertEqual(p['entry_mode'],'oversold_reversal')
        self.assertAlmostEqual(p['sniper']['take_profit'],12.55)
        self.assertLess(p['sniper']['stop_loss'],11.9)
        self.assertLess(p['sniper']['ideal_buy'],p['sniper']['take_profit'])
    def test_healthy_momentum_panic_close_st_and_index_crash_are_rejected(self):
        self.assertIsNone(M.build_plan(*history(),'测试'))
        self.assertIsNone(M.build_plan(*setup_history(),'*ST测试'))
        rows,bench=setup_history();rows[-1]['close']=11.91
        self.assertIsNone(M.build_plan(rows,bench,'测试'))
        rows,bench=setup_history();bench[-1]['close']=95.
        self.assertIsNone(M.build_plan(rows,bench,'测试'))
    def test_intraday_requires_completed_reversal_and_does_not_use_future_bar(self):
        p=self.plan();a=M.assess(p,minutes(),clock('10:00:05'))
        self.assertTrue(a['ready'],a)
        self.assertEqual(a['target'],p['sniper']['take_profit'])
        self.assertFalse(M.assess(p,minutes(),clock('09:59:59'))['ready'])
        future=dict(minutes()[-1],time='2026-03-09 10:15',close=99,high=99)
        self.assertEqual(M.assess(p,minutes()+[future],clock('10:00:05')),a)
    def test_failed_daily_structure_exits_but_future_suffix_does_not(self):
        rows,_=setup_history();p=dict(entry_day=rows[-2]['date'],stop=11.8,plan=self.plan())
        original=deepcopy(p);before=M.manage(p,rows,clock())
        future=dict(rows[-1],date='2026-03-10',close=1.,low=1.)
        self.assertEqual(M.manage(p,rows+[future],clock()),before)
        rows[-1]['close']=11.85
        self.assertEqual(M.manage(p,rows,clock())['exit_reason'],'oversold_reversal_failed')
        self.assertEqual(p,original)


if __name__=='__main__':unittest.main()
