import unittest
from copy import deepcopy
from pathlib import Path
import json,tempfile
from unittest.mock import patch
from datetime import date,timedelta
import strength_radar as S

def bars(kind='trend'):
    start=date(2026,6,1);rows=[];p=10
    for i in range(90):
        change=.002 if i<85 else .012
        previous=p;p*=1+change
        rows.append(dict(date=(start+timedelta(days=i)).isoformat(),open=previous,high=p*1.005,low=previous*.995,close=p,volume=8e6))
    return rows
class StrengthTests(unittest.TestCase):
    def test_continuous_strength_does_not_need_fresh_twenty_day_breakout(self):
        rows=bars();rows[-10]['high']=rows[-1]['close']*1.1
        r=S.evaluate({'code':'300324','name':'样例','board':'创业板'},rows,rows[-1]['date'])
        self.assertEqual(r['stage'],'matched');self.assertIn('steady_rise',r['patterns']);self.assertFalse(r['execution_enabled']);self.assertGreater(r['metrics']['up_days5'],2)
    def test_results_do_not_depend_on_stock_code_or_future_rows(self):
        rows=bars();day=rows[-1]['date'];future=dict(rows[-1],date='2027-01-01',close=1000,high=1001)
        a=S.evaluate({'code':'300324','name':'样例','board':'创业板'},rows,day)
        b=S.evaluate({'code':'002036','name':'样例','board':'主板'},rows+[future],day)
        self.assertEqual(a['metrics'],b['metrics']);self.assertEqual(a['patterns'],b['patterns'])
    def test_risk_names_missing_data_suspension_and_duplicates_are_not_opportunities(self):
        rows=bars();stock={'code':'600001','name':'正常','board':'主板'}
        self.assertEqual(S.evaluate(dict(stock,name='ST样例'),rows,rows[-1]['date'])['stage'],'security')
        self.assertEqual(S.evaluate(stock,rows, '2027-01-01')['stage'],'data')
        r=deepcopy(rows);r[-1]['volume']=0;self.assertEqual(S.evaluate(stock,r,r[-1]['date'])['stage'],'suspended')
        self.assertEqual(S.evaluate(stock,rows+[rows[-1]],rows[-1]['date'])['stage'],'data')
    def test_extended_price_is_not_promoted_as_new_opportunity(self):
        rows=bars()
        for row in rows[-5:]:
            for key in ('open','high','low','close'):row[key]*=1.3
        r=S.evaluate({'code':'600001','name':'正常','board':'主板'},rows,rows[-1]['date'])
        self.assertEqual(r['stage'],'extended')
    def test_base_recovery_can_be_seen_before_ma20_turns_up_but_is_labeled_risky(self):
        rows=bars()
        for i,row in enumerate(rows):
            c=15-i*.06 if i<85 else 9.9+(i-85)*.1
            row.update(open=c-.03,high=c+.05,low=c-.08,close=c,volume=8e6)
        result=S.evaluate({'code':'600002','name':'底部样例','board':'主板'},rows,rows[-1]['date'])
        self.assertIn('base_recovery',result['patterns']);self.assertLess(result['metrics']['ma20_slope'],0);self.assertIn('修复',result['risk_note'])
    def test_recent_signal_is_tracked_separately_when_today_no_longer_matches(self):
        rows=bars();rows[-1]['volume']=1000
        report=S.screen([{'code':'600003','name':'样例','board':'主板'}],{'600003':rows},rows[-1]['date'])
        self.assertEqual(len(report['items']),0);self.assertEqual(len(report['tracking_items']),1)
        self.assertEqual(report['tracking_items'][0]['patterns'],[]);self.assertIn('first_signal_date',report['tracking_items'][0])
    def test_collection_failure_retains_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'latest.json';path.write_text('{"items": [{"code": "600001"}]}')
            service=S.StrengthService()
            with patch.object(S,'LATEST',path),patch.object(S,'collect',side_effect=ValueError('日线源未就绪')):
                service._job();result=service.snapshot()
            self.assertEqual(result['items'][0]['code'],'600001');self.assertIn('未就绪',result['error']);self.assertEqual(result['status'],'idle')
    def test_limit_locked_bar_does_not_erase_prior_signal_from_tracking(self):
        rows=bars();price=rows[-2]['close']*1.1
        rows[-1].update(open=price,high=price,low=price,close=price)
        stock={'code':'002685','name':'样例','board':'主板'}
        result=S.screen([stock],{stock['code']:rows},rows[-1]['date'])
        self.assertEqual(result['items'],[]);self.assertEqual(len(result['tracking_items']),1)
        self.assertEqual(result['tracking_items'][0]['stage'],'locked');self.assertEqual(result['tracking_items'][0]['close'],price)
        self.assertFalse(result['tracking_items'][0]['execution_enabled'])
    def test_partial_coverage_is_explicit_and_returns_all_matches_not_top_five(self):
        rows=bars();stocks=[dict(code=f'{i:06}',name='样例',board='主板') for i in range(1,13)]
        report=S.screen(stocks,{s['code']:rows for s in stocks[:10]},rows[-1]['date'])
        self.assertEqual(report['coverage']['ready'],10);self.assertEqual(report['coverage']['total'],12);self.assertEqual(len(report['items']),10)
        self.assertFalse(report['coverage']['complete']);self.assertEqual(report['stages']['data'],2)
if __name__=='__main__':unittest.main()
