import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import signal_watch as watch


class WatchTests(unittest.TestCase):
    def test_scan_starts_immediately_instead_of_waiting_a_quarter_hour(self):
        with patch.object(watch, "load_watchlist", return_value=["NVDA"]), \
             patch.object(watch, "scan_once", side_effect=KeyboardInterrupt) as scan, \
             patch.object(watch.time, "sleep", side_effect=AssertionError("首次巡检不能先休眠")):
            with self.assertRaises(KeyboardInterrupt):
                watch.run_loop(notify=False)
            scan.assert_called_once()

    def test_entry_alert_requires_confirmed_context_and_actionable_entry(self):
        self.assertFalse(watch.is_alertable(dict(advice="做多", at_entry=True,
                         actionable=False, trend_context=dict(confirmed=False))))
        self.assertTrue(watch.is_alertable(dict(advice="做多", at_entry=True,
                        actionable=True, trend_context=dict(confirmed=True))))
        self.assertTrue(watch.is_alertable(dict(advice="平多")))

    def test_failed_symbol_does_not_prevent_full_pool_scan(self):
        def analyze(symbol, **kwargs):
            if symbol == "BAD":
                raise RuntimeError("数据不可用")
            return dict(symbol=symbol, advice="观望", trend="上涨", reason="等待回踩")

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(watch, "STATE_PATH", Path(folder) / "state.json"), \
             patch.object(watch, "ALERTS_PATH", Path(folder) / "alerts.json"), \
             patch.object(watch, "analyze_symbol", side_effect=analyze):
            result = watch.scan_once(["NVDA", "BAD", "MU"], notify=False, pause=0)
            self.assertEqual(result["scanned_n"], 3)
            self.assertEqual(result["watch_n"], 3)
            self.assertFalse(result["scanning"])
            self.assertEqual(result["errors"][0]["symbol"], "BAD")
            self.assertEqual(len(result["items"]), 3)
            self.assertIn("exit_long", result["items"][0])


if __name__ == "__main__":
    unittest.main()
