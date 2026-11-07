from pathlib import Path
import tempfile
import unittest
from paper_trading import PaperAccount
from test_paper_trading import market, NOW


class PaperMarketTests(unittest.TestCase):
    def test_stock_and_crypto_entries_are_separate_and_exit_rules_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            crypto=PaperAccount(Path(directory)/'crypto.db',market='crypto')
            stocks=PaperAccount(Path(directory)/'stock.db',market='stock')
            for account in (crypto,stocks):
                account.set_enabled(True)
                for offset in (0,10000):
                    data=market(NOW+offset)
                    data['items'].append(market(NOW+offset,symbol='MUUSDT')['items'][0])
                    account.step(data,NOW+offset)
            self.assertEqual([p['symbol'] for p in crypto.snapshot()['positions']],['BTCUSDT'])
            self.assertEqual([p['symbol'] for p in stocks.snapshot()['positions']],['MUUSDT'])
            self.assertEqual(stocks.snapshot()['rules']['market'],'stock')

    def test_new_market_scope_keeps_existing_balance_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.db'
            old=PaperAccount(path)
            with old.transaction() as state:
                state['cash']=8765
            migrated=PaperAccount(path,market='crypto')
            self.assertEqual(migrated.snapshot()['cash'],8765)
            self.assertEqual(migrated.snapshot()['rules']['market'],'crypto')


if __name__=='__main__':
    unittest.main()
