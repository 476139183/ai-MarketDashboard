"""Bitget USDT perpetual radar. Closed 4h structure; no account or trading API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import math
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from radar_periods import period_minutes, period_label, native_bar, aggregate_rows

STEP = 4 * 60 * 60 * 1000
WATCHLIST = [
    ('BTCUSDT', '比特币', 'crypto'), ('ETHUSDT', '以太坊', 'crypto'),
    ('SOLUSDT', 'Solana', 'crypto'), ('XRPUSDT', '瑞波币', 'crypto'),
    ('DOGEUSDT', '狗狗币', 'crypto'), ('BNBUSDT', 'BNB', 'crypto'),
    ('ADAUSDT', 'Cardano', 'crypto'), ('AVAXUSDT', 'Avalanche', 'crypto'),
    ('LINKUSDT', 'Chainlink', 'crypto'), ('DOTUSDT', 'Polkadot', 'crypto'),
    ('LTCUSDT', '莱特币', 'crypto'), ('BCHUSDT', '比特币现金', 'crypto'),
    ('UNIUSDT', 'Uniswap', 'crypto'), ('ATOMUSDT', 'Cosmos', 'crypto'),
    ('NEARUSDT', 'NEAR', 'crypto'), ('APTUSDT', 'Aptos', 'crypto'),
    ('ARBUSDT', 'Arbitrum', 'crypto'), ('OPUSDT', 'Optimism', 'crypto'),
    ('SUIUSDT', 'Sui', 'crypto'), ('FILUSDT', 'Filecoin', 'crypto'),
    ('AAVEUSDT', 'Aave', 'crypto'), ('TRXUSDT', 'TRON', 'crypto'),
    ('ETCUSDT', 'Ethereum Classic', 'crypto'), ('WIFUSDT', 'dogwifhat', 'crypto'),
    ('MUUSDT', '美光科技', 'stock'), ('NVDAUSDT', '英伟达', 'stock'),
    ('TSLAUSDT', '特斯拉', 'stock'), ('SNDKUSDT', '闪迪', 'stock'),
    ('AMDUSDT', 'AMD', 'stock'), ('AAPLUSDT', '苹果', 'stock'),
]
CRYPTO_WATCHLIST = [spec for spec in WATCHLIST if spec[2] == 'crypto']
CATEGORIES = {
    'main': ('主流', 'BTC ETH BNB XRP LTC BCH'),
    'layer1': ('公链', 'SOL ADA AVAX DOT ATOM NEAR APT SUI TRX ETC'),
    'layer2': ('Layer 2', 'ARB OP'), 'defi': ('DeFi', 'LINK UNI AAVE'),
    'meme': ('Meme', 'DOGE WIF'), 'infrastructure': ('基础设施', 'FIL'),
}
CATEGORY_BY_SYMBOL = {coin + 'USDT': (key, label)
                      for key, (label, coins) in CATEGORIES.items() for coin in coins.split()}


def number(value, *, positive=False):
    try:
        result = float(value)
        return result if math.isfinite(result) and (not positive or result > 0) else None
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_json(path, params):
    url = 'https://api.bitget.com/api/v2/mix/market/' + path + '?' + urlencode({key:value for key,value in params.items() if key != 'include_open'})
    request = Request(url, headers={'User-Agent': 'MarketStudio/1.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=8) as response:
        payload = json.load(response)
    if payload.get('code') != '00000' or not isinstance(payload.get('data'), list):
        raise ValueError('Bitget: ' + str(payload.get('msg', '无效响应')))
    return payload['data']


def ema(values, period):
    result = [values[0]]
    weight = 2 / (period + 1)
    for value in values[1:]:
        result.append(value * weight + result[-1] * (1 - weight))
    return result


def closed_candles(raw, now, minutes=240):
    step = period_minutes(minutes) * 60000
    label = period_label(minutes)
    boundary = now // step * step
    unique = {}
    for row in raw:
        if len(row)>7 and row[7] is False:continue
        timestamp = number(row[0])
        if timestamp is None:
            raise ValueError('K 线时间无效')
        if timestamp >= boundary:
            continue
        values = [number(value, positive=True) for value in row[1:5]]
        volume = number(row[5])
        if (len(values) != 4 or any(value is None for value in values)
                or volume is None or volume < 0 or timestamp % step):
            raise ValueError('K 线字段无效')
        opening, high, low, close = values
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError('K 线高低价无效')
        candle = {'time': int(timestamp), 'open': opening, 'high': high,
                  'low': low, 'close': close, 'volume': volume}
        if timestamp in unique and unique[timestamp] != candle:
            raise ValueError('K 线重复且数值冲突')
        unique[timestamp] = candle
    rows = sorted(unique.values(), key=lambda row: row['time'])
    if len(rows) < 60:
        raise ValueError(f'完整 {label} K 线不足 60 根')
    if rows[-1]['time'] != boundary - step:
        raise ValueError(f'尚未取得最近已收盘的 {label} K 线')
    if any(right['time'] - left['time'] != step for left, right in zip(rows[-60:], rows[-59:])):
        raise ValueError('最近 60 根 K 线存在缺口')
    return rows


def chart_candles(raw, now, minutes):
    step = minutes * 60000
    rows = {}
    for row in raw:
        stamp=number(row[0])
        values=[number(v,positive=True) for v in row[1:5]]
        volume=number(row[5])
        if stamp is None or stamp>now or stamp%step or len(values)!=4 or None in values or volume is None or volume<0:
            continue
        opening,high,low,close=values
        if not low<=min(opening,close)<=max(opening,close)<=high:continue
        if len(row)>7 and row[7] is False and stamp+step<=now:continue
        candle=dict(time=int(stamp),open=opening,high=high,low=low,close=close,volume=volume,complete=stamp+step<=now)
        if stamp in rows and rows[stamp]!=candle:raise ValueError('图表 K 线重复且冲突')
        rows[stamp]=candle
    return [rows[key] for key in sorted(rows)][-120:]


def analyze_contract(symbol, name, group, ticker, raw_candles, now, minutes=240):
    step = period_minutes(minutes) * 60000
    label = period_label(minutes)
    price = number(ticker.get('lastPr'), positive=True)
    timestamp = number(ticker.get('ts'), positive=True)
    fresh = price is not None and timestamp is not None and -5000 <= now - timestamp <= 30000
    funding, change = number(ticker.get('fundingRate')), number(ticker.get('change24h'))
    holding, mark = number(ticker.get('holdingAmount')), number(ticker.get('markPrice'), positive=True)
    item = dict(symbol=symbol, name=name, group=group, source='Bitget', price=price,
                period_minutes=minutes, period_label=label,
                category=CATEGORY_BY_SYMBOL.get(symbol, ('other', '其他') if group == 'crypto' else ('stock', '股票合约'))[0],
                category_label=CATEGORY_BY_SYMBOL.get(symbol, ('other', '其他') if group == 'crypto' else ('stock', '股票合约'))[1],
                instrument_id=ticker.get('instId', symbol),
                bid=number(ticker.get('bidPr'), positive=True), ask=number(ticker.get('askPr'), positive=True),
                contract_value=number(ticker.get('ctVal'), positive=True),
                lot_size=number(ticker.get('lotSz'), positive=True), min_size=number(ticker.get('minSz'), positive=True),
                max_size=number(ticker.get('maxSz'), positive=True),
                min_notional=number(ticker.get('minNotional'), positive=True),
                quantity_unit=ticker.get('quantity_unit', '张'),
                volume_estimated=bool(ticker.get('volume_estimated', False)),
                quote_at=timestamp, quote_fresh=fresh, structure_fresh=False,
                change_pct=None if change is None else change * 100,
                funding_pct=None if funding is None else funding * 100,
                open_interest_usdt=holding * mark if holding is not None and holding >= 0 and mark else None,
                volume_usdt=number(ticker.get('usdtVolume')), direction='observe',
                label='待确认', reasons=[], invalidation='等待完整行情与结构',
                ema20=None, ema50=None, support=None, resistance=None,
                candle_close_at=None, candles=[], chart_candles=[], volume_unit='基础币', error=None)
    if not ticker:
        item['error'] = '未取得该同名合约行情，可能未上线或暂不可用'
        item['reasons'] = [item['error']]
        return item
    try:
        item['chart_candles']=chart_candles(raw_candles,now,minutes)
        rows = closed_candles(raw_candles, now, minutes)
        closes = [row['close'] for row in rows]
        fast, slow = ema(closes, 20), ema(closes, 50)
        e20, e50 = fast[-1], slow[-1]
        item.update(structure_fresh=True, ema20=e20, ema50=e50,
                    candle_close_at=rows[-1]['time'] + step, candles=rows[-80:])
        slope = e20 - fast[-7]
        bullish = closes[-1] > e20 > e50 and slope > 0
        bearish = closes[-1] < e20 < e50 and slope < 0
        item['reasons'] = [
            f"{label}收盘 {closes[-1]:,.6g}，位于 EMA20 {'上方' if closes[-1] > e20 else '下方' if closes[-1] < e20 else '附近'}",
            f"EMA20 {'高于' if e20 > e50 else '低于' if e20 < e50 else '等于'} EMA50",
            f"EMA20 近 6 根 {'向上' if slope > 0 else '向下' if slope < 0 else '走平'}",
        ]
        if fresh:
            item['direction'] = 'bullish' if bullish else 'bearish' if bearish else 'observe'
            item['label'] = '看涨' if bullish else '看跌' if bearish else '观望'
            item['invalidation'] = (
                f'后续 {label}收盘跌回 EMA20，或均线排列 / 斜率不再满足时撤销看涨倾向'
                if bullish else f'后续 {label}收盘站上 EMA20，或均线排列 / 斜率不再满足时撤销看跌倾向'
                if bearish else f'价格、均线排列及斜率尚未形成同向条件，等待后续 {label}收盘确认')
        if price is not None:
            low = min(row['low'] for row in rows[-20:])
            high = max(row['high'] for row in rows[-20:])
            item['support'] = low if low < price else None
            item['resistance'] = high if high > price else None
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        item['error'] = str(exc)
        item['reasons'] = [str(exc)]
    if not fresh:
        item['reasons'].insert(0, '报价缺失、超过 30 秒或时间异常，方向暂停')
    return item


class ContractRadar:
    def __init__(self, fetch=None, watchlist=None, source='Bitget', candle_workers=4, minutes=240):
        self.fetch = fetch or fetch_json
        self.source = source
        self.candle_workers = candle_workers
        self.minutes = period_minutes(minutes)
        self.step = self.minutes * 60000
        self.watchlist = WATCHLIST if watchlist is None else watchlist
        self.lock = threading.Lock()
        self.running = False
        self.attempt_at = None
        self.updated_at = None
        self.error = None
        self.items = []
        self.candle_cache = {}

    def _candles(self, symbol, now):
        cached = self.candle_cache.get(symbol)
        if cached and 0 <= now - cached[0] < 10000 and now // self.step == cached[0] // self.step:
            return cached[1]
        source_minutes = 5 if self.minutes == 10 else self.minutes
        rows = self.fetch('candles', {'productType': 'USDT-FUTURES', 'symbol': symbol,
                                     'granularity': native_bar(source_minutes),
                                     'limit': '240' if self.minutes == 10 else '120', 'kLineType': 'market', 'include_open': True})
        if self.minutes == 10:
            rows = aggregate_rows([r for r in rows if len(r)<=7 or r[7] or float(r[0])+300000>now],source_minutes=5,target_minutes=10,now=now,include_open=True)
        self.candle_cache[symbol] = (now, rows)
        return rows

    def _refresh(self, now=None):
        started = time.monotonic()
        explicit_now = now is not None
        now = int(time.time() * 1000) if now is None else now
        try:
            quotes = {row['symbol']: row for row in self.fetch('tickers', {'productType': 'USDT-FUTURES'})}

            def one(spec):
                symbol, name, group = spec
                quote = quotes.get(symbol, {})
                try:
                    rows = self._candles(symbol, now) if quote else []
                    item = analyze_contract(symbol, name, group, quote, rows, now, self.minutes)
                except Exception as exc:  # Isolate upstream failure to this contract.
                    item = analyze_contract(symbol, name, group, quote, [], now, self.minutes)
                    item.update(error=str(exc), reasons=['K 线读取失败：' + str(exc)])
                item['source'] = self.source
                return item

            with ThreadPoolExecutor(max_workers=self.candle_workers) as pool:
                items = list(pool.map(one, self.watchlist))
            # A large cold scan must not publish quotes obtained before slow candle requests.
            if not explicit_now and time.monotonic() - started >= 15:
                quotes = {row['symbol']: row for row in self.fetch('tickers', {'productType': 'USDT-FUTURES'})}
                now = int(time.time() * 1000)
                for index, (symbol, name, group) in enumerate(self.watchlist):
                    rows = self.candle_cache.get(symbol, (None, []))[1]
                    item = analyze_contract(symbol, name, group, quotes.get(symbol, {}), rows, now, self.minutes)
                    item['source'] = self.source
                    items[index] = item
            with self.lock:
                self.items, self.updated_at, self.error = items, now, None
        except Exception as exc:  # Preserve last snapshot with an explicit error.
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.attempt_at, self.running = now, False

    def snapshot(self, now=None):
        now = int(time.time() * 1000) if now is None else now
        with self.lock:
            items = deepcopy(self.items)
            running, updated, error = self.running, self.updated_at, self.error
        for row in items:
            timestamp = row['quote_at']
            fresh = row['price'] is not None and timestamp is not None and -5000 <= now - timestamp <= 30000
            row['quote_fresh'] = fresh
            structure = row['candle_close_at'] == now // self.step * self.step
            row['structure_fresh'] = row['structure_fresh'] and structure
            if not fresh or not row['structure_fresh'] or error:
                row.update(direction='observe', label='待确认', invalidation=f'等待新鲜报价与完整 {period_label(self.minutes)}结构')
                if not fresh:
                    row['reasons'] = ['报价已过期或缺失，方向暂停'] + row['reasons']
                if not structure and row['candle_close_at'] is not None:
                    row['reasons'] = [f'等待最近 {period_label(self.minutes)}收盘结构'] + row['reasons']
        return dict(status='running' if running else 'idle', source=self.source, product='USDT-FUTURES',
                    period_minutes=self.minutes, period_label=period_label(self.minutes),
                    generated_at=updated, server_time=now, error=error, items=items,
                    total=len(self.watchlist), refresh_seconds=10,
                    counts={key: sum(row['direction'] == key for row in items)
                            for key in ('bullish', 'bearish', 'observe')})

    def payload(self):
        now = int(time.time() * 1000)
        with self.lock:
            if not self.running and (self.attempt_at is None or now - self.attempt_at >= 10000):
                self.running = True
                threading.Thread(target=self._refresh, daemon=True).start()
        return self.snapshot(now)


SERVICE = ContractRadar()
