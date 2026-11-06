import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import Handler
from paper_trading import PaperAccount, PaperRunner


class PaperAPITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        account = PaperAccount(Path(directory.name) / 'paper.db')
        self.runner = PaperRunner(account, None)
        substitute = patch('app.paper_runner', self.runner)
        substitute.start()
        self.addCleanup(substitute.stop)
        start = patch.object(self.runner, 'start')
        start.start()
        self.addCleanup(start.stop)

    def call(self, method, path, body=None, origin=None):
        handler = object.__new__(Handler)
        handler.path = path
        raw = json.dumps(body).encode() if body is not None else b''
        handler.rfile = io.BytesIO(raw)
        handler.headers = {'Host': '127.0.0.1:8766', 'Content-Length': str(len(raw))}
        if origin:
            handler.headers['Origin'] = origin
        result = []
        handler._send = lambda status, data, mime: result.append((status, data))
        getattr(handler, 'do_' + method)()
        return result[0]

    def test_initial_account_and_local_enable_pause(self):
        status, data = self.call('GET', '/api/paper')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)['enabled'])
        self.assertEqual(json.loads(data)['cash'], 10000)
        for enabled in (True, False):
            status, data = self.call('POST', '/api/paper/control', {'enabled': enabled})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['enabled'], enabled)

    def test_bad_control_and_foreign_origin_do_not_change_account(self):
        for body in ({'enabled': 'yes'}, {'enabled': 1}, {}, ['enabled']):
            status, _ = self.call('POST', '/api/paper/control', body)
            self.assertEqual(status, 400)
        status, _ = self.call('POST', '/api/paper/control', {'enabled': True}, 'https://foreign.test')
        self.assertEqual(status, 403)
        self.assertFalse(self.runner.snapshot()['enabled'])

    def test_exchange_queries_select_separate_sources(self):
        with patch('app.contract_radar.payload', return_value={'source': 'OKX'}) as okx, \
             patch('app.bitget_radar.payload', return_value={'source': 'Bitget'}) as bitget:
            for query, source in (('', 'OKX'), ('?exchange=okx', 'OKX'), ('?exchange=bitget', 'Bitget')):
                status, data = self.call('GET', '/api/contracts' + query)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(data)['source'], source)
            self.assertEqual(okx.call_count, 2)
            self.assertEqual(bitget.call_count, 1)
            self.assertEqual(self.call('GET', '/api/contracts?exchange=spot')[0], 400)

    def test_page_is_served(self):
        status, html = self.call('GET', '/paper')
        self.assertEqual(status, 200)
        self.assertIn(b'paper.js', html)


if __name__ == '__main__':
    unittest.main()
