import unittest
from okx_contracts import OKXFeed


class OKXFeedTests(unittest.TestCase):
    def fetch(self, path, params):
        self.calls.append((path, params))
        if path.endswith('instruments'):
            return [dict(instId='BTC-USDT-SWAP', state='live', ctType='linear',
                         settleCcy='USDT', ctVal='0.01', ctValCcy='BTC', lotSz='0.01', minSz='0.01')]
        if path.endswith('tickers'):
            return [dict(instId='BTC-USDT-SWAP', last='110', open24h='100', ts='1000',
                         bidPx='109', askPx='111', volCcy24h='50')]
        if path.endswith('mark-price'):
            return [dict(instId='BTC-USDT-SWAP', markPx='108')]
        if path.endswith('open-interest'):
            return [dict(instId='BTC-USDT-SWAP', oiCcy='10', oiUsd='1080')]
        if path.endswith('funding-rate'):
            return [dict(instId='BTC-USDT-SWAP', fundingRate='0.0001')]
        if path.endswith('candles'):
            return [['14400000', '100', '105', '99', '104', '20', '2', '208', '1'],
                    ['28800000', '104', '110', '100', '109', '30', '3', '327', '0']]
        raise AssertionError(path)

    def setUp(self):
        self.calls = []
        self.feed = OKXFeed(self.fetch)

    def test_swap_identifiers_prices_and_units(self):
        row = self.feed('tickers', {})[0]
        self.assertEqual(row['symbol'], 'BTCUSDT')
        self.assertEqual(row['instId'], 'BTC-USDT-SWAP')
        self.assertAlmostEqual(row['change24h'], .1)
        self.assertEqual(row['lastPr'], '110')
        self.assertEqual(row['holdingAmount'], '10')
        self.assertEqual(row['usdtVolume'], 5500)
        self.assertTrue(row['volume_estimated'])
        self.assertEqual(row['ctVal'], '0.01')
        self.assertTrue(all(params.get('instType', 'SWAP') == 'SWAP' for _, params in self.calls))

    def test_unconfirmed_candles_are_excluded_and_base_volume_is_used(self):
        rows = self.feed('candles', {'symbol': 'BTCUSDT'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], '2')
        self.assertEqual(rows[0][6], '208')
        self.assertEqual(self.calls[-1][1]['instId'], 'BTC-USDT-SWAP')

    def test_optional_metric_failure_keeps_quote_and_never_switches_exchange(self):
        def fail(path, params):
            if path.endswith(('funding-rate', 'open-interest', 'mark-price')):
                raise OSError('unavailable')
            return self.fetch(path, params)
        row = OKXFeed(fail)('tickers', {})[0]
        self.assertEqual(row['lastPr'], '110')
        self.assertIsNone(row['fundingRate'])
        self.assertIsNone(row['holdingAmount'])


if __name__ == '__main__':
    unittest.main()
