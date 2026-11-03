import copy
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def candle(when, close, volume=1000, spread=.7):
    return dict(t=int(when.timestamp()), o=close-.08, h=close+spread,
                l=close-spread, c=close, v=volume)


def scenario(market='us', direction='up', prices=None, last_volume=2200):
    zone = ZoneInfo('America/New_York' if market == 'us' else 'Asia/Shanghai')
    now = datetime(2026, 10, 30, 11, 35 if market == 'us' else 20, tzinfo=zone)
    days, cursor = [], now.date()-timedelta(days=1)
    while len(days) < 70:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    daily = [candle(datetime.combine(day, datetime.min.time(), zone).replace(hour=9, minute=30),
                     80 + i*.5 if direction == 'up' else 150-i*.5) for i, day in enumerate(days)]
    history = []
    for day in days[-4:]:
        start = datetime.combine(day, datetime.min.time(), zone).replace(hour=9, minute=30)
        history += [candle(start+timedelta(minutes=15*i), 110+i*.1, spread=.15) for i in range(8)]
    prices = prices or [115, 115.1, 115.2, 115.1, 115.15, 115.3, 115.5, 115.8]
    start = now.replace(hour=9, minute=30)
    today = [candle(start+timedelta(minutes=15*i), close, spread=.15) for i, close in enumerate(prices)]
    today[-1]['v'] = last_volume
    return daily, history+today, now


class MarketStateTests(unittest.TestCase):
    def module(self):
        import market_states
        return market_states

    def test_daily_direction_and_volume_are_separate_facts(self):
        daily, bars, now = scenario()
        result = self.module().build_market_context(daily, bars, now)
        self.assertEqual(result['daily_context']['bias'], 'up')
        self.assertTrue(result['daily_context']['volume_ready'])
        self.assertEqual(result['daily_context']['last_volume_ratio'], 1)
        self.assertEqual(result['intraday_state']['code'], 'acceleration')
        self.assertAlmostEqual(result['intraday_state']['bar_rvol'], 2.2)

    def test_down_daily_rally_is_rebound_not_a_new_long(self):
        daily, bars, now = scenario(direction='down')
        context = self.module().build_market_context(daily, bars, now)
        result = self.module().entry_signal(context, px=115.7, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(context['intraday_state']['code'], 'rebound')
        self.assertEqual(context['daily_context']['bias'], 'down')
        self.assertEqual(result['advice'], '观望')
        self.assertFalse(result['actionable'])

    def test_confirmed_intraday_reversal_does_not_rewrite_daily_direction(self):
        daily, bars, now = scenario(prices=[115, 115.5, 116, 116.1, 115.8, 115.3, 114.9, 114.5])
        context = self.module().build_market_context(daily, bars, now)
        result = self.module().entry_signal(context, px=114.4, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(context['intraday_state']['code'], 'reversal')
        self.assertEqual(context['intraday_state']['direction'], 'down')
        self.assertEqual(context['daily_context']['bias'], 'up')
        self.assertTrue(result['exit_long'])
        self.assertFalse(result['actionable'])

    def test_missing_historical_slot_volume_does_not_fake_confirmation(self):
        daily, bars, now = scenario()
        bars = [bar for bar in bars if datetime.fromtimestamp(bar['t'], now.tzinfo).date() == now.date()]
        context = self.module().build_market_context(daily, bars, now)
        self.assertIsNone(context['intraday_state']['bar_rvol'])
        self.assertNotEqual(context['intraday_state']['code'], 'acceleration')
        result = self.module().entry_signal(context, px=115.62, quote_fresh=True, session_open=True, now=now)
        self.assertFalse(result['actionable'])
        self.assertIn('量', result['reason'])

    def test_thin_volume_cannot_trigger_acceleration_entry(self):
        daily, bars, now = scenario(last_volume=400)
        context = self.module().build_market_context(daily, bars, now)
        self.assertNotEqual(context['intraday_state']['code'], 'acceleration')
        result = self.module().entry_signal(context, px=115.62, quote_fresh=True, session_open=True, now=now)
        self.assertFalse(result['actionable'])

    def test_future_bars_and_unfinished_daily_candle_cannot_change_result(self):
        daily, bars, now = scenario()
        before = self.module().build_market_context(daily, bars, now)
        later = candle(now.replace(hour=15), 9999, volume=1e12)
        unfinished = candle(now.replace(hour=11, minute=30), .01, volume=1e12)
        after = self.module().build_market_context(daily+[later], bars+[later, unfinished], now)
        self.assertEqual(before, after)

    def test_missing_volume_or_duplicate_current_bar_blocks_entry(self):
        daily, bars, now = scenario()
        for changed in [bars+[copy.deepcopy(bars[-1])], bars[:-1]+[{**bars[-1], 'v':0}]]:
            context = self.module().build_market_context(daily, changed, now)
            result = self.module().entry_signal(context, px=115.62, quote_fresh=True, session_open=True, now=now)
            self.assertFalse(result['actionable'])
            self.assertFalse(context['intraday_state']['data_ready'])

    def test_volume_confirmed_breakout_waits_for_price_and_expires(self):
        daily, bars, now = scenario()
        context = self.module().build_market_context(daily, bars, now)
        result = self.module().entry_signal(context, px=115.67, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(result['advice'], '做多')
        self.assertTrue(result['actionable'])
        self.assertLess(result['stop'], result['entry_low'])
        self.assertGreater(result['target'], result['entry_high'])
        self.assertGreaterEqual(result['worst_R1'], 1.5)
        chasing = self.module().entry_signal(context, px=118, quote_fresh=True, session_open=True, now=now)
        self.assertFalse(chasing['actionable'])
        expired = self.module().entry_signal(context, px=115.67, quote_fresh=True, session_open=True, now=now+timedelta(minutes=16))
        self.assertFalse(expired['actionable'])

    def test_near_resistance_cannot_be_moved_to_manufacture_rr(self):
        daily, bars, now = scenario()
        context = self.module().build_market_context(daily, bars, now)
        context['daily_context']['resistance'] = 115.95
        result = self.module().entry_signal(context, px=115.67, quote_fresh=True, session_open=True, now=now)
        self.assertFalse(result['actionable'])
        self.assertIn('盈亏比', result['reason'])

    def test_stale_quote_and_closed_session_do_not_issue_entry(self):
        daily, bars, now = scenario()
        context = self.module().build_market_context(daily, bars, now)
        for fresh, opened in [(False,True),(True,False)]:
            result = self.module().entry_signal(context, px=115.67, quote_fresh=fresh, session_open=opened, now=now)
            self.assertFalse(result['actionable'])

    def test_a_share_lunch_has_no_false_missing_bucket(self):
        daily, bars, now = scenario(market='cn')
        now = now.replace(hour=13, minute=20)
        start = now.replace(hour=13, minute=0)
        bars += [candle(start, 116, volume=2200, spread=.15)]
        context = self.module().build_market_context(daily, bars, now, market='cn')
        self.assertTrue(context['intraday_state']['data_ready'])
        result = self.module().entry_signal(context, px=116, quote_fresh=True, session_open=False, now=now.replace(hour=12))
        self.assertFalse(result['actionable'])

    def test_a_share_never_short_and_needs_overnight_and_true_limits(self):
        daily, bars, now = scenario(market='cn', direction='down', prices=[115,114.9,114.8,114.9,114.85,114.7,114.5,114.2])
        now = now.replace(hour=11, minute=35)
        context = self.module().build_market_context(daily, bars, now, market='cn')
        result = self.module().entry_signal(context, px=114.33, quote_fresh=True, session_open=True, now=now,
                                          accept_overnight=True, limits=(120,100))
        self.assertNotEqual(result['advice'], '做空')
        daily, bars, now = scenario(market='cn')
        now = now.replace(hour=11, minute=35)
        context = self.module().build_market_context(daily, bars, now, market='cn')
        for ack,limits in [(False,(120,100)),(True,None),(True,(115.8,100))]:
            result = self.module().entry_signal(context, px=115.67, quote_fresh=True, session_open=True, now=now,
                                              accept_overnight=ack, limits=limits)
            self.assertFalse(result['actionable'])

    def test_a_share_long_can_only_trigger_with_overnight_ack_and_true_limits(self):
        daily,bars,now=scenario(market='cn',prices=[115,115.1,115.2,115.1,115.5])
        now=now.replace(hour=10,minute=50)
        context=self.module().build_market_context(daily,bars,now,market='cn')
        result=self.module().entry_signal(context,px=115.38,quote_fresh=True,session_open=True,now=now,
                                          accept_overnight=True,limits=(120,100))
        self.assertEqual(result['advice'],'做多')
        self.assertTrue(result['actionable'])
        self.assertIn('T+1',result['execution_note'])

    def test_waiting_plan_risk_ratio_uses_division_and_fixed_entry_band(self):
        daily,bars,now=scenario()
        context=self.module().build_market_context(daily,bars,now)
        result=self.module().entry_signal(context,px=118,quote_fresh=True,session_open=True,now=now)
        expected=(result['target']-result['entry_mid'])/(result['entry_mid']-result['stop'])
        self.assertAlmostEqual(result['R1'],expected,places=2)
        moved=self.module().entry_signal(context,px=117,quote_fresh=True,session_open=True,now=now)
        self.assertEqual(result['E_band'],moved['E_band'])

    def test_open_drive_allows_early_short_on_down_day(self):
        # 开盘大阴后继续在 VWAP 下：应识别开盘弱势并可做空（不必再摸回开盘高）
        daily, bars, now = scenario(direction='down', prices=[148.6, 147.2, 145.5, 144.8, 143.5, 142.8], last_volume=2500)
        start = now.replace(hour=9, minute=30)
        today = [b for b in bars if datetime.fromtimestamp(b['t'], now.tzinfo).date() == now.date()]
        for i, px in enumerate([148.6, 147.2, 145.5, 144.8, 143.5, 142.8]):
            today[i].update(c=px, o=px + .2, h=px + .4, l=px - .3, v=2200)
        today[0].update(o=151, h=152, l=148.5, c=148.6, v=5000)
        today[-1]['v'] = 2500
        now = start + timedelta(minutes=15 * len(today))
        context = self.module().build_market_context(daily, bars, now)
        self.assertEqual(context['intraday_state']['code'], 'open_drive')
        result = self.module().entry_signal(context, px=142.8, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(result['advice'], '做空')
        self.assertTrue(result['actionable'])
        self.assertGreater(result['stop'], result['ref_price'])

    def test_rebound_fail_allows_short_after_morning_bounce(self):
        # 先砸后抽再失守：早盘反抽失败可做空
        path = [
            (151, 152, 145, 146),
            (146, 146.5, 143, 143.5),
            (143.5, 144, 142, 142.2),
            (142.5, 147, 142.4, 146.5),
            (146.2, 146.8, 144.5, 145),
            (144.8, 145, 143.5, 143.8),
        ]
        daily, bars, now = scenario(direction='down', prices=[p[3] for p in path], last_volume=2500)
        start = now.replace(hour=9, minute=30)
        today = [b for b in bars if datetime.fromtimestamp(b['t'], now.tzinfo).date() == now.date()]
        for i, (o, h, l, c) in enumerate(path):
            today[i].update(o=o, h=h, l=l, c=c, v=2200)
        today[-1]['v'] = 2600
        now = start + timedelta(minutes=15 * len(path))
        context = self.module().build_market_context(daily, bars, now)
        self.assertEqual(context['intraday_state']['code'], 'rebound_fail')
        result = self.module().entry_signal(context, px=145.0, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(result['advice'], '做空')
        self.assertTrue(result['actionable'])
        self.assertGreaterEqual(result['stop'], 147)

    def test_rebound_fail_rejects_on_peak_bar_without_waiting(self):
        # 反抽峰值当根收阴上影 → 立刻认失败，不等下一根
        path = [
            (151, 152, 145, 146),
            (146, 146.5, 143, 143.5),
            (143.5, 144, 142, 142.2),
            (146.8, 147.5, 142.5, 143.2),  # 冲高回落拒绝（收阴+长上影）
        ]
        daily, bars, now = scenario(direction='down', prices=[p[3] for p in path], last_volume=800)
        start = now.replace(hour=9, minute=30)
        today = [b for b in bars if datetime.fromtimestamp(b['t'], now.tzinfo).date() == now.date()]
        for i, (o, h, l, c) in enumerate(path):
            today[i].update(o=o, h=h, l=l, c=c, v=2200 if i < 3 else 600)
        # 开盘放量、拒绝根缩量：靠累计量比仍应确认
        today[0]['v'] = 5000
        now = start + timedelta(minutes=15 * len(path))
        context = self.module().build_market_context(daily, bars, now)
        self.assertEqual(context['intraday_state']['code'], 'rebound_fail')
        result = self.module().entry_signal(context, px=143.2, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(result['advice'], '做空')
        self.assertTrue(result['actionable'])
        self.assertGreaterEqual(result['stop'], 147.5)

    def test_open_drive_after_two_bars(self):
        daily, bars, now = scenario(direction='down', prices=[148.6, 147.2], last_volume=2500)
        start = now.replace(hour=9, minute=30)
        today = [b for b in bars if datetime.fromtimestamp(b['t'], now.tzinfo).date() == now.date()]
        today[0].update(o=151, h=152, l=148.5, c=148.6, v=5000)
        today[1].update(o=148.6, h=149.0, l=146.8, c=147.2, v=2200)
        now = start + timedelta(minutes=30)
        context = self.module().build_market_context(daily, bars, now)
        self.assertEqual(context['intraday_state']['code'], 'open_drive')
        result = self.module().entry_signal(context, px=147.2, quote_fresh=True, session_open=True, now=now)
        self.assertEqual(result['advice'], '做空')
        self.assertTrue(result['actionable'])


if __name__ == '__main__':
    unittest.main()
