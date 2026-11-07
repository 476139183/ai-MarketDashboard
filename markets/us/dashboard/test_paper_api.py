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
        self.binance_runner = PaperRunner(PaperAccount(Path(directory.name) / 'binance.db', exchange='Binance'), None)
        substitute = patch('app.paper_runner', self.runner)
        substitute.start()
        self.addCleanup(substitute.stop)
        self.stock_runner=PaperRunner(PaperAccount(Path(directory.name)/'stocks.db',market='stock'),None)
        stock=patch('app.stock_paper_runner',self.stock_runner);stock.start();self.addCleanup(stock.stop)
        stock_start=patch.object(self.stock_runner,'start');stock_start.start();self.addCleanup(stock_start.stop)
        other = patch('app.binance_paper_runner', self.binance_runner)
        other.start()
        self.addCleanup(other.stop)
        other_start = patch.object(self.binance_runner, 'start')
        other_start.start()
        self.addCleanup(other_start.stop)
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

    def test_binance_control_and_reads_do_not_change_okx_account(self):
        status, data = self.call('POST', '/api/paper/control', {'enabled': True, 'exchange': 'binance'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['rules']['exchange'], 'Binance')
        self.assertFalse(self.runner.snapshot()['enabled'])
        status, data = self.call('GET', '/api/paper?exchange=binance')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)['enabled'])
        self.assertEqual(self.call('GET', '/api/paper?exchange=spot')[0], 400)
        self.assertEqual(self.call('POST', '/api/paper/control', {'enabled': True, 'exchange': 'spot'})[0], 400)

    def test_binance_radar_route(self):
        with patch('app.binance_radar.payload', return_value={'source': 'Binance'}):
            status, data = self.call('GET', '/api/contracts?exchange=binance')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['source'], 'Binance')

    def test_stock_account_control_is_independent_and_binance_stock_rejected(self):
        status,data=self.call('POST','/api/paper/control',{'enabled':True,'exchange':'okx','market':'stock'})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(data)['rules']['market'],'stock')
        self.assertFalse(self.runner.snapshot()['enabled'])
        self.assertEqual(self.call('GET','/api/paper?exchange=binance&market=stock')[0],400)
        self.assertEqual(self.call('GET','/api/paper?market=invalid')[0],400)

    def test_period_rejected_and_us_page_and_api_available(self):
        self.assertEqual(self.call('GET','/api/contracts?period=2')[0],400)
        self.assertEqual(self.call('GET','/api/us-radar?period=240')[0],400)
        self.assertEqual(self.call('GET','/api/us-radar?symbols=../NVDA')[0],400)
        self.assertIn(b'us-radar.js',self.call('GET','/us-radar')[1])
        with patch('app.us_radar.payload',return_value={'items':[]}) as radar:
            self.assertEqual(self.call('GET','/api/us-radar?symbols=NVDA,MU&period=10&provider=nasdaq&session=post')[0],200)
            radar.assert_called_once_with(['NVDA','MU'],'10','post','nasdaq')
    def test_live_chart_selects_independent_service_and_rejects_batch_symbols(self):
        with patch('app.us_chart.payload',return_value={'items':[]}) as chart:
            self.assertEqual(self.call('GET','/api/us-chart?symbols=SNDK&period=5&provider=sina')[0],200)
            chart.assert_called_once_with(['SNDK'],'5','all','sina')
        self.assertEqual(self.call('GET','/api/us-chart?symbols=SNDK,NVDA')[0],400)


if __name__ == '__main__':
    unittest.main()
