import unittest
from replay import replay


def bar(day, clock, op, high, low, close):
    return {'time': f'{day} {clock}', 'open': op, 'high': high, 'low': low, 'close': close, 'volume': 100}


class ReplayTest(unittest.TestCase):
    def test_buy_next_bar_and_sell_next_session(self):
        bars = [bar('2026-10-26', '15:00', 10, 10, 10, 10)]
        bars += [bar('2026-10-27', clock, 10, high, 10, close) for clock, high, close in
                 [('09:45', 10.4, 10.3), ('10:00', 10.6, 10.5), ('10:15', 10.7, 10.6)]]
        bars += [bar('2026-10-27', '15:00', 10.6, 10.7, 10.5, 10.6)]
        bars += [bar('2026-10-28', '09:45', 10.8, 10.9, 10.7, 10.8)]
        result = replay(bars, 0.1)
        self.assertEqual(len(result['trades']), 1)
        trade = result['trades'][0]
        self.assertEqual(trade['signal_time'], '2026-10-27 10:00')
        self.assertEqual(trade['entry_bar_end'], '2026-10-27 10:15')
        self.assertEqual(trade['exit_bar_end'], '2026-10-28 09:45')
        self.assertEqual(replay(bars, 0.1, variant='volume')['closed_trades'], 0)
        bars.append(bar('2026-10-28', '11:30', 11, 11.1, 10.9, 11))
        later = replay(bars, 0.1, exit_bar='11:30')['trades'][0]
        self.assertEqual(later['exit_bar_end'], '2026-10-28 11:30')
        self.assertEqual(later['exit_price'], 11)


if __name__ == '__main__':
    unittest.main()
