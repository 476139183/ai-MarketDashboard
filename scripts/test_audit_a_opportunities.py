import unittest
from audit_a_opportunities import opening_range_opportunity


class OpeningRangeAuditTests(unittest.TestCase):
    def rows(self):
        return [dict(time='2026-11-12 '+t, open=o, high=h, low=l, close=c, volume=1000)
                for t,o,h,l,c in [('09:45',10,10.1,9.9,10),
                                  ('10:00',10,10.3,10,10.25),
                                  ('10:15',10.26,10.4,10.2,10.35)]]

    def test_requires_closed_breakout_and_a_later_bar_sample(self):
        rows=self.rows()
        self.assertIsNone(opening_range_opportunity(rows[:2],10,'600000'))
        result=opening_range_opportunity(rows,10,'600000')
        self.assertEqual(result['signal_time'],'2026-11-12 10:00')
        self.assertEqual(result['sample_price'],10.27)
        rows[-1]['close']=9.5
        self.assertEqual(opening_range_opportunity(rows,10,'600000'),result)

    def test_gap_in_minutes_and_limit_price_are_not_counted_as_opportunities(self):
        rows=self.rows();rows[1]['time']='2026-11-12 10:15';rows[2]['time']='2026-11-12 10:30'
        self.assertIsNone(opening_range_opportunity(rows,10,'600000'))
        rows=self.rows();rows[-1]['open']=11
        self.assertIsNone(opening_range_opportunity(rows,10,'600000'))
