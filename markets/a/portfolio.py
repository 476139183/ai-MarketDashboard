"""将独立股票回放记账为固定初始等权组合，包含未平仓市值。"""
from collections import defaultdict


def equity_curve(bars_by_stock, results, fee=.0015):
    days = sorted({b['date'] for bars in bars_by_stock.values() for b in bars})
    prices = {code: {b['date']: float(b['close']) for b in bars} for code, bars in bars_by_stock.items()}
    events = defaultdict(list)
    for code in bars_by_stock:
        result = results[code]
        positions = list(result['trades']) + ([result['open_position']] if result['open_position'] else [])
        for p in positions:
            events[p['entry_date']].append((code, 'buy', p['entry_price']))
            if p.get('exit_date'):
                events[p['exit_date']].append((code, 'sell', p['exit_price']))
    cash = {code: 1.0 for code in bars_by_stock}
    qty = {code: 0.0 for code in bars_by_stock}
    latest = {}
    curve = []
    for day in days:
        for code, action, price in events[day]:
            if action == 'buy':
                qty[code] = cash[code] / (price * (1 + fee))
                cash[code] = 0.0
            else:
                cash[code] = qty[code] * price * (1 - fee)
                qty[code] = 0.0
        for code in bars_by_stock:
            if day in prices[code]:
                latest[code] = prices[code][day]
        value = sum(cash[c] + qty[c] * latest.get(c, 0) for c in bars_by_stock) / len(cash)
        curve.append({'date': day, 'equity': value})
    return curve


def period_metrics(curve, start, end):
    earlier = [x for x in curve if x['date'] < start]
    inside = [x for x in curve if start <= x['date'] <= end]
    if not inside:
        return None
    opening = earlier[-1]['equity'] if earlier else 1.0
    peak, drawdown = opening, 0.0
    for x in inside:
        peak = max(peak, x['equity'])
        drawdown = min(drawdown, x['equity'] / peak - 1)
    return {'return_pct': round((inside[-1]['equity'] / opening - 1) * 100, 2),
            'max_drawdown_pct': round(drawdown * 100, 2)}
