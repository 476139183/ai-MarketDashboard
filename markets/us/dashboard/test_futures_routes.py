import json
import unittest
from unittest.mock import patch
import test_contract_routes as routes
from app import Handler
import io


class FuturesRouteTests(unittest.TestCase):
    get = routes.ContractRouteTests.get

    def post(self,body,origin=None):
        handler=object.__new__(Handler);handler.path='/api/futures/backtest'
        raw=json.dumps(body).encode();handler.rfile=io.BytesIO(raw)
        handler.headers={'Host':'127.0.0.1:8766','Content-Length':str(len(raw))}
        if origin:handler.headers['Origin']=origin
        out=[];handler._json=lambda status,data:out.append((status,data));handler.do_POST()
        return out[0]

    def test_backtest_requires_explicit_post_and_valid_same_origin(self):
        with patch('app.futures_backtest_jobs.start',return_value={'id':'abc','status':'running'}) as start,patch('app.paper_runner.start') as paper:
            status,body,_=self.get('/api/futures/backtest')
            self.assertEqual(status,200);self.assertIn('specs',json.loads(body));start.assert_not_called()
            self.assertEqual(self.post({'symbol':'RB2701'})[0],202);start.assert_called_once()
            self.assertEqual(self.post({'symbol':'RB2701'},'https://other.test')[0],403)
            paper.assert_not_called()
        self.assertEqual(self.post({'symbol':'RB0'})[0],400)
        status,_,_=self.get('/api/futures/backtest?id=missing');self.assertEqual(status,404)

    def test_radar_page_and_period_validation(self):
        status, body, _ = self.get('/futures-radar')
        self.assertEqual(status, 200)
        self.assertIn(b'futures-radar.js', body)
        with patch('app.futures_service.radar', return_value={'items': [], 'status': 'loading'}) as radar:
            status, body, _ = self.get('/api/futures/radar?period=15')
            self.assertEqual(status, 200)
            radar.assert_called_once_with('15')
        status, _, _ = self.get('/api/futures/radar?period=10')
        self.assertEqual(status, 400)

    def test_page_and_assets(self):
        for path, content in [('/futures', b'futures.js'),('/futures/', b'futures.js'),
                              ('/static/futures.js', b'/api/futures'),('/static/futures.css', b'futures-chart')]:
            with self.subTest(path=path):
                status, body, _ = self.get(path)
                self.assertEqual(status, 200);self.assertIn(content, body)

    def test_apis_are_isolated_and_bad_inputs_rejected(self):
        with patch('app.futures_service.snapshot',return_value={'items':[],'status':'loading'}) as snapshot,patch('app.analyze_many') as stocks:
            status, body, _ = self.get('/api/futures')
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['status'],'loading')
            snapshot.assert_called_once();stocks.assert_not_called()
        with patch('app.futures_service.chart',return_value={'symbol':'AU0','candles':[]}) as chart:
            self.get('/api/futures/chart?symbol=AU0&period=15')
            chart.assert_called_once_with('AU0','15')
        for query in ('symbol=BTCUSDT','symbol=RB0&period=10','symbol=RB9913'):
            status, _, _ = self.get('/api/futures/chart?'+query)
            self.assertEqual(status,400)

    def test_navigation_escapes_embedded_workspaces(self):
        from pathlib import Path
        root=Path(__file__).parent/'static'
        for name in ('research.html','index.html','contracts.html','paper.html','us-radar.html','news.html','a-review.html','a-radar.html'):
            with self.subTest(name=name):
                self.assertIn('href="/futures" target="_top"',(root/name).read_text())


if __name__=='__main__':unittest.main()
