import unittest

from simulation import rank_stocks


def history(close=12):
    rows = [dict(date=f'2026-10-{i:02d}', open=10, high=11, low=9,
                 close=10, volume=100) for i in range(1, 22)]
    rows[-1].update(close=close, high=max(11, close), open=10)
    return rows


class SimulationSelectionTest(unittest.TestCase):
    def test_no_breakout_is_not_displayed_as_an_observation(self):
        bars = history(10.5)
        rows, excluded = rank_stocks({'000001': bars}, {'000001': '平安银行'},
                                    [b['date'] for b in bars])
        self.assertEqual(rows, [])
        self.assertEqual(excluded, {})

    def test_breakout_precedes_observation_without_index_filter(self):
        bars = history()
        rows, _ = rank_stocks({'000001': bars, '000002': history(10.5)},
                              {'000001': '平安银行', '000002': '万科A'},
                              [b['date'] for b in bars])
        self.assertEqual(rows[0]['code'], '000001')
        self.assertEqual(rows[0]['status'], '突破信号')
        self.assertEqual(len(rows), 1)

    def test_stale_and_risk_warning_stocks_cannot_fill_ranking(self):
        bars = history()
        rows, excluded = rank_stocks({'000001': bars[:-1], '000002': bars},
                                    {'000001': '平安银行', '000002': '*ST示例'},
                                    [b['date'] for b in bars])
        self.assertEqual(rows, [])
        self.assertEqual(len(excluded), 2)


if __name__ == '__main__':
    unittest.main()
