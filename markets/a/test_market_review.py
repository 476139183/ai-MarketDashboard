import tempfile,unittest,json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import market_review as M
BJ=ZoneInfo('Asia/Shanghai')
class ReviewTests(unittest.TestCase):
    def test_whole_market_snapshot_counts_only_usable_quotes_and_keeps_zero(self):
        data=[{'f12':'600001','f2':10,'f3':6,'f6':100},{'f12':'000001','f2':10,'f3':-3,'f6':200},{'f12':'300001','f2':10,'f3':0,'f6':0},{'f12':'600002','f2':'-','f3':'-','f6':'-'}]
        r=M.summarize_stocks(data,total=4)
        self.assertTrue(r['complete']);self.assertEqual(r['breadth']['up'],1);self.assertEqual(r['breadth']['flat'],1);self.assertEqual(r['amount'],300);self.assertEqual(r['missing_quotes'],1)
    def test_partial_market_never_masquerades_as_complete_breadth_or_amount(self):
        r=M.summarize_stocks([{'f12':'600001','f2':10,'f3':5,'f6':100}],total=5000)
        self.assertFalse(r['complete']);self.assertIsNone(r['breadth']);self.assertIsNone(r['amount'])
    def test_weekend_latest_friday_is_not_stale_only_because_collection_is_old(self):
        data={'as_of':'2026-11-06','generated_at':'2026-11-06T18:00:00+08:00','indices':[],'breadth':{'up':381,'down':1936,'flat':40}}
        r=M.enrich_review(data,now=datetime(2026,11,8,12,tzinfo=BJ))
        self.assertFalse(r['stale']);self.assertTrue(r['collection_old']);self.assertEqual(r['session']['phase'],'closed');self.assertIn('非全A',r['breadth']['scope'])
    def test_empty_values_stay_unknown_and_no_division_by_zero(self):
        r=M.enrich_review({'as_of':'2026-11-06','generated_at':'2026-11-06T18:00:00+08:00','indices':[],'breadth':None},now=datetime(2026,11,8,12,tzinfo=BJ))
        self.assertIsNone(r['metrics']['advance_ratio']);self.assertEqual(r['market_tone'],'待确认');self.assertTrue(r['quality']['missing'])
    def test_style_divergence_is_described_using_index_and_breadth(self):
        data={'as_of':'2026-11-06','generated_at':'2026-11-06T18:00:00+08:00','indices':[{'code':'000300','name':'沪深300','change_pct':-1.4,'price':4000}],'breadth':{'up':3500,'down':1500,'flat':0,'scope':'全A股','source':'test'}}
        r=M.enrich_review(data,now=datetime(2026,11,8,12,tzinfo=BJ));self.assertIn('分化',r['market_tone']);self.assertEqual(r['metrics']['advance_ratio'],70)
    def test_all_invalid_quotes_do_not_become_zero_breadth(self):
        r=M.summarize_stocks([{'f12':'600001','f2':'-','f3':'-'}],total=1)
        self.assertIsNone(r['breadth']);self.assertIsNone(r['amount'])
    def test_zero_limit_counts_are_valid(self):
        with patch.object(M,'_get_json',return_value={'data':{'ztum':0,'dtum':0}}):
            r=M._limit_stats();self.assertEqual(r['limit_up'],0);self.assertEqual(r['limit_down'],0)
    def test_negative_breadth_is_not_used_for_ratio(self):
        r=M.enrich_review({'breadth':{'up':-1,'down':2,'flat':1}})
        self.assertIsNone(r['metrics']['advance_ratio'])
    def test_limit_pool_requires_matching_quote_date(self):
        with patch.object(M,'_get_json',return_value={'data':{'tc':12,'qdate':20261105}}):
            r=M._limit_stats('2026-11-06');self.assertIsNone(r['limit_up']);self.assertIsNone(r['limit_down'])
    def test_shanghai_shenzhen_turnover_is_labeled_without_double_counting_indices(self):
        r=M.enrich_review({'indices':[{'code':'000001','amount':100},{'code':'399001','amount':200},{'code':'000300','amount':80}]})
        self.assertEqual(r['metrics']['turnover'],300);self.assertIn('沪深',r['metrics']['turnover_scope'])
    def test_total_collection_failure_preserves_previous_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'review.json';output.write_text('{"previous":true}')
            with patch.object(M,'OUT',output),patch.object(M,'_indices',return_value=[]),patch.object(M,'_breadth',return_value=None),patch.object(M,'_sectors',return_value=([],[])),patch.object(M,'_stock_snapshot',return_value={'complete':False}):
                with self.assertRaisesRegex(RuntimeError,'保留'):M.build_review()
            self.assertEqual(json.loads(output.read_text()),{'previous':True})
    def test_source_date_takes_precedence_over_weekend_calendar_guess(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(M,'OUT',Path(folder)/'review.json'),patch.object(M,'_now',return_value=datetime(2026,11,8,12,tzinfo=BJ)),patch.object(M,'_indices',return_value=[{'code':'000300','name':'沪深300','price':4000,'change_pct':-1.2,'asof':'20261106150000'}]),patch.object(M,'_breadth',return_value=None),patch.object(M,'_sectors',return_value=([],[])),patch.object(M,'_limit_stats',return_value=None),patch.object(M,'_stock_snapshot',return_value={'complete':False,'breadth':None,'amount':None}):
            r=M.build_review();self.assertEqual(r['as_of'],'2026-11-06');self.assertEqual(r['report_kind'],'after_close')

if __name__=='__main__':unittest.main()
