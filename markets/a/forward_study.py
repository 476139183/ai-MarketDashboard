#!/usr/bin/env python3
"""独立记录每天候选并观测未来 3 / 5 个交易日，不以持仓过滤信号。"""
import gzip
import json
import statistics
from daily_strategy import entry_signal
from large_evaluate import market_above_ma20


def horizon_observation(bars, calendar, entry_day, entry_price, horizon, limit_ratio, fee=.0015):
    if horizon < 1 or entry_day not in calendar or entry_price <= 0:
        return {'status': '输入无效'}
    idx = calendar.index(entry_day) + horizon
    if idx >= len(calendar):
        return {'status': '数据尚不足'}
    target, prior = calendar[idx], calendar[idx - 1]
    by_date = {x['date']: x for x in bars}
    if target not in by_date or prior not in by_date:
        return {'status': '目标日行情缺失', 'date': target}
    close, volume = float(by_date[target]['close']), float(by_date[target]['volume'])
    previous = float(by_date[prior]['close'])
    if previous <= 0 or close <= 0 or volume <= 0:
        return {'status': '目标日无有效成交', 'date': target}
    if close <= previous * (1 - limit_ratio) * 1.005:
        return {'status': '接近估算跌停价，无法确认卖出', 'date': target}
    return {'status': '观察值', 'date': target, 'close': close,
            'return_pct': round((close * (1 - fee) / (entry_price * (1 + fee)) - 1) * 100, 3)}


def summary(events, market_ok, horizon, start, end):
    values = [e[str(horizon)]['return_pct'] for e in events if e['market_ok'] == market_ok and
              start <= e['signal_date'] <= end and e[str(horizon)]['status'] == '观察值' and e[str(horizon)]['date'] <= end]
    return {'observations': len(values), 'positive_pct': round(sum(x > 0 for x in values) * 100 / len(values), 2) if values else None,
            'mean_pct': round(statistics.mean(values), 3) if values else None,
            'median_pct': round(statistics.median(values), 3) if values else None}


def main():
    with open('markets/a/large-universe.json', encoding='utf-8') as f:
        universe = json.load(f)
    with gzip.open('markets/a/hot-history.json.gz', 'rt', encoding='utf-8') as f:
        histories = json.load(f)
    with open('markets/a/hot-backtest.json', encoding='utf-8') as f:
        hot = json.load(f)
    with open('markets/a/benchmark-results.json', encoding='utf-8') as f:
        benchmark = json.load(f)
    calendar = [x['date'] for x in benchmark['bars']]
    next_date = dict(zip(calendar, calendar[1:]))
    market_ok = market_above_ma20(benchmark['bars'])
    events, no_buy, pending = [], [], []
    for row in universe['included']:
        code, name = row['code'], row['name']
        bars = histories[code]
        dates = {x['date']: x for x in bars}
        board = hot['metadata'][code]['board']
        ratio = .3 if board == '北交所' else .2 if board in ('创业板', '科创板') else .1
        for i in range(20, len(bars)):
            signal = bars[i]
            if not entry_signal(bars[i-20:i+1], 'trend'):
                continue
            day = signal['date']
            item = {'code': code, 'name': name, 'signal_date': day, 'market_ok': day in market_ok}
            next_day = next_date.get(day)
            if not next_day:
                pending.append({**item, 'reason': '下一交易日行情尚不可用'})
                continue
            if next_day not in dates:
                no_buy.append({**item, 'entry_date': next_day, 'reason': '下一市场交易日无行情，候选买入过期'})
                continue
            b = dates[next_day]
            op, prev = float(b['open']), float(signal['close'])
            if op <= 0 or prev <= 0 or b['volume'] <= 0 or abs(op / prev - 1) >= .09 or op >= prev * (1 + ratio) * .995:
                no_buy.append({**item, 'entry_date': next_day, 'reason': '开盘异常或接近估算涨停，未模拟入场'})
                continue
            item.update(entry_date=next_day, entry_price=op,
                        **{str(n): horizon_observation(bars, calendar, next_day, op, n, ratio) for n in (3, 5)})
            events.append(item)
    periods = {'2023_2024': ('2023-01-01', '2024-12-31'), '2025': ('2025-01-01', '2025-12-31'), '2026': ('2026-01-01', '2026-10-27')}
    result = {'method': '独立扫描收盘日线趋势信号，不因已有持仓抑制新候选；假设下一交易日开盘入场、3或5个交易日后收盘观察，单边成本0.15%',
              'market_filter': '沪深300信号日收盘价高于MA20；未通过的信号作为观察池',
              'periods': {name: {'market_pass': {str(n): summary(events, True, n, start, end) for n in (3, 5)},
                                 'market_weak_watch': {str(n): summary(events, False, n, start, end) for n in (3, 5)}}
                          for name, (start, end) in periods.items()},
              'events': events, 'no_buy': no_buy, 'pending': pending,
              'limitations': ['当前热榜和当前市值回看历史，存在前视及幸存者偏差', '未复权行情、缺少历史ST及真实涨跌停',
                              '每日信号独立记录，可能同股重叠；并非可执行组合收益', '目标日收盘是观察价，不保证实际成交']}
    with open('markets/a/forward-study.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
