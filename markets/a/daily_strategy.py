"""A 股日线研究：只在收盘后决定下一交易日动作。"""


def entry_signal(bars, entry_rule='breakout'):
    if entry_rule not in ('breakout', 'reclaim', 'trend'):
        raise ValueError('未知入场规则')
    if len(bars) < 21:
        return False
    closes = [float(x['close']) for x in bars[-20:]]
    close = closes[-1]
    ma5, ma20 = sum(closes[-5:]) / 5, sum(closes) / 20
    if not (ma5 > ma20 and close > ma20):
        return False
    if entry_rule == 'breakout':
        return close > max(float(x['high']) for x in bars[-21:-1])
    if entry_rule == 'reclaim':
        prior_ma5 = sum(float(x['close']) for x in bars[-6:-1]) / 5
        return float(bars[-2]['close']) <= prior_ma5 and close > ma5
    prior_ma20 = sum(float(x['close']) for x in bars[-21:-1]) / 20
    return float(bars[-2]['close']) <= prior_ma20


def run(bars, limit_ratio, fee=0.0015, min_entry_date=None, entry_rule='breakout', exit_ma=10, trading_calendar=None, allowed_signal_dates=None):
    if entry_rule not in ('breakout', 'reclaim', 'trend') or exit_ma not in (10, 20):
        raise ValueError('未知研究规则')
    trades, position, pending = [], None, None
    next_day = {a: b for a, b in zip(trading_calendar, trading_calendar[1:])} if trading_calendar else None
    skipped = 0
    for i, bar in enumerate(bars):
        day, op, close = bar['date'], float(bar['open']), float(bar['close'])
        if pending and pending['side'] == 'buy' and next_day is not None and next_day.get(pending['date']) != day:
            pending = None
            skipped += 1
        if i and pending:
            prev = float(bars[i - 1]['close'])
            # 仅为保守的成交近似，不替代历史真实涨跌停价和盘口。
            unusual_gap = abs(op / prev - 1) >= .09
            blocked = (op >= prev * (1 + limit_ratio) * .995 if pending['side'] == 'buy'
                       else op <= prev * (1 - limit_ratio) * 1.005)
            if unusual_gap or blocked or op <= 0:
                skipped += 1
                if pending['side'] == 'buy':
                    pending = None
            elif pending['side'] == 'buy' and position is None:
                position = {'signal_date': pending['date'], 'entry_date': day, 'entry_price': op}
                pending = None
            elif pending['side'] == 'sell' and position and day > position['entry_date']:
                position.update(exit_signal_date=pending['date'], exit_date=day, exit_price=op,
                                return_pct=round((op * (1 - fee) / (position['entry_price'] * (1 + fee)) - 1) * 100, 3))
                trades.append(position)
                position = None
                pending = None
        if i < 20:
            continue
        closes = [float(x['close']) for x in bars[i-19:i+1]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes) / 20
        if position:
            if not pending and (close < (ma10 if exit_ma == 10 else ma20) or close <= position['entry_price'] * .95):
                pending = {'side': 'sell', 'date': day}
        elif not pending and (min_entry_date is None or day >= min_entry_date) and (allowed_signal_dates is None or day in allowed_signal_dates):
            if entry_signal(bars[i-20:i+1], entry_rule):
                pending = {'side': 'buy', 'date': day}
    return {'trades': trades, 'open_position': position, 'pending': pending, 'skipped_fills': skipped}


def from_minutes(bars):
    from collections import defaultdict
    grouped = defaultdict(list)
    for b in bars:
        grouped[b['time'][:10]].append(b)
    required = {'09:45', '10:00', '10:15', '10:30', '10:45', '11:00', '11:15', '11:30',
                '13:15', '13:30', '13:45', '14:00', '14:15', '14:30', '14:45', '15:00'}
    daily = []
    for day, rows in sorted(grouped.items()):
        if len(rows) != 16 or {x['time'][11:] for x in rows} != required:
            continue
        daily.append({'date': day, 'open': rows[0]['open'], 'high': max(x['high'] for x in rows),
                      'low': min(x['low'] for x in rows), 'close': rows[-1]['close'],
                      'volume': sum(x['volume'] for x in rows)})
    return daily
