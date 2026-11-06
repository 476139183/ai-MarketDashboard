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

STEP = 4 * 60 * 60 * 1000
WATCHLIST = [
    ('BTCUSDT', '比特币', 'crypto'), ('ETHUSDT', '以太坊', 'crypto'),
    ('SOLUSDT', 'Solana', 'crypto'), ('XRPUSDT', '瑞波币', 'crypto'),
    ('DOGEUSDT', '狗狗币', 'crypto'), ('BNBUSDT', 'BNB', 'crypto'),
    ('MUUSDT', '美光科技', 'stock'), ('NVDAUSDT', '英伟达', 'stock'),
    ('TSLAUSDT', '特斯拉', 'stock'), ('SNDKUSDT', '闪迪', 'stock'),
    ('AMDUSDT', 'AMD', 'stock'), ('AAPLUSDT', '苹果', 'stock'),
]


def number(value, *, positive=False):
    try:
        result = float(value)
        return result if math.isfinite(result) and (not positive or result > 0) else None
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_json(path, params):
    url = 'https://api.bitget.com/api/v2/mix/market/' + path + '?' + urlencode(params)
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


def closed_candles(raw, now):
    boundary = now // STEP * STEP
    unique = {}
    for row in raw:
        timestamp = number(row[0])
        if timestamp is None:
            raise ValueError('K 线时间无效')
        if timestamp >= boundary:
            continue
        values = [number(value, positive=True) for value in row[1:5]]
        volume = number(row[5])
        if (len(values) != 4 or any(value is None for value in values)
                or volume is None or volume < 0 or timestamp % STEP):
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
        raise ValueError('完整 4 小时 K 线不足 60 根')
    if rows[-1]['time'] != boundary - STEP:
        raise ValueError('尚未取得最近已收盘的 4 小时 K 线')
    if any(right['time'] - left['time'] != STEP for left, right in zip(rows[-60:], rows[-59:])):
        raise ValueError('最近 60 根 K 线存在缺口')
    return rows


def analyze_contract(symbol, name, group, ticker, raw_candles, now):
    price = number(ticker.get('lastPr'), positive=True)
    timestamp = number(ticker.get('ts'), positive=True)
    fresh = price is not None and timestamp is not None and -5000 <= now - timestamp <= 30000
    funding, change = number(ticker.get('fundingRate')), number(ticker.get('change24h'))
    holding, mark = number(ticker.get('holdingAmount')), number(ticker.get('markPrice'), positive=True)
    item = dict(symbol=symbol, name=name, group=group, source='Bitget', price=price,
                quote_at=timestamp, quote_fresh=fresh, structure_fresh=False,
                change_pct=None if change is None else change * 100,
                funding_pct=None if funding is None else funding * 100,
                open_interest_usdt=holding * mark if holding is not None and holding >= 0 and mark else None,
                volume_usdt=number(ticker.get('usdtVolume')), direction='observe',
                label='待确认', reasons=[], invalidation='等待完整行情与结构',
                ema20=None, ema50=None, support=None, resistance=None,
                candle_close_at=None, candles=[], error=None)
    if not ticker:
        item['error'] = '未取得该同名合约行情，可能未上线或暂不可用'
        item['reasons'] = [item['error']]
        return item
    try:
        rows = closed_candles(raw_candles, now)
        closes = [row['close'] for row in rows]
        fast, slow = ema(closes, 20), ema(closes, 50)
        e20, e50 = fast[-1], slow[-1]
        item.update(structure_fresh=True, ema20=e20, ema50=e50,
                    candle_close_at=rows[-1]['time'] + STEP, candles=rows[-80:])
        slope = e20 - fast[-7]
        bullish = closes[-1] > e20 > e50 and slope > 0
        bearish = closes[-1] < e20 < e50 and slope < 0
        item['reasons'] = [
            f"4 小时收盘 {closes[-1]:,.6g}，位于 EMA20 {'上方' if closes[-1] > e20 else '下方' if closes[-1] < e20 else '附近'}",
            f"EMA20 {'高于' if e20 > e50 else '低于' if e20 < e50 else '等于'} EMA50",
            f"EMA20 近 6 根 {'向上' if slope > 0 else '向下' if slope < 0 else '走平'}",
        ]
        if fresh:
            item['direction'] = 'bullish' if bullish else 'bearish' if bearish else 'observe'
            item['label'] = '看涨' if bullish else '看跌' if bearish else '观望'
            item['invalidation'] = (
                '后续 4 小时收盘跌回 EMA20，或均线排列 / 斜率不再满足时撤销看涨倾向'
                if bullish else '后续 4 小时收盘站上 EMA20，或均线排列 / 斜率不再满足时撤销看跌倾向'
                if bearish else '价格、均线排列及斜率尚未形成同向条件，等待后续 4 小时收盘确认')
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
    def __init__(self, fetch=None, watchlist=None):
        self.fetch = fetch or fetch_json
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
        if cached and 0 <= now - cached[0] < 60000 and now // STEP == cached[0] // STEP:
            return cached[1]
        rows = self.fetch('candles', {'productType': 'USDT-FUTURES', 'symbol': symbol,
                                     'granularity': '4H', 'limit': '120', 'kLineType': 'market'})
        self.candle_cache[symbol] = (now, rows)
        return rows

    def _refresh(self, now=None):
        now = int(time.time() * 1000) if now is None else now
        try:
            quotes = {row['symbol']: row for row in self.fetch('tickers', {'productType': 'USDT-FUTURES'})}

            def one(spec):
                symbol, name, group = spec
                quote = quotes.get(symbol, {})
                try:
                    rows = self._candles(symbol, now) if quote else []
                    return analyze_contract(symbol, name, group, quote, rows, now)
                except Exception as exc:  # Isolate upstream failure to this contract.
                    item = analyze_contract(symbol, name, group, quote, [], now)
                    item.update(error=str(exc), reasons=['K 线读取失败：' + str(exc)])
                    return item

            with ThreadPoolExecutor(max_workers=4) as pool:
                items = list(pool.map(one, self.watchlist))
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
            structure = row['candle_close_at'] == now // STEP * STEP
            row['structure_fresh'] = row['structure_fresh'] and structure
            if not fresh or not row['structure_fresh'] or error:
                row.update(direction='observe', label='待确认', invalidation='等待新鲜报价与完整 4 小时结构')
                if not fresh:
                    row['reasons'] = ['报价已过期或缺失，方向暂停'] + row['reasons']
                if not structure and row['candle_close_at'] is not None:
                    row['reasons'] = ['等待最近 4 小时收盘结构'] + row['reasons']
        return dict(status='running' if running else 'idle', source='Bitget', product='USDT-FUTURES',
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
