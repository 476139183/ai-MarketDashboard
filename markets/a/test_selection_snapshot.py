import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import selection_snapshot as S


class SnapshotTests(unittest.TestCase):
    def test_invalid_partial_or_executable_payload_is_not_shown_as_research(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'latest.json';self.assertIsNone(S.load(path))
            row=dict(kind='a_share_selection_research',execution_enabled=False,signal_date='2026-11-11',total=100,stages={'candidate':1,'oversold':99})
            path.write_text(json.dumps(row));before=path.read_bytes();self.assertEqual(S.load(path),row);self.assertEqual(path.read_bytes(),before)
            row['stages']['oversold']=98;path.write_text(json.dumps(row));self.assertIsNone(S.load(path))
            row['stages']['oversold']=99;row['execution_enabled']=True;path.write_text(json.dumps(row));self.assertIsNone(S.load(path))
