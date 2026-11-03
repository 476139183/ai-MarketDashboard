import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import engine


def candles(closes, start=None, step=900):
    start = start or datetime(2026, 10, 30, 9, 30, tzinfo=engine.NY)
    return [dict(t=int((start + timedelta(seconds=i * step)).timestamp()),
                 dt_ny=start + timedelta(seconds=i * step),
                 dt_bj=(start + timedelta(seconds=i * step)).astimezone(engine.BJ),
                 o=c - .1, h=c + .2, l=c - .2, c=c, v=1000)
            for i, c in enumerate(closes)]


class TrendPolicyTests(unittest.TestCase):
    def context(self, bias="up"):
        return dict(bias=bias, daily_bias=bias, hourly_bias=bias,
                    confirmed=bias in ("up", "down"), reason="测试同向趋势")

    def signal(self, context=None, **changes):
        args = dict(px=104, atr=8, orb_l=99, orb_h=103, vwap=103.5,
                    day_o=100, path_l=99, path_h=110,
                    bars=candles([100, 101, 102, 103, 104, 105, 104.8, 105.5]),
                    trend_context=context or self.context())
        args.update(changes)
        return engine.compute_intraday_signal(**args)

    def test_daily_and_hourly_disagreement_cannot_generate_entry_plan(self):
        result = self.signal(context=dict(bias="mixed", confirmed=False,
                                         daily_bias="up", hourly_bias="down", reason="日线与1小时分歧"))
        self.assertEqual(result["advice"], "观望")
        self.assertIsNone(result["E_band"])
        self.assertFalse(result["actionable"])
        self.assertTrue(result["exit_long"])
        self.assertTrue(result["exit_short"])

    def test_intraday_pullback_cannot_reverse_confirmed_overall_direction(self):
        result = self.signal(px=101, vwap=104, path_l=98,
                             bars=candles([108, 107, 106, 105, 104, 103, 102, 101]))
        self.assertEqual(result["trend"], "上涨")
        self.assertEqual(result["advice"], "观望")
        self.assertIsNone(result["E_band"])

    def test_confirmed_uptrend_has_reachable_pullback_plan(self):
        result = self.signal()
        self.assertEqual(result["advice"], "做多")
        self.assertGreaterEqual(result["entry_low"], 103)
        self.assertLessEqual(result["entry_low"], 104)
        self.assertGreaterEqual(result["entry_high"], 104)
        self.assertTrue(result["at_entry"])
        self.assertGreaterEqual(result["R1"], engine.TARGET_RR)

    def test_downtrend_only_generates_short_plan(self):
        result = self.signal(context=self.context("down"), px=101, vwap=101.5,
                             orb_l=102, orb_h=106, path_l=96, path_h=107,
                             bars=candles([108, 107, 106, 105, 104, 103, 102, 101]))
        self.assertEqual(result["advice"], "做空")
        self.assertTrue(result["at_entry"])
        self.assertLess(result["target"], result["ref_price"])
        self.assertGreater(result["stop"], result["ref_price"])

    def test_risk_exit_survives_unknown_direction(self):
        result = self.signal(context=dict(bias="unknown", confirmed=False, reason="数据不足"),
                             px=97, path_h=110)
        self.assertEqual(result["advice"], "平多")
        self.assertFalse(result["at_entry"])

    def test_unconfirmed_context_has_no_entry_prices(self):
        result = self.signal(context=dict(bias="unknown", confirmed=False, reason="缺少日线"))
        self.assertIsNone(result["E_band"])
        self.assertIsNone(result["target"])

    def test_invalid_entry_band_is_rejected(self):
        result = self.signal(px=2, atr=100, orb_l=1.9, orb_h=2.2, vwap=2,
                             path_l=1.8, path_h=3,
                             bars=candles([1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2]))
        self.assertEqual(result["advice"], "观望")
        self.assertIsNone(result["E_band"])

    def test_daily_context_ignores_todays_unfinished_candle(self):
        now = datetime(2026, 10, 30, 12, tzinfo=engine.NY)
        start = now - timedelta(days=35)
        daily = candles([100 + i for i in range(35)], start, step=86400)
        expected = engine.confirm_trend(daily, [], now)["daily_bias"]
        daily += candles([1], now)
        self.assertEqual(engine.confirm_trend(daily, [], now)["daily_bias"], expected)
        self.assertEqual(expected, "up")

    def test_hourly_context_ignores_unfinished_hour(self):
        now = datetime(2026, 10, 30, 12, tzinfo=engine.NY)
        bars = []
        for days in (3, 2, 1, 0):
            start = (now - timedelta(days=days)).replace(hour=9, minute=30)
            bars += candles([100 + len(bars) * .1 + i * .1 for i in range(24)], start)
        before = engine.confirm_trend([], bars, now)["hourly_bias"]
        future = candles([1], now.replace(hour=11, minute=45))
        after = engine.confirm_trend([], bars + future, now)["hourly_bias"]
        self.assertEqual(before, "up")
        self.assertEqual(after, before)

    def test_sampled_prices_cannot_overwrite_real_volume_bars(self):
        real = candles([104, 105, 106, 107, 108])
        sampled = candles([200, 201, 202, 203, 204])
        for bar in sampled:
            bar["v"] = 0
        with patch.object(engine, "fetch_nasdaq_intraday_points", return_value=([], {})), \
             patch.object(engine, "aggregate_points_to_bars", return_value=sampled), \
             patch.object(engine, "fetch_chart", return_value=(real, {}, "live")), \
             patch.object(engine, "_fetch_yfinance_chart", return_value=None):
            result, _, _ = engine.fetch_bars_intraday("NVDA")
        self.assertEqual(result[0]["v"], 1000)
        self.assertEqual(result[0]["c"], 104)

    def test_old_cache_is_refreshed_instead_of_becoming_permanent_daily_bias(self):
        old = candles([100] * 30, datetime(2026, 8, 1, 9, 30, tzinfo=engine.NY), 86400)
        fresh = candles([101] * 30, datetime.now(engine.NY) - timedelta(days=30), 86400)
        with patch.object(engine, "_load_cache", return_value=(old, {})), \
             patch.object(engine, "_yahoo_http_json", return_value={}) as http, \
             patch.object(engine, "_parse_chart", return_value=(fresh, {})), \
             patch.object(engine, "_save_cache"):
            rows, _, source = engine.fetch_chart("NVDA", "3mo", "1d", prefer_cache=True)
        self.assertEqual(source, "live")
        http.assert_called()

    def test_stale_intraday_cache_is_rejected_then_yfinance_used(self):
        day = engine._expected_us_session_date()
        stale = candles([100] * 8, datetime(2026, 10, 20, 9, 30, tzinfo=engine.NY))
        fresh = candles([110] * 26, datetime(day.year, day.month, day.day, 9, 30, tzinfo=engine.NY))
        for bar in fresh:
            bar["feed"] = "yfinance"
        with patch.object(engine, "_load_cache", return_value=(stale, {})), \
             patch.object(engine, "_yahoo_http_json", side_effect=engine.urllib.error.HTTPError(
                 "https://query1.finance.yahoo.com", 429, "Too Many Requests", hdrs=None, fp=None)), \
             patch.object(engine, "_fetch_yfinance_chart", return_value=(fresh, {"source": "yfinance"})), \
             patch.object(engine, "_save_cache"):
            rows, meta, source = engine.fetch_chart("SNDK", "5d", "15m", prefer_cache=True)
        self.assertEqual(source, "yfinance")
        self.assertGreaterEqual(engine._session_volume_bars(rows), 3)
        self.assertIn("yfinance", meta.get("feed_warning") or "")

    def test_zero_volume_sample_cannot_overwrite_volume_bar_on_merge(self):
        volume = candles([104], datetime(2026, 11, 2, 10, 0, tzinfo=engine.NY))
        sampled = candles([200], datetime(2026, 11, 2, 10, 0, tzinfo=engine.NY))
        sampled[0]["v"] = 0
        sampled[0]["feed"] = "nasdaq_sampled"
        merged = engine._merge_bar_rows(sampled, volume)
        self.assertEqual(merged[0]["c"], 104)
        self.assertEqual(merged[0]["v"], 1000)


class LiveTrendTests(unittest.TestCase):
    def analyze(self, *, volume=1000, minutes_old=0, is_rth=True, quote_time="Oct 30, 2026 12:05 PM ET"):
        now = datetime(2026, 10, 30, 12, 5, tzinfo=engine.NY)
        daily = candles([100 + i for i in range(70)], now - timedelta(days=70), 86400)
        bars = candles([100 + i * .2 for i in range(11)])
        bars[-1].update(c=1, h=1000, l=1)  # 12:00 未收盘，不得进入趋势/ORB/VWAP。
        for bar in bars:
            bar["v"] = volume
            bar["t"] -= minutes_old * 60
            bar["dt_ny"] -= timedelta(minutes=minutes_old)
            bar["dt_bj"] -= timedelta(minutes=minutes_old)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz or engine.BJ)

        with patch.object(engine, "datetime", Clock), \
             patch.object(engine, "fetch_chart", return_value=(daily, {}, "cache")), \
             patch.object(engine, "fetch_bars_intraday", return_value=(bars, {}, "live")), \
             patch.object(engine, "fetch_nasdaq_quote", return_value={}), \
             patch.object(engine, "resolve_display_price", return_value=(102, 100, 2, "实时", "Nasdaq", quote_time)), \
             patch.object(engine, "session_status", return_value={"is_rth": is_rth, "phase": "常规盘中"}), \
             patch.object(engine, "confirm_trend", return_value=dict(bias="up", confirmed=True, reason="日线与1h偏多")) as confirm, \
             patch.object(engine, "compute_intraday_signal", wraps=engine.compute_intraday_signal) as compute:
            result = engine.analyze_symbol("TEST")
            return result, compute, confirm

    def test_unfinished_15m_bar_is_excluded(self):
        result, _, _ = self.analyze()
        self.assertEqual(result['intraday_state']['last_close'], 101.8)
        self.assertEqual(result['intraday_state']['bar_count'], 10)
        self.assertEqual(result['daily_context']['bias'], 'up')

    def test_missing_volume_blocks_entry_instead_of_faking_vwap(self):
        result, compute, _ = self.analyze(volume=0)
        self.assertEqual(result["advice"], "观望")
        self.assertIsNone(result["vwap"])
        self.assertFalse(result["actionable"])
        self.assertIn("成交量", result["reason"])
        compute.assert_not_called()

    def test_stale_15m_structure_cannot_generate_entry(self):
        result, compute, _ = self.analyze(minutes_old=120)
        self.assertFalse(result["actionable"])
        self.assertIn("过期", result["reason"])
        compute.assert_not_called()

    def test_non_rth_does_not_create_entry_plans(self):
        result, compute, _ = self.analyze(is_rth=False)
        self.assertEqual(result["advice"], "观望")
        self.assertFalse(result["actionable"])
        compute.assert_not_called()

    def test_old_quote_cannot_create_entry_plan(self):
        result, compute, _ = self.analyze(quote_time="Oct 29, 2026 12:05 PM ET")
        self.assertFalse(result["actionable"])
        self.assertIn("报价", result["reason"])
        compute.assert_not_called()

    def test_missing_quote_timestamp_does_not_claim_fresh(self):
        result, compute, _ = self.analyze(quote_time=None)
        self.assertFalse(result["quote_fresh"])
        compute.assert_not_called()

    def test_watchlist_results_keep_order_and_isolate_data_failures(self):
        def analyze(symbol, **kwargs):
            if symbol == "BAD":
                raise RuntimeError("no data")
            return dict(symbol=symbol, signal="观望")
        with patch.object(engine, "analyze_symbol", side_effect=analyze):
            result = engine.analyze_many(["MU", "BAD", "NVDA"])
        self.assertEqual([r["symbol"] for r in result["items"]], ["MU", "BAD", "NVDA"])
        self.assertEqual(result["errors"][0]["symbol"], "BAD")


if __name__ == "__main__":
    unittest.main()
