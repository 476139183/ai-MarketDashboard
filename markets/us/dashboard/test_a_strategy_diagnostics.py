from copy import deepcopy
import unittest
import a_strategy_diagnostics as D
from test_a_paper_research import sample


def position():
    return dict(entry_day='2026-09-01',stop=90.,target=120.,costs={'exit_ma':10})


def history(two_days=False):
    rows=[dict(date=f'2026-08-{i:02d}',close=100.) for i in range(10,30)]
    rows.append(dict(date='2026-09-01',close=99.))
    if two_days:rows.append(dict(date='2026-09-02',close=98.))
    return rows


class DiagnosticTests(unittest.TestCase):
    def test_t_plus_one_is_not_itself_a_sell_signal(self):
        p=position();rows=history();rows[-1]['close']=101
        for policy in D.POLICIES:
            self.assertIsNone(D.exit_reason(policy,p,{'price':105},rows))
    def test_confirmation_waits_for_two_post_entry_closes(self):
        p=position()
        self.assertEqual(D.exit_reason('ma10',p,{'price':99},history()),'ma10_exit')
        self.assertIsNone(D.exit_reason('confirm2',p,{'price':99},history()))
        self.assertEqual(D.exit_reason('confirm2',p,{'price':98},history(True)),'ma10_confirm2')
    def test_stop_and_target_are_not_delayed_by_soft_exit_confirmation(self):
        for policy in D.POLICIES:
            self.assertEqual(D.exit_reason(policy,position(),{'price':89},history()),'stop')
            self.assertEqual(D.exit_reason(policy,position(),{'price':121},history()),'target')
    def test_baseline_factory_replays_identical_to_current_engine(self):
        s=sample();a=D.R.replay(s,D.P.DEFAULTS)
        b=D.R.replay(s,D.P.DEFAULTS,account_factory=lambda path:D.PolicyAccount(path,'ma10'))
        self.assertEqual(a['trades'],b['trades']);self.assertEqual(a['summary'],b['summary'])
    def test_horizon_returns_are_candidate_outcomes_not_trades(self):
        s=sample();s['start']=s['daily']['600000'][30]['date'];out=D.event_study(s,'trend')
        self.assertGreater(len(out['events']),0)
        e=out['events'][0];self.assertIn('forward',e);self.assertIn('1',e['forward'])
        self.assertNotIn('entry_price',e)
    def test_prototype_rule_does_not_read_future_daily_data(self):
        from datetime import datetime,timedelta
        dates=[];cursor=datetime(2026,1,1)
        while len(dates)<75:
            if cursor.weekday()<5:dates.append(cursor.date().isoformat())
            cursor+=timedelta(days=1)
        rows=[dict(date=d,open=100+i*.2,high=100.1+i*.2,low=99.9+i*.2,close=100+i*.2,volume=10000) for i,d in enumerate(dates)]
        rows[-2]['low']=rows[-2]['close']-2
        data=dict(daily={'600000':rows},names={'600000':'test'},benchmark=[dict(date=d,close=2000+i) for i,d in enumerate(dates)])
        before=D.prototype_radar(data,dates[-2],'trend_pullback')
        self.assertTrue(before['strategy_candidates'])
        changed=deepcopy(data);changed['daily']['600000'][-1]['close']=9999
        self.assertEqual(before,D.prototype_radar(changed,dates[-2],'trend_pullback'))

    def test_fixed_entries_preserved_and_future_suffix_cannot_rewrite_earlier_exit(self):
        s=sample();base=D.R.replay(s,D.P.DEFAULTS)['trades'];self.assertTrue(base)
        t=base[0];a=D.fixed_entry(t,s,'ma10')
        later=deepcopy(s);later['daily']['600000'][-1]['close']=9999
        b=D.fixed_entry(t,later,'ma10')
        self.assertEqual(a['entry_at'],t['entry_at']);self.assertEqual(a['entry_price'],t['entry_price'])
        self.assertEqual(a['exit_at'],b['exit_at']);self.assertEqual(a['net_pnl'],b['net_pnl'])

if __name__=='__main__':unittest.main()
