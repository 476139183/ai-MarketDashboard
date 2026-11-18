import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import radar
from test_stock_setups import rising


class RadarTests(unittest.TestCase):
    def test_index_falls_back_to_sina_and_truncates_future_dates(self):
        from io import BytesIO
        import json
        rows=[dict(day=(datetime(2026,10,1)+timedelta(days=i)).date().isoformat(),close='4000') for i in range(43)]
        response=BytesIO(('var x=('+json.dumps(rows)+');').encode())
        with mock.patch.object(radar,'_hs300_tencent',side_effect=ValueError('501')),mock.patch.object(radar,'urlopen',return_value=response):
            result=radar._hs300('2026-11-11')
        self.assertEqual(result[-1]['date'],'2026-11-11');self.assertTrue(all(r['date']<='2026-11-11' for r in result))
    def test_reversal_can_be_prepared_independently_without_ma20_cross(self):
        from test_oversold_reversal import setup_history
        rows,calendar=setup_history();stock=dict(code='600000',name='测试',board='主板')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(radar,'OUT',Path(tmp)/'radar.json'), \
             mock.patch.object(radar,'_load_hot',return_value={'stocks':[stock]}), \
             mock.patch.object(radar,'prepare_symbols',return_value=([stock],[])), \
             mock.patch.object(radar,'quotes',return_value={'600000':dict(name='测试',float_cap_yi=50)}), \
             mock.patch.object(radar,'_hs300',return_value=calendar), \
             mock.patch.object(radar,'load',return_value=(rows,'fixture',None)), \
             mock.patch.object(radar,'as_of_date',return_value=rows[-1]['date']):
            result=radar.scan(enable_reversal=True)
        self.assertFalse(result['market_pass'])
        self.assertEqual(result['reversal_count'],1)
        self.assertTrue(result['oversold_reversal_market_pass'])
        self.assertEqual(result['strategy_candidates'][0]['trade_plan']['entry_mode'],'oversold_reversal')

    def test_defensive_index_allows_only_independent_strong_continuation_candidate(self):
        from test_continuation import history
        rows,calendar=history();stock=dict(code='600000',name='测试',board='主板')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(radar,'OUT',Path(tmp)/'radar.json'), \
             mock.patch.object(radar,'_load_hot',return_value={'stocks':[stock]}), \
             mock.patch.object(radar,'prepare_symbols',return_value=([stock],[])), \
             mock.patch.object(radar,'quotes',return_value={'600000':dict(name='测试',float_cap_yi=50)}), \
             mock.patch.object(radar,'_hs300',return_value=calendar), \
             mock.patch.object(radar,'load',return_value=(rows,'fixture',None)), \
             mock.patch.object(radar,'as_of_date',return_value=rows[-1]['date']):
            blocked=radar.scan(enable_continuation=False)
            self.assertFalse(blocked['continuation_enabled'])
            self.assertEqual(blocked['strategy_candidates'],[])
            result=radar.scan(enable_continuation=True)
        self.assertFalse(result['market_pass'])
        self.assertTrue(result['continuation_market_pass'])
        self.assertEqual(result['strategy_candidates'][0]['trade_plan']['entry_mode'],'continuation')
        self.assertIn('等待盘中',result['strategy_candidates'][0]['note'])

    def test_stale_primary_uses_complete_fresh_history(self):
        rows = rising()
        with mock.patch.object(radar, 'load', return_value=(rows[:-1], 'stale', [])), \
             mock.patch.object(radar, '_latest_daily', return_value=(rows, 'fresh'), create=True):
            bars, source = radar._current_history(dict(code='600000', board='主板'), rows[-1]['date'])
        self.assertEqual(bars, rows)
        self.assertEqual(source, 'fresh')

    def test_stale_both_sources_is_not_a_valid_empty_scan(self):
        rows = rising()
        with mock.patch.object(radar, 'load', return_value=(rows[:-1], 'stale', [])), \
             mock.patch.object(radar, '_latest_daily', return_value=(rows[:-1], 'also stale'), create=True):
            with self.assertRaisesRegex(ValueError, '信号日日线未落地'):
                radar._current_history(dict(code='600000', board='主板'), rows[-1]['date'])

    def test_latest_daily_uses_unadjusted_prices_and_share_volume(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"data":{"sh600000":{"day":[["2026-11-12","10","11","12","9","123"]]}}}'
        with mock.patch.object(radar, 'urlopen', return_value=response) as request:
            bars, source = radar._latest_daily('600000', '主板')
        self.assertEqual(bars[0]['volume'], 12300)
        self.assertEqual(bars[0]['close'], 11)
        self.assertIn('未复权', source)
        self.assertNotIn('qfq', request.call_args.args[0].full_url)

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
            with mock.patch.object(radar, 'load', return_value=(rows[:-1], 'stale', [])), \
                 mock.patch.object(radar, '_latest_daily', return_value=(rows[:-1], 'stale')):
                unavailable = radar.scan()
            self.assertFalse(unavailable['data_ready'])
            self.assertEqual(unavailable['daily_unavailable_count'], 1)
            self.assertEqual(sum(unavailable['exclusion_summary'].values()), 1)

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
