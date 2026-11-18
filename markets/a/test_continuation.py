from copy import deepcopy
from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

import continuation as C
from test_stock_setups import rising


def history():
    rows=rising()
    for r in rows:r['volume']=10000000.
    return rows,[dict(date=r['date'],close=100.) for r in rows]


def minutes(date='2026-03-09'):
    return [dict(time=date+' '+t,open=o,high=h,low=l,close=c,volume=v) for t,o,h,l,c,v in [
        ('09:45',13.2,13.35,13.15,13.3,1000000),
        ('10:00',13.31,13.5,13.3,13.48,2000000),
        ('10:15',13.44,13.47,13.32,13.43,1000000)]]


def clock(time='10:15:05',date='2026-03-09'):
    return int(datetime.fromisoformat(date+'T'+time).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)


class ContinuationTests(unittest.TestCase):
    def plan(self):return C.build_plan(*history(),'测试')
    def test_daily_relative_strength_and_liquidity_admit_without_legacy_cross(self):
        p=self.plan();self.assertTrue(p['plan_ready'],p)
        self.assertEqual(p['entry_mode'],'continuation')
        self.assertEqual(p['signal_date'],history()[0][-1]['date'])
    def test_weak_relative_strength_st_risk_or_market_crash_do_not_admit(self):
        rows,bench=history()
        for r in rows:r.update(open=10.,close=10.,high=10.1,low=9.9)
        self.assertIsNone(C.build_plan(rows,bench,'测试'))
        self.assertIsNone(C.build_plan(*history(),'*ST测试'))
        rows,bench=history();bench[-1]['close']=90.
        self.assertIsNone(C.build_plan(rows,bench,'测试'))
        rows,bench=history()
        for r in rows:r['volume']=1000.
        self.assertIsNone(C.build_plan(rows,bench,'测试'))
    def test_first_breakout_waits_for_completed_followup_confirmation(self):
        self.assertFalse(C.assess(self.plan(),minutes()[:2],clock('10:00:05'))['ready'])
        s=C.assess(self.plan(),minutes(),clock())
        self.assertTrue(s['ready'],s)
        self.assertLess(s['stop'],s['lower']);self.assertLessEqual(s['lower'],s['upper'])
        self.assertIsNone(s['target'])
    def test_future_partial_or_missing_bars_never_produce_an_entry(self):
        rows=minutes();before=C.assess(self.plan(),rows,clock())
        future=dict(rows[-1],time='2026-03-09 10:30',close=999.,high=999.)
        self.assertEqual(C.assess(self.plan(),rows+[future],clock()),before)
        self.assertFalse(C.assess(self.plan(),rows[1:],clock())['ready'])
        self.assertFalse(C.assess(self.plan(),rows,clock('10:14:59'))['ready'])
        self.assertFalse(C.assess(self.plan(),rows,clock('10:40:00'))['ready'])
    def test_unconfirmed_volume_failed_retest_and_chasing_are_blocked(self):
        rows=minutes();rows[1]['volume']=500000
        self.assertFalse(C.assess(self.plan(),rows,clock())['ready'])

    def test_followup_same_breakout_keeps_identity_for_later_quote_execution(self):
        rows=minutes();first=C.assess(self.plan(),rows,clock())
        rows.append(dict(rows[-1],time='2026-03-09 10:30'))
        later=C.assess(self.plan(),rows,clock('10:30:05'))
        self.assertTrue(later['ready'])
        self.assertEqual(first['signal_id'],later['signal_id'])
        rows=minutes();rows[-1].update(close=13.2,low=13.1)
        self.assertFalse(C.assess(self.plan(),rows,clock())['ready'])
        rows=minutes();rows[-1].update(open=14.,close=14.1,high=14.2,low=13.9,volume=4000000)
        self.assertFalse(C.assess(self.plan(),rows,clock())['ready'])
    def test_two_closed_bars_breaking_vwap_and_original_structure_confirm_exit(self):
        p=dict(entry_price=13.43,stop=13.,plan=dict(self.plan(),breakout_level=13.35))
        rows=minutes('2026-03-10')
        rows[0].update(open=13.5,high=13.6,low=13.4,close=13.5)
        rows[1].update(open=13.45,high=13.5,low=13.25,close=13.3)
        rows[2].update(open=13.3,high=13.35,low=13.2,close=13.25)
        result=C.manage(p,rows,clock(date='2026-03-10'))
        self.assertEqual(result['exit_reason'],'continuation_structure_exit')
        before=deepcopy(p);C.manage(p,rows,clock(date='2026-03-10'));self.assertEqual(p,before)

    def test_trailing_stop_requires_post_entry_swing_with_two_closed_bars_on_each_side(self):
        p=dict(entry_price=13.43,entry_at=clock('09:30:00'),stop=13.,plan=dict(self.plan(),breakout_level=13.35))
        rows=[dict(time='2026-03-09 '+t,open=13.6,high=13.8,low=low,close=13.7,volume=1000000)
              for t,low in [('09:45',13.45),('10:00',13.4),('10:15',13.35),('10:30',13.4),('10:45',13.5)]]
        self.assertEqual(C.manage(p,rows[:4],clock('10:30:05'))['stop'],13.)
        self.assertGreater(C.manage(p,rows,clock('10:45:05'))['stop'],13.)
        p['entry_at']=clock('10:20:00')
        self.assertEqual(C.manage(p,rows,clock('10:45:05'))['stop'],13.)


if __name__=='__main__':unittest.main()
