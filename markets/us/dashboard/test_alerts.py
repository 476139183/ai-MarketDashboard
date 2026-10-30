import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app


class AlertTests(unittest.TestCase):
    def test_expired_alert_is_removed_from_active_but_kept_in_history(self):
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        old = {"symbol": "NVDA", "fired_bj": "2026-10-30 19:00:00"}
        fresh = {"symbol": "MU", "fired_bj": "2026-10-30 19:55:00"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alerts.json"
            path.write_text(json.dumps({"active": [old, fresh], "history": [old]}))
            with patch.object(app, "ALERTS", path):
                result = app.load_alerts(now)
        self.assertEqual(result["active"], [fresh])
        self.assertEqual(result["history"], [old])


if __name__ == "__main__":
    unittest.main()
