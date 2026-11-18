from copy import deepcopy
import unittest
import verify_a_trend_trial as V


def trial():
    samples={'development':{'daily':{'600000':[],'600001':[]}},
             'time-holdout':{'daily':{'600000':[],'600001':[]}},
             'stock-holdout':{'daily':{'600002':[],'600003':[]}}}
    reports={}
    for name,count,start,end in [('development',12,'2026-08-04','2026-11-03'),('time-holdout',4,'2026-11-04','2026-11-11'),('stock-holdout',6,'2026-11-04','2026-11-11')]:
        reports[name]=dict(rule_version=V.RULE_VERSION,replay_version=V.REPLAY_VERSION,start=start,end=end,
            sample_sha256=V.fingerprint(samples[name]),results=[dict(case=case,summary=dict(return_pct=ret,max_drawdown_pct=1.,closed_trades=count))
                for case,ret in [('baseline',0.),('trend_reclaim',1.),('cost_double',.5)]])
    return reports,samples


class TrialGateTests(unittest.TestCase):
    def test_reversal_profile_requires_paired_cost_profit(self):
        reports,samples=trial()
        for r in reports.values():
            r['rule_version']='a-oversold-reversal-v3'
            r['results'][1].update(case='oversold_reversal',paired_cost_stress={'return_pct':.2})
        options=dict(rule_version='a-oversold-reversal-v3',candidate_case='oversold_reversal',max_drawdown=5,require_paired=True)
        self.assertTrue(V.evaluate(reports,samples,**options)['passed'])
        reports['development']['results'][1]['paired_cost_stress']['return_pct']=-.01
        self.assertFalse(V.evaluate(reports,samples,**options)['passed'])
    def test_requires_all_three_positive_compatible_and_sufficient_groups(self):
        reports,samples=trial();self.assertTrue(V.evaluate(reports,samples)['passed'])
    def test_losses_or_only_unrealized_gains_do_not_pass(self):
        reports,samples=trial();reports['time-holdout']['results'][1]['summary']['return_pct']=-.01
        self.assertFalse(V.evaluate(reports,samples)['passed'])
        reports,samples=trial()
        for r in reports.values():
            for case in r['results']:case['summary']['closed_trades']=0
        self.assertFalse(V.evaluate(reports,samples)['passed'])
    def test_tampered_inputs_overlapping_stocks_and_old_replay_are_rejected(self):
        reports,samples=trial();samples['stock-holdout']['daily']['600000']=[]
        self.assertFalse(V.evaluate(reports,samples)['passed'])
        reports,samples=trial();reports['time-holdout']['replay_version']='old'
        self.assertFalse(V.evaluate(reports,samples)['passed'])
        reports,samples=trial();samples['development']['daily']['600000'].append({'close':999})
        self.assertFalse(V.evaluate(reports,samples)['passed'])
    def test_nonfinite_returns_cannot_pass_and_evaluation_is_read_only(self):
        reports,samples=trial();before=deepcopy(reports);V.evaluate(reports,samples);self.assertEqual(reports,before)
        reports['development']['results'][1]['summary']['return_pct']=float('nan')
        self.assertFalse(V.evaluate(reports,samples)['passed'])


if __name__=='__main__':unittest.main()
