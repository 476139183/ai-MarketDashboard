import unittest
from copy import deepcopy
import screen_a_candidates as S
from test_oversold_reversal import setup_history


class CandidateScreenTests(unittest.TestCase):
    def test_universe_excludes_indices_funds_and_wrong_exchanges_and_requires_date(self):
        payload=dict(as_of='2026-11-11',stocks=[dict(code=c,code_name='测试',tradeStatus='1') for c in ('sh.000001','sh.510300','sz.600000','sh.600000','sz.300001')])
        self.assertEqual(set(S.universe_rows(payload,'2026-11-11')),{'600000','300001'})
        with self.assertRaises(ValueError):S.universe_rows(payload,'2026-11-12')
    def test_future_daily_suffix_does_not_change_prior_day_selection(self):
        rows,bench=setup_history();stock=dict(trading=True,name='测试');day=rows[-1]['date']
        before=S.evaluate_stock(stock,rows,bench,day)
        later=deepcopy(rows[-1]);later.update(date='2026-12-01',close=999)
        self.assertEqual(S.evaluate_stock(stock,rows+[later],bench,day),before)
        self.assertEqual(S.evaluate_stock(stock,rows+[later],bench+[dict(date='2026-12-01',close=1)],day),before)
        self.assertEqual(before['stage'],'candidate')
        self.assertEqual(S.evaluate_stock(stock,rows[:-1],bench,day)['stage'],'data')


if __name__=='__main__':unittest.main()
