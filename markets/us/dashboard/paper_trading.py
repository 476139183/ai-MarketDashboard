"""Local forward paper accounts using isolated OKX/Binance public quotes; no order API."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path
import sqlite3
import threading
import time

from contract_radar import STEP, WATCHLIST, number
STOCK_SYMBOLS = {symbol for symbol, _, group in WATCHLIST if group == 'stock'}

RULES = dict(initial_balance=10000.0, order_budget=1000.0, max_positions=3,
             fee_rate=.0006, slippage_rate=.0005, stop_pct=.02, target_pct=.04,
             leverage=1, strategy='closed-4h-ema-v1', exchange='OKX')


class PaperAccount:
    def __init__(self, path, exchange='OKX', market='all'):
        self.path = Path(path)
        if exchange not in ('OKX', 'Binance'):
            raise ValueError('模拟账户仅支持 OKX / Binance')
        self.exchange = exchange
        if market not in ('all','crypto','stock') or (exchange == 'Binance' and market == 'stock'):
            raise ValueError('美股合约模拟目前由欧易提供；市场仅支持 crypto / stock')
        self.market = market

    def eligible(self, row):
        group = row.get('group') or ('stock' if row.get('symbol') in STOCK_SYMBOLS else 'crypto')
        return self.market == 'all' or group == self.market

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS paper_state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT body FROM paper_state WHERE id=1').fetchone()
            state = json.loads(saved[0]) if saved else dict(enabled=False, cash=RULES['initial_balance'],
                positions=[], trades=[], signals={}, updated_at=None, error=None, exchange=self.exchange)
            if state.get('exchange', 'OKX') != self.exchange:
                raise ValueError('模拟账户的交易所与账本不一致')
            state['exchange'] = self.exchange
            previous_market = state.get('market','all')
            if previous_market not in ('all',self.market):
                raise ValueError('模拟账户的市场类型与账本不一致')
            state['market'] = self.market
            yield state
            encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
            if saved is None or saved[0] != encoded:
                db.execute('INSERT OR REPLACE INTO paper_state VALUES (1, ?)', (encoded,))
            db.commit()
        finally:
            db.close()

    def set_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('enabled 必须为布尔值')
        with self.transaction() as state:
            state['enabled'] = enabled
            if not enabled:
                state['signals'] = {key: value for key, value in state['signals'].items() if value['used']}

    def quote_valid(self, row, now):
        ts = number(row.get('quote_at'))
        bid, ask = number(row.get('bid'), positive=True), number(row.get('ask'), positive=True)
        return (row.get('source') == self.exchange and row.get('quote_fresh') and ts is not None
                and -5000 <= now - ts <= 30000 and bid is not None and ask is not None and ask >= bid)

    @staticmethod
    def structure_valid(row, now):
        return (row.get('structure_fresh') and not row.get('error')
                and row.get('candle_close_at') == now // STEP * STEP)

    def step(self, market, now=None):
        now = int(time.time() * 1000) if now is None else now
        with self.transaction() as state:
            state['updated_at'] = now
            if market.get('source') != self.exchange or market.get('error'):
                state['error'] = market.get('error') or f'模拟交易只接受 {self.exchange} 合约行情'
                return
            state['error'] = None
            rows = {row['symbol']: row for row in market.get('items', [])}
            closed = set()
            for position in list(state['positions']):
                row = rows.get(position['symbol'], {})
                if not self.quote_valid(row, now) or row['quote_at'] <= position['last_quote_at']:
                    continue
                side = 1 if position['side'] == 'long' else -1
                exit_reference = row['bid'] if side == 1 else row['ask']
                position.update(last_price=exit_reference, last_quote_at=row['quote_at'])
                reason = None
                if side * (exit_reference - position['stop_price']) <= 0:
                    reason = 'stop_loss'
                elif side * (exit_reference - position['target_price']) >= 0:
                    reason = 'take_profit'
                elif self.structure_valid(row, now) and row.get('direction') != ('bullish' if side == 1 else 'bearish'):
                    reason = 'signal_invalid'
                if reason:
                    exit_price = exit_reference * (1 - side * RULES['slippage_rate'])
                    gross = side * (exit_price - position['entry_price']) * position['quantity']
                    exit_fee = position['quantity'] * exit_price * RULES['fee_rate']
                    fees = position['entry_fee'] + exit_fee
                    trade = dict(position, exit_price=exit_price, exit_time=now, exit_quote_at=row['quote_at'],
                                 exit_reason=reason, gross_pnl=gross, fees=fees, net_pnl=gross - fees)
                    state['cash'] += position['notional'] + gross - exit_fee
                    state['trades'].append(trade)
                    state['positions'].remove(position)
                    closed.add(position['symbol'])

            for key, signal in list(state['signals'].items()):
                row = rows.get(signal['symbol'], {})
                if not signal['used'] and (not self.quote_valid(row, now) or not self.structure_valid(row, now)
                    or not self.eligible(row)
                    or row.get('direction') != signal['direction'] or row.get('candle_close_at') != signal['candle_close_at']):
                    del state['signals'][key]
            if not state['enabled']:
                return
            for symbol, row in rows.items():
                if (not self.quote_valid(row, now) or not self.structure_valid(row, now)
                        or not self.eligible(row)
                        or row.get('direction') not in ('bullish', 'bearish') or symbol in closed):
                    continue
                key = f"{symbol}:{row['candle_close_at']}:{row['direction']}"
                signal = state['signals'].get(key)
                if signal is None:
                    state['signals'][key] = dict(symbol=symbol, direction=row['direction'],
                        candle_close_at=row['candle_close_at'], observed_at=max(now, row['quote_at']), used=False)
                    continue
                if (signal['used'] or row['quote_at'] <= signal['observed_at']
                        or len(state['positions']) >= RULES['max_positions']
                        or any(position['symbol'] == symbol for position in state['positions'])):
                    continue
                side = 1 if row['direction'] == 'bullish' else -1
                reference = row['ask'] if side == 1 else row['bid']
                entry = reference * (1 + side * RULES['slippage_rate'])
                ct, lot, minimum = [number(row.get(key), positive=True)
                                    for key in ('contract_value', 'lot_size', 'min_size')]
                if ct is None or lot is None or minimum is None:
                    continue
                contracts = (Decimal(str(RULES['order_budget'])) / Decimal(str(entry)) / Decimal(str(ct))
                             / Decimal(str(lot))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(lot))
                if contracts < Decimal(str(minimum)):
                    continue
                quantity = float(contracts * Decimal(str(ct)))
                notional = quantity * entry
                maximum = number(row.get('max_size'), positive=True)
                min_notional = number(row.get('min_notional'), positive=True)
                if (maximum is not None and contracts > Decimal(str(maximum))) or (min_notional and notional < min_notional):
                    continue
                fee = notional * RULES['fee_rate']
                if state['cash'] < notional + fee:
                    continue
                state['positions'].append(dict(symbol=symbol, instrument_id=row['instrument_id'],
                    name=row.get('name', symbol), side='long' if side == 1 else 'short',
                    quantity=quantity, contracts=float(contracts), entry_price=entry,
                    quantity_unit=row.get('quantity_unit', '张'),
                    market='stock' if symbol in STOCK_SYMBOLS else 'crypto', strategy_period_minutes=240,
                    notional=notional, entry_fee=fee, entry_time=now, entry_quote_at=row['quote_at'],
                    stop_price=entry * (1 - side * RULES['stop_pct']),
                    target_price=entry * (1 + side * RULES['target_pct']),
                    signal_key=key, signal_observed_at=signal['observed_at'],
                    last_price=row['bid'] if side == 1 else row['ask'], last_quote_at=row['quote_at']))
                state['cash'] -= notional + fee
                signal['used'] = True

    def snapshot(self, now=None):
        now = int(time.time() * 1000) if now is None else now
        with self.transaction() as state:
            result = dict(state)
        unrealized, collateral = 0, 0
        for position in result['positions']:
            side = 1 if position['side'] == 'long' else -1
            position['unrealized_pnl'] = side * (position['last_price'] - position['entry_price']) * position['quantity']
            position['quote_fresh'] = -5000 <= now - position['last_quote_at'] <= 30000
            unrealized += position['unrealized_pnl']
            collateral += position['notional']
        all_trades = result['trades']
        result.update(rules={**RULES, 'exchange': self.exchange, 'market': self.market}, unrealized_pnl=unrealized,
                      legacy_position_count=sum(not self.eligible(p) for p in result['positions']),
                      realized_pnl=sum(trade['net_pnl'] for trade in all_trades),
                      equity=result['cash'] + collateral + unrealized,
                      valuation_fresh=all(position['quote_fresh'] for position in result['positions']),
                      total_trades=len(all_trades), winning_trades=sum(trade['net_pnl'] > 0 for trade in all_trades),
                      fees=sum(trade['fees'] for trade in all_trades) + sum(p['entry_fee'] for p in result['positions']),
                      pending_signals=sum(not signal['used'] for signal in result['signals'].values()),
                      trades=list(reversed(all_trades[-100:])))
        result.pop('signals')
        return result


class PaperRunner:
    def __init__(self, account, radar):
        self.account, self.radar = account, radar
        self.lock = threading.Lock()
        self.thread = None
        self.stop_event = threading.Event()
        self.error = None

    def start(self):
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                state = self.account.snapshot()
                if state['enabled'] or state['positions']:
                    self.account.step(self.radar.payload())
                self.error = None
            except Exception as exc:
                self.error = str(exc)
            self.stop_event.wait(5)

    def snapshot(self):
        result = self.account.snapshot()
        result['runner_error'] = self.error
        return result


from okx_contracts import SERVICE as OKX_RADAR
ACCOUNT = PaperAccount(Path(__file__).parent / 'cache' / 'paper-trading.sqlite3', market='crypto')
RUNNER = PaperRunner(ACCOUNT, OKX_RADAR)
from binance_contracts import SERVICE as BINANCE_RADAR
BINANCE_ACCOUNT = PaperAccount(Path(__file__).parent / 'cache' / 'paper-trading-binance.sqlite3', exchange='Binance', market='crypto')
BINANCE_RUNNER = PaperRunner(BINANCE_ACCOUNT, BINANCE_RADAR)
STOCK_ACCOUNT = PaperAccount(Path(__file__).parent / 'cache' / 'paper-trading-okx-stock.sqlite3', market='stock')
STOCK_RUNNER = PaperRunner(STOCK_ACCOUNT, OKX_RADAR)
