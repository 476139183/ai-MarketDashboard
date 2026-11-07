"""Binance USD-M USDT perpetual public data. Never falls back to spot or another exchange."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from contract_radar import ContractRadar, CRYPTO_WATCHLIST, number


def fetch_public(path, params):
    request = Request('https://fapi.binance.com/fapi/v1/' + path + '?' + urlencode(params),
                      headers={'User-Agent': 'MarketStudio/1.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=10) as response:
        data = json.load(response)
    if isinstance(data, dict) and data.get('code', 0) != 0:
        raise ValueError('Binance: ' + str(data.get('msg', '无效响应')))
    if not isinstance(data, (list, dict)):
        raise ValueError('Binance: 无效响应')
    return data


class BinanceFeed:
    def __init__(self, fetch=None):
        self.fetch = fetch or fetch_public
        self.instruments = None
        self.instruments_at = 0
        self.interests = {}
        self.interest_lock = threading.Lock()
        self.interest_running = False

    def metadata(self):
        if self.instruments is None or time.monotonic() - self.instruments_at >= 3600:
            data = self.fetch('exchangeInfo', {})
            self.instruments = {row['symbol']: row for row in data['symbols']
                if row.get('status') == 'TRADING' and row.get('contractType') == 'PERPETUAL'
                and row.get('quoteAsset') == 'USDT' and row.get('marginAsset') == 'USDT'
                and row.get('underlyingType') == 'COIN'}
            self.instruments_at = time.monotonic()
        return self.instruments

    def optional(self, path, params):
        try:
            return self.fetch(path, params)
        except (OSError, ValueError):
            return []

    def refresh_interest(self, symbols):
        with self.interest_lock:
            needed = [symbol for symbol in symbols if symbol not in self.interests
                      or time.monotonic() - self.interests[symbol][0] >= 60]
            if self.interest_running or not needed:
                return
            self.interest_running = True

        def job():
            def one(symbol):
                result = self.optional('openInterest', {'symbol': symbol})
                with self.interest_lock:
                    self.interests[symbol] = (time.monotonic(), result.get('openInterest') if isinstance(result, dict) else None)
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(one, needed))
            finally:
                with self.interest_lock:
                    self.interest_running = False
        threading.Thread(target=job, daemon=True).start()

    def __call__(self, path, params):
        if path == 'candles':
            now = time.time() * 1000
            rows = self.fetch('klines', {'symbol': params['symbol'],
                                        'interval': params.get('granularity','4H').lower(),
                                        'limit': params.get('limit','120')})
            return [row[:6] + [row[7]] for row in rows if len(row) >= 8 and (row[6] < now or params.get('include_open'))]
        if path != 'tickers':
            raise ValueError('不支持的公开行情接口')
        with ThreadPoolExecutor(max_workers=4) as pool:
            metadata_job = pool.submit(self.metadata)
            quote_job = pool.submit(self.fetch, 'ticker/24hr', {})
            book_job = pool.submit(self.optional, 'ticker/bookTicker', {})
            mark_job = pool.submit(self.optional, 'premiumIndex', {})
            instruments, quotes = metadata_job.result(), quote_job.result()
            books = {row['symbol']: row for row in book_job.result()}
            marks = {row['symbol']: row for row in mark_job.result()}
        wanted = {spec[0] for spec in CRYPTO_WATCHLIST}
        selected = [row for row in quotes if row.get('symbol') in instruments and row['symbol'] in wanted]
        self.refresh_interest([row['symbol'] for row in selected])
        result = []
        for row in selected:
            symbol = row['symbol']
            filters = {f['filterType']: f for f in instruments[symbol]['filters']}
            lot = filters.get('MARKET_LOT_SIZE', filters.get('LOT_SIZE', {}))
            if number(lot.get('stepSize'), positive=True) is None:
                lot = filters.get('LOT_SIZE', {})
            book, mark = books.get(symbol, {}), marks.get(symbol, {})
            ticker_at, book_at = number(row.get('closeTime')), number(book.get('time'))
            change = number(row.get('priceChangePercent'))
            with self.interest_lock:
                interest = self.interests.get(symbol)
                holding = interest[1] if interest and time.monotonic() - interest[0] < 120 else None
            result.append(dict(symbol=symbol, instId=symbol, lastPr=row.get('lastPrice'),
                ts=min(ticker_at, book_at) if ticker_at is not None and book_at is not None else None,
                bidPr=book.get('bidPrice'), askPr=book.get('askPrice'),
                change24h=None if change is None else change / 100,
                usdtVolume=row.get('quoteVolume'), fundingRate=mark.get('lastFundingRate'),
                markPrice=mark.get('markPrice'), holdingAmount=holding,
                ctVal=1, lotSz=lot.get('stepSize'), minSz=lot.get('minQty'), maxSz=lot.get('maxQty'),
                minNotional=filters.get('MIN_NOTIONAL', {}).get('notional'), quantity_unit='基础币'))
        return result


SERVICE = ContractRadar(BinanceFeed(), watchlist=CRYPTO_WATCHLIST, source='Binance', candle_workers=8)
