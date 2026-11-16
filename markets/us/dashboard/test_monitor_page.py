import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app import load_monitor_report

class MonitorPageTests(unittest.TestCase):
    def test_missing_report_is_not_live(self):
        with tempfile.TemporaryDirectory() as folder:
            result=load_monitor_report(Path(folder)/'missing.json', datetime.now(timezone.utc))
            self.assertFalse(result['available'])

    def test_old_report_is_marked_stale(self):
        now=datetime(2026,10,29,8,30,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'latest.json'
            p.write_text(json.dumps({'generated_bj':(now-timedelta(minutes=30)).isoformat(),'quote_fresh':True,'current_price':100}))
            report=load_monitor_report(p,now)
            self.assertFalse(report['report_fresh'])
            self.assertFalse(report['quote_fresh'])
            self.assertIsNone(report['current_price'])

if __name__=='__main__':unittest.main()
