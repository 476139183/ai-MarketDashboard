import copy
import unittest

import contract_radar as radar


STEP = 4 * 60 * 60 * 1000
NOW = 1800000000000 // STEP * STEP + 60000


def candles(falling=False):
    boundary = NOW // STEP * STEP
    result = []
    for i in range(100):
        price = 250 - i if falling else 100 + i
        result.append([str(boundary - (100 - i) * STEP), str(price),
                       str(price + 2), str(price - 2), str(price + .5), '10', '1000'])
    return result


def ticker(**overrides):
    return dict(symbol='BTCUSDT', lastPr='200', ts=str(NOW), change24h='.05',
                fundingRate='.0001', holdingAmount='12', markPrice='201',
                usdtVolume='1000000', **overrides)


def analyze(rows=None, quote=None, now=NOW):
    return radar.analyze_contract('BTCUSDT', '比特币', 'crypto',
                                  quote if quote is not None else ticker(),
                                  rows if rows is not None else candles(), now)


class ContractAnalysisTests(unittest.TestCase):
    def test_closed_trend_ignores_unfinished_and_future_candles(self):
        rows = candles()
        boundary = NOW // STEP * STEP
        rows.extend([[str(boundary), '1', '999999', '.1', '1', '20', '200'],
                     [str(boundary + STEP), '1', '2', '.1', '1', '20', '200']])
        result = analyze(list(reversed(rows)))
        self.assertEqual(result['direction'], 'bullish')
        self.assertEqual(result['candle_close_at'], boundary)
        self.assertLess(result['ema20'], 200)

    def test_bearish_trend(self):
        self.assertEqual(analyze(candles(True))['direction'], 'bearish')

    def test_flat_prices_are_observe(self):
        rows = candles()
        for row in rows:
            row[1:5] = ['100', '101', '99', '100']
        self.assertEqual(analyze(rows)['direction'], 'observe')

    def test_stale_and_future_quotes_do_not_generate_direction(self):
        for offset in (-31000, 6000):
            quote = ticker()
            quote['ts'] = str(NOW + offset)
            result = analyze(quote=quote)
            self.assertEqual(result['direction'], 'observe')
            self.assertFalse(result['quote_fresh'])

    def test_missing_old_or_gapped_candles_are_unconfirmed(self):
        for rows in (candles()[:30], candles()[:-1], candles()[:80] + candles()[81:]):
            result = analyze(rows)
            self.assertEqual(result['direction'], 'observe')
            self.assertFalse(result['structure_fresh'])

    def test_invalid_prices_cannot_generate_direction_or_nan_json(self):
        for value in ('NaN', 'Infinity', '-1', ''):
            quote = ticker()
            quote['lastPr'] = value
            result = analyze(quote=quote)
            self.assertEqual(result['direction'], 'observe')
            self.assertIsNone(result['price'])

    def test_invalid_ohlc_and_conflicting_duplicates_are_rejected(self):
        rows = candles()
        rows[-1][2] = '1'
        self.assertFalse(analyze(rows)['structure_fresh'])
        rows = candles()
        duplicate = copy.deepcopy(rows[-1])
        duplicate[4] = '199'
        rows.append(duplicate)
        self.assertFalse(analyze(rows)['structure_fresh'])

    def test_units_and_nullable_optional_fields(self):
        result = analyze()
        self.assertAlmostEqual(result['change_pct'], 5)
        self.assertAlmostEqual(result['funding_pct'], .01)
        self.assertEqual(result['open_interest_usdt'], 2412)
        quote = ticker()
        for key in ('fundingRate', 'holdingAmount', 'markPrice', 'change24h'):
            quote.pop(key)
        result = analyze(quote=quote)
        for key in ('funding_pct', 'open_interest_usdt', 'change_pct'):
            self.assertIsNone(result[key])

    def test_missing_contract_has_no_stock_substitution(self):
        result = analyze(quote={})
        self.assertEqual(result['direction'], 'observe')
        self.assertIsNone(result['price'])


class RadarServiceTests(unittest.TestCase):
    def test_partial_failure_keeps_other_symbols_and_cached_view_expires(self):
        def fetch(path, params):
            if path.endswith('tickers'):
                return [ticker(), dict(ticker(), symbol='ETHUSDT')]
            if params['symbol'] == 'ETHUSDT':
                raise OSError('offline')
            return candles()

        service = radar.ContractRadar(fetch, [('BTCUSDT', '比特币', 'crypto'),
                                               ('ETHUSDT', '以太坊', 'crypto'),
                                               ('MUUSDT', '美光', 'stock')])
        service._refresh(NOW)
        payload = service.snapshot(NOW)
        self.assertEqual(len(payload['items']), 3)
        self.assertEqual(payload['items'][0]['direction'], 'bullish')
        self.assertEqual(payload['items'][1]['direction'], 'observe')
        self.assertIsNone(payload['items'][2]['price'])
        old = service.snapshot(NOW + 31000)
        self.assertTrue(all(row['direction'] == 'observe' for row in old['items']))

    def test_api_failure_is_explicit_and_does_not_erase_last_quote(self):
        failed = False

        def fetch(path, params):
            if failed:
                raise OSError('network unavailable')
            return [ticker()] if path.endswith('tickers') else candles()

        service = radar.ContractRadar(fetch, [('BTCUSDT', '比特币', 'crypto')])
        service._refresh(NOW)
        failed = True
        service._refresh(NOW + 40000)
        payload = service.snapshot(NOW + 40000)
        self.assertIn('network unavailable', payload['error'])
        self.assertEqual(payload['items'][0]['price'], 200)
        self.assertEqual(payload['items'][0]['direction'], 'observe')


if __name__ == '__main__':
    unittest.main()
