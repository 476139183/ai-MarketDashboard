import unittest
from first_signal_quality import evaluate_path, forward_direction

class FirstSignalQualityTests(unittest.TestCase):
    def test_first_target_before_stop(self):
        rows=[{'timestamp':60,'high':103,'low':99,'close':102}, {'timestamp':120,'high':103,'low':94,'close':95}]
        result=evaluate_path(rows, {'entry':100,'side':'做多','stop':95,'entry_t':60,'exit_t':180}, 2)
        self.assertEqual(result['outcome'], 'target_first')
        self.assertEqual(result['first_target_t'],60)

    def test_same_minute_both_stop_and_target_is_conservative(self):
        rows=[{'timestamp':60,'high':103,'low':94,'close':101}]
        result=evaluate_path(rows, {'entry':100,'side':'做多','stop':95,'entry_t':60,'exit_t':120}, 2)
        self.assertEqual(result['outcome'], 'ambiguous_stop_first')
        self.assertIsNone(result['first_target_t'])

    def test_path_excludes_after_exit(self):
        rows=[{'timestamp':60,'high':101,'low':99,'close':100}, {'timestamp':120,'high':110,'low':99,'close':105}]
        result=evaluate_path(rows, {'entry':100,'side':'做多','stop':95,'entry_t':60,'exit_t':120}, 2)
        self.assertEqual(result['outcome'], 'neither')
        self.assertEqual(result['max_favorable_pct'],1)

    def test_forward_direction_uses_future_close_and_correct_short_sign(self):
        rows=[{'timestamp':60,'close':100}, {'timestamp':60+14*60,'close':95}]
        t={'entry':100,'entry_t':60,'side':'做空'}
        result=forward_direction(rows,t,(15,30))
        self.assertEqual(result['15'],5)
        self.assertIsNone(result['30'])

    def test_missing_forward_bar_is_not_filled_by_later_price(self):
        rows=[{'timestamp':60+16*60,'close':105}]
        self.assertIsNone(forward_direction(rows,{'entry':100,'entry_t':60,'side':'做多'},(15,))['15'])

if __name__=='__main__':unittest.main()
