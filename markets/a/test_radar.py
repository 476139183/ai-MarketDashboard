import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import radar
from test_stock_setups import rising


class RadarTests(unittest.TestCase):
    def test_confirmed_pullback_can_be_candidate_and_market_gate_still_applies(self):
        rows=rising();last=rows[-1]['date']
        calendar=[dict(date=r['date'],close=100+i) for i,r in enumerate(rows)]
        stock=dict(code='600000',name='测试',board='主板')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(radar,'OUT',Path(tmp)/'radar.json'), \
             mock.patch.object(radar,'_load_hot',return_value={'stocks':[stock]}), \
             mock.patch.object(radar,'prepare_symbols',return_value=([stock],[])), \
             mock.patch.object(radar,'quotes',return_value={}), \
             mock.patch.object(radar,'_hs300',return_value=calendar), \
             mock.patch.object(radar,'load',return_value=(rows,'fixture',None)), \
             mock.patch.object(radar,'exclusion_reason',return_value=None), \
             mock.patch.object(radar,'as_of_date',return_value=last):
            result=radar.scan()
            self.assertEqual(result['strategy_version'],'a-setups-v2')
            self.assertEqual(result['strategy_count'],1)
            r=result['strategy_candidates'][0]
            self.assertIn('trend_pullback',r['rules'])
            self.assertTrue(r['trade_plan']['plan_ready'])
            self.assertEqual(r['strategy_version'],'a-setups-v2')
            with mock.patch.object(radar,'market_above_ma20',return_value=set()):
                blocked=radar.scan()
            self.assertEqual(blocked['strategy_count'],0)
            self.assertEqual(blocked['confirmed_count'],1)
            self.assertIn('大盘',blocked['watch_pool'][0]['note'])

    def test_watch_note_marks_strategy_vs_defense(self):
        self.assertIn('优先观察', radar._watch_note('策略候选', ['trend'], True))
        self.assertIn('未站上MA20', radar._watch_note('观察池', ['breakout'], False))

    def test_load_cached_marks_stale_after_max_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'radar-latest.json'
            old = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(hours=20)).isoformat()
            path.write_text('{"generated_at":"%s","signal_date":"2026-11-03"}' % old, encoding='utf-8')
            with mock.patch.object(radar, 'OUT', path):
                data = radar.load_cached(max_age_hours=12)
            self.assertTrue(data['stale'])
            self.assertEqual(data['signal_date'], '2026-11-03')


if __name__ == '__main__':
    unittest.main()
