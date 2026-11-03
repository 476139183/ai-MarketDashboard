import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import radar


class RadarTests(unittest.TestCase):
    def test_watch_note_marks_strategy_vs_defense(self):
        self.assertIn('优先观察', radar._watch_note('策略候选', ['trend'], True))
        self.assertIn('未站上MA20', radar._watch_note('观察池', ['breakout'], False))

    def test_load_cached_marks_stale_after_max_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'radar-latest.json'
            old = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(hours=20)).isoformat()
            path.write_text('{"generated_at":"%s","signal_date":"2026-11-03"}' % old, encoding='utf-8')
            with mock.patch.object(radar, 'OUT', path):
                data = radar.load_cached(max_age_hours=12)
            self.assertTrue(data['stale'])
            self.assertEqual(data['signal_date'], '2026-11-03')


if __name__ == '__main__':
    unittest.main()
