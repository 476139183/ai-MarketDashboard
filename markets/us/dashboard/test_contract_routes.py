import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app import Handler


class ContractRouteTests(unittest.TestCase):
    def get(self, path):
        handler = object.__new__(Handler)
        handler.path = path
        result = []
        header = {}
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: header.update(status=status)
        handler.send_header = lambda key, value: header.update({key: value})
        handler.end_headers = lambda: None
        handler._send = lambda status, data, mime: result.append((status, data, mime))
        handler.do_GET()
        return result[0] if result else (header['status'], handler.wfile.getvalue(), header['Content-Type'])

    def test_page_static_assets_and_existing_a_share_radar(self):
        for path, expected in [('/contracts', b'contracts.js'),
                               ('/contracts/', b'contracts.js'),
                               ('/static/contracts.js', b'/api/contracts'),
                               ('/static/contracts.css', b'contracts-workspace'),
                               ('/a-radar', b'a-radar.js')]:
            with self.subTest(path=path):
                status, body, _ = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn(expected, body)

    def test_contract_api_does_not_call_stock_engine(self):
        payload = {'items': [], 'source': 'Bitget', 'status': 'running'}
        with patch('app.contract_radar.payload', return_value=payload), patch('app.analyze_many') as stocks:
            status, body, _ = self.get('/api/contracts')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), payload)
        stocks.assert_not_called()

    def test_contract_links_escape_embedded_stock_workspaces(self):
        root = Path(__file__).parent / 'static'
        for name in ('research.html', 'index.html', 'a-radar.html', 'a-review.html'):
            self.assertIn('href="/contracts" target="_top"', (root / name).read_text())


if __name__ == '__main__':
    unittest.main()
