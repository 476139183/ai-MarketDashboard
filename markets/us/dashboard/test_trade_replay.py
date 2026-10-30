import unittest

from backtest_trend_policy import replay_minutes, summarize, variant_signal


def minute(t, o=100, h=101, l=99, c=100):
    return dict(timestamp=t, open=o, high=h, low=l, close=c, volume=100)


def plan(side="做多"):
    return dict(advice=side, trend="上涨" if side == "做多" else "下跌",
                actionable=True, at_entry=True, entry_low=99, entry_high=101,
                stop=98 if side == "做多" else 102,
                target=104 if side == "做多" else 96)


class TradeReplayTests(unittest.TestCase):
    def test_signal_executes_after_close_with_one_minute_delay(self):
        rows = [minute(0), minute(60, o=100.2), minute(120, o=100.5)]
        trades = replay_minutes(rows, {0: plan()}, fee_bps=0, slip_bps=0)
        self.assertEqual(trades[0]["entry_t"], 60)
        self.assertEqual(trades[0]["entry"], 100.2)

    def test_open_outside_planned_band_is_not_filled(self):
        rows = [minute(0), minute(60, o=103), minute(120)]
        self.assertEqual(replay_minutes(rows, {0: plan()}), [])

    def test_fill_must_still_have_minimum_actual_reward_risk(self):
        rows = [minute(0), minute(60, o=101), minute(120)]
        self.assertEqual(replay_minutes(rows, {0: plan()}, fee_bps=0, slip_bps=0), [])

    def test_same_minute_touching_stop_and_target_uses_stop_first(self):
        rows = [minute(0), minute(60, h=105, l=97), minute(120)]
        trade = replay_minutes(rows, {0: plan()}, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["reason"], "stop")
        self.assertEqual(trade["exit"], 98)
        self.assertAlmostEqual(trade["net_R"], -1)

    def test_stop_gap_uses_worse_open(self):
        rows = [minute(0), minute(60), minute(120, o=95, h=96, l=94)]
        trade = replay_minutes(rows, {0: plan()}, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["exit"], 95)
        self.assertLess(trade["net_R"], -1)

    def test_known_target_gap_precedes_later_intraminute_stop_touch(self):
        rows = [minute(0), minute(60), minute(120, o=105, h=106, l=97)]
        trade = replay_minutes(rows, {0: plan()}, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["exit"], 104)
        self.assertEqual(trade["reason"], "target_gap")

    def test_short_target_gap_precedes_later_intraminute_stop_touch(self):
        rows = [minute(0), minute(60), minute(120, o=95, h=103, l=94)]
        trade = replay_minutes(rows, {0: plan("做空")}, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["exit"], 96)

    def test_unknown_hour_data_does_not_invent_new_exit_flags(self):
        observation = dict(after=dict(advice="观望", trend="震荡", exit_long=False, exit_short=False),
                           context=dict(bias="unknown", hourly_score=0))
        candidate = dict(name="h2_m2_r1.5", hour_score=2, entry_score=2, rr=1.5)
        signal = variant_signal(observation, candidate)
        self.assertFalse(signal["exit_long"])
        self.assertFalse(signal["exit_short"])

    def test_short_target_and_costs_have_correct_direction(self):
        rows = [minute(0), minute(60), minute(120, o=98, h=99, l=95, c=96)]
        no_cost = replay_minutes(rows, {0: plan("做空")}, fee_bps=0, slip_bps=0)[0]
        cost = replay_minutes(rows, {0: plan("做空")})[0]
        self.assertEqual(no_cost["reason"], "target")
        self.assertEqual(no_cost["net_R"], 2)
        self.assertLess(cost["net_R"], no_cost["net_R"])

    def test_direction_change_exits_after_delay(self):
        rows = [minute(0), minute(60), minute(120, o=101), minute(180, o=102)]
        signals = {0: plan(), 120: dict(trend="震荡", advice="观望", exit_long=True)}
        trade = replay_minutes(rows, signals, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["reason"], "direction_lost")
        self.assertEqual(trade["exit_t"], 180)

    def test_baseline_wait_does_not_invent_an_exit_rule(self):
        rows = [minute(0), minute(60), minute(120, o=101), minute(180, o=102)]
        signals = {0: plan(), 120: dict(trend="震荡", advice="观望")}
        trade = replay_minutes(rows, signals, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["reason"], "session_close")

    def test_stricter_entry_filter_preserves_risk_exit(self):
        observation = dict(after=dict(advice="平多", trend="上涨", short_trend=dict(score=1)),
                           context=dict(hourly_score=4))
        candidate = dict(name="h2_m3_r1.5", hour_score=2, entry_score=3, rr=1.5)
        self.assertEqual(variant_signal(observation, candidate)["advice"], "平多")

    def test_end_of_session_forces_flat_without_overnight_profit(self):
        rows = [minute(0), minute(60, c=100.5)]
        trade = replay_minutes(rows, {0: plan()}, fee_bps=0, slip_bps=0)[0]
        self.assertEqual(trade["reason"], "session_close")
        self.assertEqual(trade["exit_t"], 120)

    def test_empty_summary_does_not_invent_profit_factor(self):
        report = summarize([])
        self.assertEqual(report["trades"], 0)
        self.assertIsNone(report["profit_factor"])


if __name__ == "__main__":
    unittest.main()
