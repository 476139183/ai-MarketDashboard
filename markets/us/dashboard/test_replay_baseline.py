import unittest
from unittest.mock import patch

import backtest_trend_policy as replay


class BaselineTests(unittest.TestCase):
    def test_baseline_is_pinned_and_does_not_follow_new_commits(self):
        with patch.object(replay.subprocess, "run") as git:
            git.return_value.stdout = "baseline source"
            self.assertEqual(replay.baseline_source(), "baseline source")
        self.assertEqual(git.call_args.args[0], [
            "git", "show", "09a8a1f103f1ad51e4daf55f9a496b96640edb1a:markets/us/dashboard/engine.py"])


if __name__ == "__main__":
    unittest.main()
