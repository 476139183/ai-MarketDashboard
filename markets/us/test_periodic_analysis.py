import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from periodic_analysis import build_report, seconds_to_next_quarter, sampled_direction, collect_many, retain_previous_directions

NY=ZoneInfo('America/New_York')

class PeriodicAnalysisTests(unittest.TestCase):
    def test_retains_previous_signal_when_current_is_unconfirmed(self):
        now=datetime(2026,10,29,6,30,tzinfo=NY)
        earlier={'generated_bj':'2026-10-29T18:15:05+08:00','items':[{'symbol':'SNDK','phase':'盘前','direction_sample':{'direction':'偏多','first_price':1372.14,'first_time_bj':'2026-10-29T18:15:00+08:00'}}]}
        current={'generated_bj':'2026-10-29T18:30:05+08:00','items':[{'symbol':'SNDK','phase':'盘前','direction_sample':None}]}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'history.jsonl').write_text(json.dumps(earlier)+'\n')
            result=retain_previous_directions(current,Path(folder))
        self.assertIsNone(result['items'][0]['direction_sample'])
        self.assertEqual(result['items'][0]['previous_direction_sample']['first_price'],1372.14)

    def test_opposite_signal_keeps_previous_direction(self):
        earlier={'generated_bj':'2026-10-29T18:15:00+08:00','items':[{'symbol':'SNDK','phase':'盘前','direction_sample':{'direction':'偏多','first_price':1372.14,'first_time_bj':'2026-10-29T18:15:00+08:00'}}]}
        current={'generated_bj':'2026-10-29T18:45:00+08:00','items':[{'symbol':'SNDK','phase':'盘前','direction_sample':{'direction':'偏空','first_price':1368.16,'first_time_bj':'2026-10-29T18:45:00+08:00'}}]}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'history.jsonl').write_text(json.dumps(earlier)+'\n')
            result=retain_previous_directions(current,Path(folder))
        self.assertEqual(result['items'][0]['previous_direction_sample']['direction'],'偏多')
        self.assertEqual(result['items'][0]['direction_sample']['direction'],'偏空')

    def test_does_not_carry_signal_to_next_day(self):
        earlier={'generated_bj':'2026-10-28T18:15:00+08:00','items':[{'symbol':'SNDK','phase':'盘前','direction_sample':{'direction':'偏多'}}]}
        current={'generated_bj':'2026-10-29T18:30:00+08:00','items':[{'symbol':'SNDK','phase':'盘前','direction_sample':None}]}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'history.jsonl').write_text(json.dumps(earlier)+'\n')
            result=retain_previous_directions(current,Path(folder))
        self.assertIsNone(result['items'][0]['previous_direction_sample'])

    def test_batch_keeps_order_and_reports_individual_failure(self):
        def fetch(symbol):
            if symbol=='MU': raise RuntimeError('failure')
            return {'symbol':symbol,'signal':'观望'}
        with patch('periodic_analysis.collect',side_effect=fetch):
            reports=collect_many(['SNDK','MU','NVDA'])
        self.assertEqual([r['symbol'] for r in reports],['SNDK','MU','NVDA'])
        self.assertEqual(reports[1]['signal'],'观望')
        self.assertIn('failure',reports[1]['errors'][0])

    def test_first_same_direction_tracks_original_bucket(self):
        now=datetime(2026,10,29,5,0,tzinfo=NY)
        start=datetime(2026,10,29,4,0,tzinfo=NY)
        points=[{'dt_ny':start+timedelta(minutes=i),'c':100+(i//15)} for i in range(60)]
        result=sampled_direction(points,now)
        self.assertEqual(result['direction'],'偏多')
        self.assertEqual(result['first_price'],102)
        self.assertEqual(result['first_time_bj'],datetime(2026,10,29,16,45,tzinfo=ZoneInfo('Asia/Shanghai')).isoformat())
        self.assertEqual(result['price'],103)

    def test_missing_quarter_does_not_create_signal(self):
        now=datetime(2026,10,29,5,0,tzinfo=NY)
        start=datetime(2026,10,29,4,0,tzinfo=NY)
        points=[{'dt_ny':start+timedelta(minutes=i),'c':100+(i//15)} for i in list(range(15))+list(range(30,60))]
        self.assertIsNone(sampled_direction(points,now))

    def test_schedule_aligns_to_next_quarter(self):
        self.assertEqual(seconds_to_next_quarter(8*3600+8*60), 7*60)
        self.assertEqual(seconds_to_next_quarter(8*3600+15*60), 15*60)

    def test_report_preserves_requested_symbol(self):
        now=datetime(2026,10,29,4,15,tzinfo=NY)
        report=build_report(now, {}, [], symbol='NVDA')
        self.assertEqual(report['symbol'], 'NVDA')

    def test_stale_quote_is_not_live(self):
        now=datetime(2026,10,29,4,15,tzinfo=NY)
        report=build_report(now, {'price':1363.5,'ts':'Oct 28, 2026 7:59 PM ET','market_status':'Pre-Market'}, [])
        self.assertEqual(report['signal'], '观望')
        self.assertFalse(report['quote_fresh'])
        self.assertIsNone(report['current_price'])

    def test_uses_only_current_session_points(self):
        now=datetime(2026,10,29,4,15,tzinfo=NY)
        points=[{'dt_ny':now-timedelta(days=1),'c':10},{'dt_ny':now-timedelta(minutes=1),'c':100}]
        report=build_report(now, {'price':100,'ts':'Oct 29, 2026 4:14 AM ET','market_status':'Pre-Market'}, points)
        self.assertEqual(report['sample_count'],1)
        self.assertEqual(report['signal'],'观望')
        self.assertTrue(report['quote_fresh'])

if __name__=='__main__':unittest.main()
