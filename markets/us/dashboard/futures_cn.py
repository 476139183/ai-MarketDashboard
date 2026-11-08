"""Domestic commodity futures observations, separate from trading strategies/accounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BJ = ZoneInfo('Asia/Shanghai')
SOURCE = '新浪财经 · 国内商品期货'
# Price units are contract quotation units, not currency per lot.
PRODUCTS = {
    'RB': ('螺纹钢', 'SHFE', '黑色', '元/吨'),
    'AU': ('黄金', 'SHFE', '贵金属', '元/克'),
    'AG': ('白银', 'SHFE', '贵金属', '元/千克'),
    'CU': ('沪铜', 'SHFE', '有色', '元/吨'),
    'AL': ('沪铝', 'SHFE', '有色', '元/吨'),
    'SC': ('原油', 'INE', '能源', '元/桶'),
    'FU': ('燃料油', 'SHFE', '能源', '元/吨'),
    'M': ('豆粕', 'DCE', '农产品', '元/吨'),
    'Y': ('豆油', 'DCE', '农产品', '元/吨'),
    'P': ('棕榈油', 'DCE', '农产品', '元/吨'),
    'C': ('玉米', 'DCE', '农产品', '元/吨'),
    'I': ('铁矿石', 'DCE', '黑色', '元/吨'),
}
SYMBOLS = [root+'0' for root in PRODUCTS]
PERIODS = ('day', '5', '15', '30', '60')


def validate_symbol(symbol):
    symbol = str(symbol).strip().upper()
    match = re.fullmatch(r'([A-Z]{1,2})(0|[0-9]{3,4})', symbol)
    if not match or match[1] not in PRODUCTS:
        raise ValueError('请选择已支持商品，或输入对应月份合约，如 RB2701')
    if match[2] != '0' and not 1 <= int(match[2][-2:]) <= 12:
        raise ValueError('合约月份应为01至12')
    return symbol


def validate_period(period):
    period = str(period)
    if period not in PERIODS:
        raise ValueError('期货周期仅支持日线及5/15/30/60分钟')
    return period


def identity(symbol):
    symbol = validate_symbol(symbol)
    root = re.match(r'[A-Z]+', symbol)[0]
    name, exchange, group, unit = PRODUCTS[root]
    continuous = symbol == root+'0'
    return dict(symbol=symbol, product=root, name=name, exchange=exchange,
                group=group, price_unit=unit, volume_unit='手', currency='CNY',
                continuous=continuous, contract_label='主力连续' if continuous else '月份合约', source=SOURCE)


def number(value):
    if value is None or str(value).strip() in ('', '--', 'null'):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('行情数值非有限数')
    return result


def parse_quotes(text, symbols, now):
    found = dict(re.findall(r'var\s+hq_str_nf_([A-Z0-9]+)\s*=\s*"([^"\r\n]*)"\s*;', text))
    result = []
    for symbol in symbols:
        row = dict(identity(symbol), price=None, change_pct=None, quote_at=None,
                   quote_fresh=False, volume=None, open_interest=None, error=None)
        try:
            fields = found.get(symbol, '').split(',')
            if len(fields) < 18:
                raise ValueError('来源未返回该合约报价，可能已到期或尚未上市')
            price, settlement, volume, hold = [number(fields[i]) for i in (8, 10, 14, 13)]
            if price is None or price <= 0 or any(v is not None and v < 0 for v in (volume, hold)):
                raise ValueError('来源报价无效')
            stamp = datetime.strptime(fields[17]+' '+fields[1].zfill(6), '%Y-%m-%d %H%M%S').replace(tzinfo=BJ).timestamp()*1000
            row.update(price=price, previous_settlement=settlement,
                       change_pct=(price/settlement-1)*100 if settlement and settlement > 0 else None,
                       volume=volume, open_interest=hold, quote_at=stamp,
                       quote_fresh=-5000 <= now-stamp <= 120000,
                       source_name=fields[0])
        except (ValueError, TypeError, IndexError) as exc:
            row['error'] = str(exc)
        result.append(row)
    return result


def parse_bars(text, period, now):
    period = validate_period(period)
    # The source prepends a JavaScript comment. Extract JSON, never execute JavaScript.
    match = re.search(r'=\s*\((\s*\[.*\]\s*)\)\s*;?\s*$', text, re.S)
    if not match:
        raise ValueError('期货K线响应格式无效')
    payload = json.loads(match[1])
    if not isinstance(payload, list) or not payload:
        raise ValueError('来源未返回期货K线')
    today = datetime.fromtimestamp(now/1000, BJ).date()
    result = {}
    # Only the current 300 source bars are consumed. An ancient corrupt record
    # outside this window must not block the current chart or its indicators.
    for raw in payload[-300:]:
        dt = datetime.strptime(raw['d'], '%Y-%m-%d' if period == 'day' else '%Y-%m-%d %H:%M:%S').replace(tzinfo=BJ)
        stamp = int(dt.timestamp()*1000)
        opening, high, low, close = [number(raw[k]) for k in ('o', 'h', 'l', 'c')]
        volume, hold = number(raw.get('v')), number(raw.get('p'))
        if any(v is None or v <= 0 for v in (opening, high, low, close)):
            raise ValueError('期货OHLC价格无效')
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError('期货高低价无效')
        if any(v is not None and v < 0 for v in (volume, hold)):
            raise ValueError('期货成交量或持仓量无效')
        row = dict(time=stamp, open=opening, high=high, low=low, close=close,
                   volume=volume, open_interest=hold,
                   complete=dt.date() < today if period == 'day' else stamp+60000 <= now)
        if stamp in result and result[stamp] != row:
            raise ValueError('期货K线时间重复且内容冲突')
        result[stamp] = row
    return [result[key] for key in sorted(result)][-300:]


def analyze(bars):
    closed = [row for row in bars if row['complete']]
    out = dict(trend='insufficient', label='完整K线不足', count=len(closed), asof=None,
               sma20=None, sma60=None, atr14=None, support=None, resistance=None,
               volume_ratio=None, oi_change=None, evidence=[])
    if not closed:
        return out
    last = closed[-1]
    out.update(asof=last['time'], support=min(b['low'] for b in closed[-20:]),
               resistance=max(b['high'] for b in closed[-20:]))
    if len(closed) >= 2 and last['open_interest'] is not None and closed[-2]['open_interest'] is not None:
        out['oi_change'] = last['open_interest']-closed[-2]['open_interest']
        out['evidence'].append(f"较上一完整K线持仓变化 {out['oi_change']:+g} 手；仅凭持仓增减不能区分多空主动性。")
    if len(closed) >= 15:
        ranges = [max(b['high']-b['low'], abs(b['high']-p['close']), abs(b['low']-p['close'])) for p,b in zip(closed[-15:-1],closed[-14:])]
        out['atr14'] = sum(ranges)/14
    if len(closed) >= 21:
        prior = [b['volume'] for b in closed[-21:-1]]
        if last['volume'] is not None and all(v is not None for v in prior) and sum(prior) > 0:
            out['volume_ratio'] = last['volume']/(sum(prior)/20)
    if len(closed) < 60:
        out['evidence'].append('至少需要60根完整K线才能比较SMA20与SMA60。')
        return out
    out['sma20'] = sum(b['close'] for b in closed[-20:])/20
    out['sma60'] = sum(b['close'] for b in closed[-60:])/60
    if last['close'] > out['sma20'] > out['sma60']:
        out.update(trend='strong', label='历史结构偏强')
    elif last['close'] < out['sma20'] < out['sma60']:
        out.update(trend='weak', label='历史结构偏弱')
    else:
        out.update(trend='mixed', label='历史结构交错')
    out['evidence'].append('以最后完整K线收盘价与SMA20、SMA60的相对位置描述历史结构，不代表当前开仓信号。')
    return out


def read_source(url, encoding='utf-8'):
    req = Request(url, headers={'User-Agent':'Mozilla/5.0', 'Referer':'https://finance.sina.com.cn/'})
    with urlopen(req, timeout=8) as response:
        raw = response.read(3_000_001)
    if len(raw) > 3_000_000:
        raise ValueError('期货行情响应过大')
    return raw.decode(encoding)


def fetch_quotes(symbols):
    url = 'https://hq.sinajs.cn/list='+','.join('nf_'+validate_symbol(s) for s in symbols)
    return parse_quotes(read_source(url, 'gb18030'), symbols, time.time()*1000)


def fetch_chart(symbol, period):
    method = 'getDailyKLine' if period == 'day' else 'getFewMinLine'
    params = dict(symbol=symbol)
    if period != 'day':
        params['type'] = period
    url = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.'+method+'?'+urlencode(params)
    bars = parse_bars(read_source(url), period, time.time()*1000)
    result = dict(identity(symbol), candles=bars, analysis=analyze(bars),
                  period=period, period_minutes=0 if period == 'day' else int(period),
                  time_convention='trading_date' if period == 'day' else 'period_end', quote=None, quote_error=None)
    try:
        result['quote'] = fetch_quotes([symbol])[0]
    except (OSError, ValueError) as exc:
        result['quote_error'] = str(exc)[:180]
    return result


class FuturesService:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cn-futures')
        self.lock = threading.Lock()
        self.cache = {}

    @staticmethod
    def load(fetch):
        return fetch(), int(time.time()*1000)

    def cached(self, key, fetch, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            # A visitor may abandon a request by switching symbols. Reap every
            # completed job, including keys that visitor never polls again.
            for entry in self.cache.values():
                future = entry['future']
                if future is not None and future.done():
                    try:
                        entry['data'], entry['fetched_at'] = future.result()
                        entry['error'] = None
                    except Exception as exc:
                        entry['error'] = '期货行情读取失败：'+str(exc)[:180]
                    entry['future'] = None
            if key not in self.cache:
                if len(self.cache) >= 64:
                    idle = next((k for k,v in self.cache.items() if v['future'] is None), None)
                    if idle is None:
                        return dict(status='loading', error=None, stale=True, fetched_at=None, source=SOURCE)
                    self.cache.pop(idle)
                self.cache[key] = dict(data={}, future=None, next_at=0, error=None, fetched_at=None)
            entry = self.cache[key]
            if entry['future'] is None and now >= entry['next_at']:
                pending = sum(v['future'] is not None for v in self.cache.values())
                if pending < 4:
                    entry['future'] = self.pool.submit(self.load, fetch)
                    entry['next_at'] = now+30
            loading = entry['future'] is not None or not entry['data'] and not entry['error']
            return dict(entry['data'], status='loading' if loading else 'error' if entry['error'] else 'idle',
                        error=entry['error'], stale=bool(entry['error']) or bool(entry['data']) and (loading or now >= entry['next_at']),
                        fetched_at=entry['fetched_at'], source=SOURCE)

    def snapshot(self):
        return self.cached('quotes', lambda: dict(items=fetch_quotes(SYMBOLS)))

    def chart(self, symbol='RB0', period='day'):
        symbol, period = validate_symbol(symbol), validate_period(period)
        return self.cached(symbol+':'+period, lambda: fetch_chart(symbol, period))


SERVICE = FuturesService()
