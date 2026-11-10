import importlib.util
import unittest
import gzip,json
from pathlib import Path
from test_a_paper_research import sample
from quant_research import normalize


@unittest.skipUnless(importlib.util.find_spec('rqalpha'),'使用.venv-quant运行框架策略测试')
class WorkerTests(unittest.TestCase):
    def test_archived_five_trades_reconcile_with_independent_framework(self):
        import quant_worker as W
        path=W.ROOT/'.cache/a-paper-research/sample-latest.json.gz'
        if not path.is_file():self.skipTest('未缓存历史研究样本')
        original=json.loads((W.ROOT/'docs/reports/a-share-plan-2026-11-09/summary.json').read_text())
        out=W.run_stock(normalize(dict(market='a_share',strategy='expanded')))
        self.assertEqual(out['data']['sample_sha256'],original['sample_sha256'])
        self.assertEqual(out['summary']['closed_trades'],5)
        self.assertAlmostEqual(out['summary']['end_equity'],original['results'][-1]['summary']['end_equity'],places=2)
        self.assertAlmostEqual(out['summary']['fees'],original['results'][-1]['summary']['fees'],places=2)
    def test_isolated_single_touch_does_not_replace_consecutive_plan_quotes(self):
        import quant_worker as W
        s=sample();date=s['start']
        for i,b in enumerate(s['minutes']['600000']):
            if b['time'][:10]==date:
                px=100.1 if i%2==0 else 102.
                b.update(open=px,close=px,high=px+.1,low=px-.1)
        out=W.run_stock(normalize(dict(market='a_share',strategy='legacy')),sample_override=s)
        self.assertEqual(out['fills'],[])


class RiskBudgetTests(unittest.TestCase):
    def test_minimum_lot_is_rejected_instead_of_exceeding_risk_or_margin_budget(self):
        from quant_worker import risk_lots
        cfg=dict(multiplier=10,margin_rate=.15,max_margin_fraction=.5)
        self.assertEqual(risk_lots(100000,100,1000,cfg),0)
        lots=risk_lots(100000,10,1000,cfg)
        self.assertGreater(lots,0)
        self.assertLessEqual(lots*10*cfg['multiplier'],100000*.005)
        self.assertEqual(risk_lots(100000,1,100000,cfg),0)


if __name__=='__main__':unittest.main()
