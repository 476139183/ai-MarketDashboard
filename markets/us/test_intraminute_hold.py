import unittest
from unittest.mock import patch
import intraminute_hold as hold

class IntraminuteHoldTests(unittest.TestCase):
    def test_first_signal_enters_next_minute_and_waits_for_15m_check(self):
        rows=[{'timestamp':i*60,'open':100+i,'high':101+i,'low':99+i,'close':100+i,'volume':10} for i in range(60)]
        def signal(history, atr):
            i=history[-1]['timestamp']//60
            if i==30: return {'signal':'做多','structure':'多头','S':90}
            if i==44: return {'signal':'观望','structure':'多头'}
            if i==59: return {'signal':'观望','structure':'震荡'}
            return {'signal':'观望','structure':'震荡'}
        with patch.object(hold,'compute_asof',side_effect=signal):
            trade=hold.replay(rows,4)[0]
        self.assertEqual(trade['signal_t'],30*60)
        self.assertEqual(trade['entry_t'],31*60)
        self.assertEqual(trade['reason'],'时段收盘')
        self.assertEqual(trade['exit_t'],60*60)

    def test_entry_and_exit_signal_execute_next_minute(self):
        rows=[{'timestamp':i*60,'open':100,'high':101,'low':99,'close':100,'volume':10} for i in range(75)]
        def signal(history,atr):
            i=history[-1]['timestamp']//60
            return {'signal':'做多' if i==30 else '观望','structure':'多头' if i<44 else '震荡','S':90}
        with patch.object(hold,'compute_asof',side_effect=signal):
            trade=hold.replay(rows,4)[0]
        self.assertEqual(trade['entry_t'],31*60)
        self.assertEqual(trade['exit_t'],45*60)
        self.assertEqual(trade['reason'],'结构失效')

if __name__=='__main__': unittest.main()
