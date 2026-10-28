"""独立 A 股研究规则；日线回测与盘中信号是不同策略。"""
from datetime import datetime, time, timedelta
import math
import re


def classify(symbol):
    s = symbol.strip().lower().replace('.', '')
    match = re.fullmatch(r'(sh|sz)?(\d{6})', s)
    if not match:
        raise ValueError('请输入沪深 A 股股票代码')
    prefix, code = match.groups()
    if code.startswith(('600', '601', '603', '605')):
        exchange, board = 'sh', '沪市主板'
    elif code.startswith(('000', '001', '002', '003')):
        exchange, board = 'sz', '深市主板'
    elif code.startswith(('300', '301')):
        exchange, board = 'sz', '创业板'
    elif code.startswith(('688', '689')):
        exchange, board = 'sh', '科创板'
    else:
        raise ValueError('未支持的股票代码；不推断基金、北交所或其他产品')
    if prefix and prefix != exchange:
        raise ValueError('代码与交易所前缀不匹配')
    return exchange + code, board


def intraday_signal(bars, now, accept_overnight=False, upper_limit=None, lower_limit=None):
    def wait(reason):
        return {'action': '观望', 'reason': reason}
    if now.weekday() >= 5 or not (time(9, 30) <= now.time() <= time(11, 30) or time(13) <= now.time() < time(14, 57)):
        return wait('非连续竞价时段；交易日仍需由行情源验证')
    if not accept_overnight:
        return wait('A股新买入股票受 T+1 约束；未确认接受隔夜风险')
    if (upper_limit is None or lower_limit is None or not math.isfinite(upper_limit) or
            not math.isfinite(lower_limit) or lower_limit <= 0 or upper_limit <= lower_limit):
        return wait('缺少当日真实涨跌停价，无法确认可执行性')
    try:
        items = sorted((datetime.strptime(b['time'], '%Y-%m-%d %H:%M'), b) for b in bars)
        items = [(t, b) for t, b in items if t.date() == now.date() and t <= now and
                 (time(9, 45) <= t.time() <= time(11, 30) or time(13, 15) <= t.time() <= time(14, 45))]
        morning = [(t, b) for t, b in items if t.time() == time(9, 45)]
        if len(morning) != 1 or len(items) < 2:
            return wait('缺少完整开盘 15 分钟或后续 K 线')
        if items[-1][0].time() <= time(9, 45):
            return wait('需要开盘区间之后的完整 K 线')
        expected = []
        cursor = items[0][0].replace(hour=9, minute=45)
        while cursor <= items[-1][0]:
            if cursor.time() <= time(11, 30) or time(13, 15) <= cursor.time() <= time(14, 45):
                expected.append(cursor)
            cursor += timedelta(minutes=15)
        if [t for t, _ in items] != expected:
            return wait('分钟 K 线不连续，无法计算全天估算均价')
        if now - items[-1][0] > timedelta(minutes=17):
            return wait('分钟数据已过期')
        if any(not math.isfinite(float(b['volume'])) or float(b['volume']) <= 0 for _, b in items):
            return wait('成交量缺失，无法估算均价')
        if any(not all(math.isfinite(float(b[k])) for k in ('low', 'high', 'close')) or
               float(b['low']) <= 0 or not float(b['low']) <= float(b['close']) <= float(b['high']) for _, b in items):
            return wait('行情数据异常')
        latest = float(items[-1][1]['close'])
        orb = max(float(b['high']) for _, b in morning)
        total = sum(float(b['volume']) for _, b in items)
        vwap = sum(((float(b['high']) + float(b['low']) + float(b['close'])) / 3) * float(b['volume']) for _, b in items) / total
        if latest >= upper_limit * .995 or latest <= lower_limit * 1.005:
            return wait('接近涨跌停，成交与退出风险不明')
        if latest > orb and latest > vwap:
            return {'action': '观察做多', 'reason': '已收盘的 15 分钟线突破开盘区间且高于估算均价；须核对流动性及交易日', 'price': latest, 'orb_high': orb, 'estimated_vwap': round(vwap, 3)}
        return wait('未同时突破开盘区间和估算均价')
    except (KeyError, ValueError, TypeError, OverflowError):
        return wait('分钟数据无效')


def backtest(bars, fee=0.0015):
    """收盘后判断均线，次日开盘执行；非盘中策略回测。"""
    cash, shares, pending, position = 1.0, 0.0, None, None
    trades = []
    for i, b in enumerate(bars):
        op, close = float(b['open']), float(b['close'])
        if op <= 0 or close <= 0 or float(b['volume']) <= 0:
            pending = None
            continue
        # 使用前复权数据时，涨跌停无法可靠重建：大幅跳空和一字板不假设成交。
        prev = float(bars[i - 1]['close']) if i else op
        locked = abs(float(b['high']) - float(b['low'])) < 1e-9 or abs(op / prev - 1) >= .095
        if pending and not locked:
            if pending[0] == 'buy' and not shares:
                shares = cash * (1 - fee) / op
                cash = 0
                position = {'signal_date': pending[1], 'entry_date': b['date'], 'entry_price': op}
                trades.append(position)
            elif pending[0] == 'sell' and shares and position['entry_date'] != b['date']:
                cash = shares * op * (1 - fee)
                shares = 0
                position.update({'exit_date': b['date'], 'exit_price': op})
                position = None
        pending = None
        if i < 19:
            continue
        ma = sum(float(x['close']) for x in bars[i-19:i+1]) / 20
        if not shares and close > ma:
            pending = ('buy', b['date'])
        elif shares and close < ma:
            pending = ('sell', b['date'])
    final = cash + shares * float(bars[-1]['close']) if bars else cash
    return {'return_pct': round((final - 1) * 100, 2), 'trades': trades, 'open_position': bool(shares), 'fee_each_side': fee}
