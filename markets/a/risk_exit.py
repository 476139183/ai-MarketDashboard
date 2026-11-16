"""仅用昨日已知价格设保护线；日线触价成交是研究近似。"""


def protective_fill(bar, position, previous_close, previous_ma10, peak_close, limit_ratio,
                    loss_pct=.05, trailing_pct=.05):
    if bar['date'] <= position['entry_date'] or float(bar['volume']) <= 0:
        return None
    op = float(bar['open'])
    # 没有真实限价和队列，开盘接近估算跌停时保守地不假定可卖。
    if op <= 0 or op <= previous_close * (1 - limit_ratio) * 1.005:
        return None
    stop = max(position['entry_price'] * (1 - loss_pct),
               previous_ma10, peak_close * (1 - trailing_pct))
    if op <= stop:
        return {'price': op, 'stop': stop, 'reason': '开盘跌破昨日保护线'}
    if float(bar['low']) <= stop:
        return {'price': stop, 'stop': stop, 'reason': '盘中触及昨日保护线（日线近似）'}
    return None
