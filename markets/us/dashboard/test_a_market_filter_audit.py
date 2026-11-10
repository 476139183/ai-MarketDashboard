import gzip
import json
from pathlib import Path
import unittest
import a_paper_research as R
import a_market_filter_audit as A


class MarketFilterAuditTests(unittest.TestCase):
    def test_reusing_other_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'指纹'):
            A.run({},dict(sample_sha256='other'))

    def test_strict_reproduces_existing_candidates_and_future_does_not_change_them(self):
        path=Path(__file__).resolve().parents[3]/'.cache/a-paper-research/sample-latest.json.gz'
        if not path.exists():self.skipTest('本地研究样本未缓存')
        with gzip.open(path,'rt') as f:s=json.load(f)
        for rule in ('trend','expanded'):
            for d in ('2026-08-17','2026-09-01'):
                self.assertEqual(A.radar(s,d,rule,'strict'),R.reconstructed_radar(s,d,rule))
                past=dict(s,benchmark=[r for r in s['benchmark'] if r['date']<=d],daily={c:[r for r in rows if r['date']<=d] for c,rows in s['daily'].items()})
                for mode in A.MODES:self.assertEqual(A.radar(s,d,rule,mode),A.radar(past,d,rule,mode))

    def test_weak_market_can_pass_relaxed_but_not_strict(self):
        bench=[dict(date=str(i),close=100.) for i in range(20)]
        bench[-1]['close']=99.
        self.assertFalse(A.market_gate(bench,'strict'))
        self.assertTrue(A.market_gate(bench,'near_ma20'))
        self.assertTrue(A.market_gate(bench,'off'))
        bench[-1]['close']=90.
        self.assertFalse(A.market_gate(bench,'near_ma20'))


if __name__=='__main__':unittest.main()
