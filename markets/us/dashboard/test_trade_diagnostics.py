import unittest

from trade_diagnostics import transform_signals, minute_signals, diagnose_trade
from test_trade_replay import plan, minute


class DiagnosticTests(unittest.TestCase):
    def inputs(self):
        return dict(px=100, atr=8, orb_l=94, orb_h=101, vwap=99,
                    day_o=95, path_l=94, path_h=102,
                    bars=[dict(t=i*900, o=98+i*.3, h=99+i*.3, l=97+i*.3, c=98+i*.3, v=1000)
                          for i in range(5)])

    def record(self, signals):
        return dict(signals={t: dict(after=sig, context=dict(bias="up", hourly_score=3),
                                   inputs=self.inputs()) for t, sig in signals.items()})

    def test_two_bar_soft_exit_waits_and_keeps_hard_exit(self):
        record = self.record({900: dict(advice="观望", exit_long=True),
                              1800: dict(advice="观望", exit_long=True),
                              2700: dict(advice="平空", exit_short=True, short_invalid=True)})
        signals = transform_signals(record, "confirm_exit")
        self.assertFalse(signals[900]["exit_long"])
        self.assertTrue(signals[1800]["exit_long"])
        self.assertTrue(signals[2700]["exit_short"])
        self.assertEqual(signals[2700]["advice"], "平空")

    def test_missing_observation_breaks_exit_confirmation(self):
        record = self.record({900: dict(advice="观望", exit_long=True),
                              2700: dict(advice="观望", exit_long=True)})
        self.assertFalse(transform_signals(record, "confirm_exit")[2700]["exit_long"])

    def test_hard_exit_only_keeps_orb_exit(self):
        record = self.record({900: dict(advice="观望", exit_long=True),
                              1800: dict(advice="平多", exit_long=True, long_invalid=True)})
        result = transform_signals(record, "hard_exit")
        self.assertFalse(result[900]["exit_long"])
        self.assertTrue(result[1800]["exit_long"])

    def test_swing_stop_uses_known_structure_and_target_stays_fixed(self):
        sig = plan()
        sig.update(stop=94, target=110, ref_price=100, short_trend=dict(score=3))
        record = self.record({4500: sig})
        result = transform_signals(record, "swing_stop")[4500]
        self.assertGreater(result["stop"], 94)
        self.assertLess(result["stop"], 100)
        self.assertEqual(result["target"], 110)

    def test_entry_filter_does_not_swallow_position_exit(self):
        record = self.record({900: dict(advice="平多", exit_long=True, long_invalid=True)})
        # Fixture timestamps are not late NY time, but an explicit exit must survive any entry rule.
        result = transform_signals(record, "before_1400")[900]
        self.assertTrue(result["exit_long"])

    def test_minute_trigger_never_uses_future_minutes(self):
        record = self.record({4500: dict(advice="观望", actionable=False, exit_long=False, exit_short=False)})
        record["minutes"] = [minute(4500, c=100), minute(4560, c=100.2), minute(4620, h=10000, c=101)]
        earlier = minute_signals({**record, "minutes": record["minutes"][:2]})
        full = minute_signals(record)
        self.assertEqual(earlier[4560], full[4560])
        self.assertEqual(earlier[4620], full[4620])

    def test_minute_entry_check_does_not_add_intraminute_trend_exit(self):
        record = self.record({4500: dict(advice="观望", actionable=False, exit_long=False, exit_short=False)})
        record["minutes"] = [minute(4500, c=1)]
        result = minute_signals(record)[4560]
        self.assertFalse(result.get("exit_long"))
        self.assertFalse(result.get("exit_short"))

    def test_minute_trigger_keeps_the_existing_plan_prices(self):
        sig = plan()
        sig.update(ref_price=100, atr_i=2.8, short_trend=dict(score=3),
                   vwap=99, broke_up=True, orb=dict(low=94, high=101))
        record = self.record({4500: sig})
        record["minutes"] = [minute(4500, c=100), minute(4560, c=100.2)]
        result = minute_signals(record)
        for observation in result.values():
            self.assertEqual(observation["entry_low"], 99)
            self.assertEqual(observation["entry_high"], 101)
            self.assertEqual(observation["target"], 104)

    def test_mfe_does_not_use_extrema_after_open_exit(self):
        record = self.record({0: dict(advice="观望")})
        record["minutes"] = [minute(60), minute(120, h=10000, l=1)]
        trade = dict(entry_t=60, exit_t=120, signal_t=0, entry=100, exit=100.5,
                     target=104, risk=2, side="做多")
        result = diagnose_trade(trade, record)
        self.assertEqual(result["mfe_R_before_exit"], .5)
        self.assertEqual(result["mae_R_before_exit"], -.5)


if __name__ == "__main__":
    unittest.main()
