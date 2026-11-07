import unittest
from unittest.mock import patch
from binance_contracts import BinanceFeed


class BinanceFeedTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def fetch(self, path, params):
        self.calls.append((path, params))
        if path.endswith('exchangeInfo'):
            valid = dict(symbol='BTCUSDT', status='TRADING', contractType='PERPETUAL',
                         quoteAsset='USDT', marginAsset='USDT', underlyingType='COIN',
                         filters=[dict(filterType='LOT_SIZE', stepSize='.001', minQty='.001', maxQty='100'),
                                  dict(filterType='MARKET_LOT_SIZE', stepSize='.002', minQty='.002', maxQty='10'),
                                  dict(filterType='MIN_NOTIONAL', notional='5')])
            return {'symbols': [valid, dict(valid, symbol='ETHUSDT', status='SETTLING'),
                                dict(valid, symbol='SOLUSDT', contractType='CURRENT_QUARTER')]}
        if path.endswith('24hr'):
            return [dict(symbol='BTCUSDT', lastPrice='100', closeTime=10000, priceChangePercent='5', quoteVolume='12345')]
        if path.endswith('bookTicker'):
            return [dict(symbol='BTCUSDT', bidPrice='99', askPrice='101', time=9000)]
        if path.endswith('premiumIndex'):
            return [dict(symbol='BTCUSDT', markPrice='100', lastFundingRate='.0001')]
        if path.endswith('openInterest'):
            return dict(openInterest='12')
        if path.endswith('klines'):
            return [[0, '100', '105', '99', '104', '20', 14399999, '2080'],
                    [14400000, '104', '110', '100', '109', '30', 28799999, '3270']]
        raise AssertionError(path)

    def test_only_trading_usdt_perpetuals_and_correct_percentage_units(self):
        feed = BinanceFeed(self.fetch)
        rows = feed('tickers', {})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['instId'], 'BTCUSDT')
        self.assertAlmostEqual(row['change24h'], .05)
        self.assertEqual(row['usdtVolume'], '12345')
        self.assertEqual(row['ts'], 9000)
        self.assertEqual(row['ctVal'], 1)
        self.assertEqual(row['lotSz'], '.002')
        self.assertEqual(row['maxSz'], '10')
        self.assertEqual(row['minNotional'], '5')

    def test_candles_use_base_volume_quote_volume_and_exclude_open_bar(self):
        with patch('binance_contracts.time.time', return_value=14400.001):
            rows = BinanceFeed(self.fetch)('candles', {'symbol': 'BTCUSDT'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5:], ['20', '2080'])
        self.assertEqual(self.calls[-1][1]['interval'], '4h')

    def test_missing_or_stale_book_is_not_made_fresh_by_new_ticker(self):
        def missing(path, params):
            return [] if path.endswith('bookTicker') else self.fetch(path, params)
        row = BinanceFeed(missing)('tickers', {})[0]
        self.assertIsNone(row['ts'])
        self.assertIsNone(row['bidPr'])


if __name__ == '__main__':
    unittest.main()
