from pathlib import Path
import tempfile
import unittest
from paper_trading import PaperAccount
from test_paper_trading import market, NOW


class PaperExchangeTests(unittest.TestCase):
    def test_separate_accounts_reject_cross_exchange_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            okx = PaperAccount(Path(directory) / 'okx.db')
            binance = PaperAccount(Path(directory) / 'binance.db', exchange='Binance')
            for account in (okx, binance):
                account.set_enabled(True)
            quote = market()
            binance.step(quote, NOW)
            self.assertEqual(binance.snapshot()['pending_signals'], 0)
            self.assertIn('Binance', binance.snapshot()['error'])
            for offset in (0, 10000):
                quote = market(NOW + offset)
                quote['source'] = quote['items'][0]['source'] = 'Binance'
                binance.step(quote, NOW + offset)
            self.assertEqual(len(binance.snapshot()['positions']), 1)
            self.assertEqual(binance.snapshot()['rules']['exchange'], 'Binance')
            self.assertEqual(okx.snapshot()['cash'], 10000)
            self.assertEqual(okx.snapshot()['positions'], [])
            with self.assertRaisesRegex(ValueError, '账本不一致'):
                PaperAccount(Path(directory) / 'okx.db', exchange='Binance').snapshot()

    def test_quantity_and_min_notional_filters_prevent_invalid_simulated_fills(self):
        with tempfile.TemporaryDirectory() as directory:
            account = PaperAccount(Path(directory) / 'binance.db', exchange='Binance')
            account.set_enabled(True)
            for offset in (0, 10000):
                quote = market(NOW + offset, max_size=1, min_notional=500)
                quote['source'] = quote['items'][0]['source'] = 'Binance'
                account.step(quote, NOW + offset)
            self.assertEqual(account.snapshot()['positions'], [])
