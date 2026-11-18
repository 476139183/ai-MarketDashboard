from copy import deepcopy
import unittest
from test_continuation import history,clock
import trend_reclaim as T


def bars():
    return [dict(time='2026-03-09 '+t,open=o,high=h,low=l,close=c,volume=v) for t,o,h,l,c,v in [
        ('09:45',13.05,13.1,13.,13.04,1000000),('10:00',13.05,13.19,13.03,13.18,1000000)]]


class TrendReclaimTests(unittest.TestCase):
    def plan(self):return T.build_plan(*history(),'测试')
    def test_daily_trend_admits_pullback_without_requiring_new_ma20_cross(self):
        p=self.plan();self.assertIsNotNone(p)
        self.assertEqual(p['entry_mode'],'trend_reclaim');self.assertEqual(p['exit_ma'],20)
    def test_pullback_then_reclaim_requires_completed_close_and_next_execution(self):
        p=self.plan();s=T.assess(p,bars(),clock('10:00:05'))
        self.assertTrue(s['ready'],s);self.assertIsNone(s['target'])
        self.assertFalse(T.assess(p,bars(),clock('09:59:59'))['ready'])
        future=dict(bars()[-1],time='2026-03-09 10:15',close=99,high=99)
        self.assertEqual(T.assess(p,bars()+[future],clock('10:00:05')),s)
    def test_chasing_dead_volume_failed_reclaim_and_crash_are_blocked(self):
        rows=bars();rows[-1]['close']=13.07
        self.assertFalse(T.assess(self.plan(),rows,clock('10:00:05'))['ready'])
        rows=bars();rows[-1]['volume']=100000
        self.assertFalse(T.assess(self.plan(),rows,clock('10:00:05'))['ready'])
        daily,bench=history();daily[-1].update(open=14.,close=14.,high=14.1,low=13.9)
        self.assertIsNone(T.build_plan(daily,bench,'测试'))
        daily,bench=history();bench[-1]['close']=90.
        self.assertIsNone(T.build_plan(daily,bench,'测试'))
    def test_structure_exit_uses_completed_daily_trend_not_intraday_noise(self):
        daily,_=history();p=dict(entry_day=daily[-2]['date'],entry_price=13.1,stop=12.7,plan=self.plan())
        before=deepcopy(p)
        self.assertIsNone(T.manage(p,daily,clock())['exit_reason'])
        daily[-1]['close']=12.
        self.assertEqual(T.manage(p,daily,clock())['exit_reason'],'trend_reclaim_ma20_exit')
        self.assertEqual(p,before)

    def test_future_daily_suffix_cannot_trigger_exit(self):
        daily,_=history();p=dict(entry_day=daily[-2]['date'],entry_price=13.1,stop=12.7,plan=self.plan())
        before=T.manage(p,daily,clock())
        future=dict(daily[-1],date='2026-03-10',close=1.,low=1.)
        self.assertEqual(T.manage(p,daily+[future],clock()),before)


if __name__=='__main__':unittest.main()
