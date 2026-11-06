import tempfile
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor

from paper_trading import PaperAccount

STEP = 14400000
NOW = 1800000000000 // STEP * STEP + 60000


def market(now=NOW, direction='bullish', price=100, symbol='BTCUSDT', **overrides):
    row = dict(symbol=symbol, instrument_id=symbol[:-4] + '-USDT-SWAP', name=symbol,
               source='OKX', price=price, bid=price - .01, ask=price + .01, quote_at=now,
               quote_fresh=True, structure_fresh=True, candle_close_at=now // STEP * STEP,
               direction=direction, error=None, contract_value=.01, lot_size=.01, min_size=.01)
    row.update(overrides)
    return {'source': 'OKX', 'error': None, 'items': [row]}


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'paper.sqlite3'
        self.account = PaperAccount(self.path)
        self.account.set_enabled(True)

    def open_position(self, direction='bullish'):
        self.account.step(market(direction=direction), NOW)
        self.account.step(market(NOW + 10000, direction), NOW + 10000)

    def test_new_signal_waits_for_later_quote_and_does_not_duplicate(self):
        self.account.step(market(), NOW)
        self.assertEqual(self.account.snapshot()['positions'], [])
        self.account.step(market(), NOW + 1000)
        self.assertEqual(self.account.snapshot()['positions'], [])
        self.account.step(market(NOW + 10000), NOW + 10000)
        self.account.step(market(NOW + 20000), NOW + 20000)
        result = self.account.snapshot()
        self.assertEqual(len(result['positions']), 1)
        self.assertGreater(result['positions'][0]['entry_price'], 100)
        self.assertLess(result['cash'], 9000)

    def test_stop_uses_current_gap_price_and_cannot_reopen_same_signal(self):
        self.open_position()
        self.account.step(market(NOW + 20000, price=90), NOW + 20000)
        result = self.account.snapshot()
        self.assertEqual(len(result['positions']), 0)
        trade = result['trades'][0]
        self.assertEqual(trade['exit_reason'], 'stop_loss')
        self.assertLess(trade['exit_price'], 90)
        self.assertLess(trade['net_pnl'], -100)
        self.assertAlmostEqual(result['cash'], 10000 + trade['net_pnl'], places=6)
        self.account.step(market(NOW + 30000), NOW + 30000)
        self.assertEqual(self.account.snapshot()['positions'], [])

    def test_short_profit_and_fees(self):
        self.open_position('bearish')
        self.account.step(market(NOW + 20000, 'bearish', 90), NOW + 20000)
        result = self.account.snapshot()
        self.assertGreater(result['trades'][0]['net_pnl'], 0)
        self.assertEqual(result['trades'][0]['exit_reason'], 'take_profit')
        self.assertGreater(result['trades'][0]['fees'], 0)
        self.assertAlmostEqual(result['cash'], 10000 + result['trades'][0]['net_pnl'], places=6)

    def test_stale_missing_foreign_quotes_do_not_open_or_close(self):
        self.open_position()
        for data in (market(NOW, price=80), market(NOW + 20000, price=80, bid=None),
                     dict(market(NOW + 20000, price=80), source='Bitget'),
                     dict(market(NOW + 20000, price=80), error='network failed')):
            self.account.step(data, NOW + 40000)
        self.assertEqual(len(self.account.snapshot()['positions']), 1)

    def test_valid_observe_closes_but_missing_structure_does_not(self):
        self.open_position()
        self.account.step(market(NOW + 20000, 'observe', structure_fresh=False), NOW + 20000)
        self.assertEqual(len(self.account.snapshot()['positions']), 1)
        self.account.step(market(NOW + 30000, 'observe'), NOW + 30000)
        self.assertEqual(self.account.snapshot()['trades'][0]['exit_reason'], 'signal_invalid')

    def test_pause_blocks_entries_but_keeps_risk_exits(self):
        self.open_position()
        self.account.set_enabled(False)
        self.account.step(market(NOW + 20000, price=110), NOW + 20000)
        self.assertEqual(self.account.snapshot()['positions'], [])
        self.account.step(market(NOW + 30000, symbol='ETHUSDT'), NOW + 30000)
        self.assertEqual(self.account.snapshot()['pending_signals'], 0)

    def test_restart_preserves_positions_cash_and_signal_deduplication(self):
        self.open_position()
        second = PaperAccount(self.path)
        second.step(market(NOW + 20000), NOW + 20000)
        self.assertEqual(len(second.snapshot()['positions']), 1)
        self.assertEqual(second.snapshot()['cash'], self.account.snapshot()['cash'])

    def test_position_cap_and_no_same_quote_signal_flip(self):
        first = market()
        first['items'] *= 1
        first['items'] = [market(symbol=f'COIN{i}USDT')['items'][0] for i in range(5)]
        self.account.step(first, NOW)
        for row in first['items']:
            row['quote_at'] = NOW + 10000
        self.account.step(first, NOW + 10000)
        self.assertEqual(len(self.account.snapshot()['positions']), 3)

    def test_concurrent_updates_preserve_single_fill_and_accounting(self):
        self.account.step(market(), NOW)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.account.step(market(NOW + 10000), NOW + 10000), range(12)))
        result = self.account.snapshot()
        self.assertEqual(len(result['positions']), 1)
        position = result['positions'][0]
        self.assertAlmostEqual(result['cash'] + position['notional'] + position['entry_fee'], 10000)

    def test_reversal_requires_new_quote_and_cannot_flip_on_exit_tick(self):
        self.open_position()
        self.account.step(market(NOW + 10000, 'bearish'), NOW + 20000)
        self.assertEqual(len(self.account.snapshot()['positions']), 1)
        self.account.step(market(NOW + 30000, 'bearish'), NOW + 30000)
        result = self.account.snapshot()
        self.assertEqual(len(result['positions']), 0)
        self.assertEqual(result['trades'][0]['exit_reason'], 'signal_invalid')

    def test_insufficient_cash_and_missing_contract_sizes_do_not_fill(self):
        with self.account.transaction() as state:
            state['cash'] = 900
        self.account.step(market(), NOW)
        self.account.step(market(NOW + 10000), NOW + 10000)
        self.assertEqual(self.account.snapshot()['positions'], [])
        with self.account.transaction() as state:
            state['cash'] = 10000
        self.account.step(market(NOW + 20000, contract_value=None), NOW + 20000)
        self.assertEqual(self.account.snapshot()['positions'], [])


if __name__ == '__main__':
    unittest.main()
