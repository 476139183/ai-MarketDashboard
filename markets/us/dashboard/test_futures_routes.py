import json
import unittest
from unittest.mock import patch
import test_contract_routes as routes


class FuturesRouteTests(unittest.TestCase):
    get = routes.ContractRouteTests.get

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
