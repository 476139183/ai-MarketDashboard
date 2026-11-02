import unittest
from premarket_breakout import replay_day

class PremarketBreakoutTests(unittest.TestCase):
    def test_close_break_enters_next_minute_and_exits_on_structure_check(self):
        rows=[]
        for i in range(20):
            rows.append({'timestamp':i*60,'open':100,'high':101,'low':99,'close':100,'volume':10})
        rows[2].update(close=98,low=97)
        rows[3].update(open=98,high=99,low=97,close=98)
        for i in range(4, 20): rows[i]['high']=100.5
        rows[14].update(close=100.5,high=100.8)
        t=replay_day(rows,pre_low=99,pre_high=110)
        self.assertEqual(t['side'],'做空')
        self.assertEqual(t['entry_t'],3*60)
        self.assertEqual(t['exit_t'],15*60)
        self.assertEqual(t['reason'],'结构失效')

    def test_entry_requires_next_available_minute(self):
        rows=[{'timestamp':i*60,'open':100,'high':101,'low':99,'close':100,'volume':10} for i in range(20)]
        rows[2].update(close=98,low=97)
        del rows[3]
        self.assertIsNone(replay_day(rows,pre_low=99,pre_high=110))

    def test_no_trade_before_break(self):
        rows=[{'timestamp':i*60,'open':100,'high':101,'low':99,'close':100,'volume':10} for i in range(20)]
        self.assertIsNone(replay_day(rows,pre_low=90,pre_high=110))

if __name__=='__main__':unittest.main()
