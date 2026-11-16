import unittest

from paper_account import update_account


class PaperAccountTest(unittest.TestCase):
    def test_weak_market_only_accelerates_exit_when_stock_structure_breaks(self):
        days=[f'2026-11-{i:02d}' for i in range(1,26)]
        rows=[dict(date=d,open=10+i*.1,close=10+i*.1,high=10.05+i*.1,
                   low=9.95+i*.1,volume=100) for i,d in enumerate(days)]
        meta={'000001':{'name':'趋势股','board':'深市主板'}}
        signal={'signal_date':days[20],'stocks':[{'code':'000001'}]}
        weak={d:.2 for d in days}
        held=update_account(None,{'000001':rows},meta,days,[signal],{},days[20],
                            historical_research=True,exit_policy='breadth_trend',breadth=weak)
        self.assertIn('000001',held['positions'])
        rows[23].update(close=11.7,low=11.6)
        exited=update_account(None,{'000001':rows},meta,days,[signal],{},days[20],
                              historical_research=True,exit_policy='breadth_trend',breadth=weak)
        self.assertEqual(exited['events'][-1]['side'],'sell')
        strong=update_account(None,{'000001':rows},meta,days,[signal],{},days[20],
                              historical_research=True,exit_policy='breadth_trend',breadth={d:1 for d in days})
        self.assertIn('000001',strong['positions'])

    def test_historical_complete_month_count_uses_replay_end_not_start(self):
        days = ['2026-06-01','2026-06-30','2026-07-01']
        a = update_account(None,{}, {},days,[],{},days[0],historical_research=True)
        self.assertEqual(a['completed_months'],1)
        self.assertEqual(a['completed_months_at_target'],0)

    def test_new_daily_candidates_do_not_force_existing_trending_positions_out(self):
        days = [f'2026-11-{i:02d}' for i in range(1, 26)]
        rows = [dict(date=d, open=10+i*.1, close=10+i*.1,
                     high=10.05+i*.1, low=9.95+i*.1, volume=100) for i,d in enumerate(days)]
        codes = [f'{i:06d}' for i in range(1, 7)]
        histories = {c: rows for c in codes}
        metadata = {c: {'name': '趋势股'+c, 'board': '深市主板'} for c in codes}
        signals = [dict(signal_date=days[20],stocks=[{'code': c} for c in codes[:5]]),
                   dict(signal_date=days[21],stocks=[{'code': codes[-1]}])]
        a = update_account(None,histories,metadata,days,signals,{},days[20],historical_research=True)
        self.assertEqual(set(a['positions']),set(codes[:5]))
        self.assertTrue(all(e['side']=='buy' for e in a['events']))

    def fixture(self):
        days = [f'2026-11-{i:02d}' for i in range(1, 5)]
        bars = [dict(date=d, open=10, high=11, low=9, close=10, volume=100) for d in days]
        meta = {'000001': {'name': '平安银行', 'board': '深市主板'}}
        return days, {'000001': bars}, meta

    def test_late_recorded_signal_cannot_buy_at_past_open(self):
        days, bars, meta = self.fixture()
        signal = dict(signal_date=days[0], recorded_at=days[1]+'T10:00:00+08:00', stocks=[{'code': '000001'}])
        a = update_account(None, bars, meta, days, [signal], {}, days[0])
        self.assertEqual(a['positions'], {})
        self.assertEqual(a['events'], [])

    def test_only_prospective_signal_buys_and_repeat_run_is_idempotent(self):
        days, bars, meta = self.fixture()
        signal = dict(signal_date=days[0], recorded_at=days[0]+'T18:00:00+08:00', stocks=[{'code': '000001'}])
        a = update_account(None, bars, meta, days, [signal], {}, days[0])
        self.assertEqual(a['positions']['000001']['entry_date'], days[1])
        self.assertEqual(len(a['events']), 1)
        again = update_account(a, bars, meta, days, [signal], {}, days[0])
        self.assertEqual(a, again)

    def test_new_position_waits_until_t_plus_one_and_exits_without_new_candidates(self):
        days = [f'2026-11-{i:02d}' for i in range(1, 15)]
        rows = [dict(date=d, open=10, high=11, low=9, close=10, volume=100) for d in days]
        rows[11]['close'] = 9.4
        for row in rows[12:]:
            row.update(open=9.4, high=9.5, low=9.3, close=9.4)
        signal = dict(signal_date=days[10], recorded_at=days[10]+'T18:00:00+08:00', stocks=[{'code': '000001'}])
        meta = {'000001': {'name': '平安银行', 'board': '深市主板'}}
        a = update_account(None, {'000001': rows[:12]}, meta, days[:12], [signal], {}, days[10])
        self.assertEqual([x['side'] for x in a['events']], ['buy'])
        self.assertIn('000001', a['pending_sells'])
        a = update_account(a, {'000001': rows}, {}, days, [], {}, days[10])
        self.assertEqual([x['side'] for x in a['events']], ['buy', 'sell'])
        self.assertEqual(a['events'][1]['date'], days[12])
        self.assertEqual(a['positions'], {})

    def test_historical_research_is_explicit_and_cannot_be_used_in_forward_mode(self):
        days, bars, meta = self.fixture()
        signal = {'signal_date': days[0], 'stocks': [{'code': '000001'}]}
        with self.assertRaises(ValueError):
            update_account(None, bars, meta, days, [signal], {}, days[0])
        a = update_account(None, bars, meta, days, [signal], {}, days[0], historical_research=True)
        self.assertEqual(a['mode'], 'historical_research')
        self.assertEqual(a['events'][0]['date'], days[1])
        with self.assertRaises(ValueError):
            update_account(a, bars, meta, days, [], {}, days[0])

    def test_historical_signal_is_not_changed_by_future_prices(self):
        days, bars, meta = self.fixture()
        signal = {'signal_date': days[0], 'stocks': [{'code': '000001'}]}
        first = update_account(None, {c: b[:3] for c, b in bars.items()}, meta, days[:3], [signal], {}, days[0], historical_research=True)
        second = update_account(None, bars, meta, days, [signal], {}, days[0], historical_research=True)
        self.assertEqual(first['events'], second['events'])

    def test_open_execution_does_not_read_later_intraday_high_or_low(self):
        from copy import deepcopy
        days, bars, meta = self.fixture()
        signal = {'signal_date': days[0], 'stocks': [{'code': '000001'}]}
        a = update_account(None, bars, meta, days, [signal], {}, days[0], historical_research=True)
        changed = deepcopy(bars)
        changed['000001'][1].update(high=10, low=10)
        b = update_account(None, changed, meta, days, [signal], {}, days[0], historical_research=True)
        self.assertEqual(a['events'], b['events'])


if __name__ == '__main__':
    unittest.main()
