#!/usr/bin/env python3
"""读取当前板块样本，分段拉取前复权日线并比较有限的日线策略。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import statistics
from daily_strategy import from_minutes, run
from research import fetch

START, END = '2023-01-01', '2026-10-27'
PERIODS = {'探索_2023_2024': ('2023-01-01', '2024-12-31'),
           '验证_2025': ('2025-01-01', '2025-12-31'),
           '保留_2026': ('2026-01-01', END)}
RULES = [(entry, ma) for entry in ('breakout', 'reclaim', 'trend') for ma in (10, 20)]


def load(code):
    chunks = []
    for start, end in ((START, '2024-12-31'), ('2025-01-01', END)):
        try:
            _, _, part = fetch(code, start, end)
            chunks += part
        except ValueError as exc:
            if '未取得前复权日线' not in str(exc):
                raise
    if len(chunks) < 21:
        raise ValueError('可用日线不足21天')
    if any(a['date'] >= b['date'] for a, b in zip(chunks, chunks[1:])):
        raise ValueError('日线重复或乱序')
    return chunks


def metrics(rows, fee=.0015):
    returns = [100 * (t['exit_price'] * (1-fee) / (t['entry_price'] * (1+fee)) - 1) for _, t in rows]
    by_stock = {}
    for (code, _), value in zip(rows, returns):
        by_stock[code] = by_stock.get(code, 1) * (1 + value / 100)
    return {'trades': len(rows), 'active_stocks': len(by_stock),
            'win_rate_pct': round(100 * sum(r > 0 for r in returns) / len(returns), 2) if returns else None,
            'mean_trade_pct': round(statistics.mean(returns), 3) if returns else None,
            'median_trade_pct': round(statistics.median(returns), 3) if returns else None,
            'median_active_stock_pct': round(100 * (statistics.median(by_stock.values())-1), 2) if by_stock else None}


def main():
    with open('markets/a/sector-results.json', encoding='utf-8') as f:
        universe = json.load(f)
    bars, errors, source_by_stock = {}, {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(load, code): code for code in universe['stocks']}
        for n, future in enumerate(as_completed(futures), 1):
            code = futures[future]
            try:
                bars[code] = future.result()
                source_by_stock[code] = '腾讯前复权日线'
            except Exception as exc:
                fallback = from_minutes(universe['stocks'][code]['bars'])
                if len(fallback) >= 21:
                    bars[code] = fallback
                    source_by_stock[code] = '新浪近期未复权分钟聚合日线；前复权源不可用'
                else:
                    errors[code] = str(exc)
            if n % 20 == 0 or n == len(futures):
                print(f'多年日线 {n}/{len(futures)}，成功 {len(bars)}，失败 {len(errors)}', flush=True)
    results = {}
    for entry, ma in RULES:
        name = f'{entry}_ma{ma}'
        period_rows = {p: [] for p in PERIODS}
        by_stock = {}
        for code, candles in bars.items():
            metadata = universe['stocks'][code]
            result = run(candles, .2 if metadata['board'] in ('创业板', '科创板') else .1,
                         entry_rule=entry, exit_ma=ma)
            by_stock[code] = result
            for trade in result['trades']:
                for period, (start, end) in PERIODS.items():
                    if start <= trade['signal_date'] and trade['exit_date'] <= end:
                        period_rows[period].append((code, trade))
        results[name] = {'periods': {p: metrics(rows) for p, rows in period_rows.items()},
                         'fee_sensitivity': {p: {str(fee): metrics(rows, fee) for fee in (.0005, .0015, .003)} for p, rows in period_rows.items()},
                         'by_stock': by_stock}
    train = '探索_2023_2024'
    valid = [(x, r['periods'][train]) for x, r in results.items() if r['periods'][train]['trades'] >= 30 and r['periods'][train]['active_stocks'] >= 10]
    chosen = max(valid, key=lambda item: item[1]['mean_trade_pct'])[0] if valid else None
    report = {'source': '腾讯公开前复权日线，缺失时用新浪近期未复权分钟聚合日线；板块样本为2026年当前成分',
              'periods': PERIODS, 'universe_size': len(universe['stocks']), 'loaded': len(bars), 'errors': errors,
              'rules': results, 'chosen_by_exploration_mean': chosen,
              'source_by_stock': source_by_stock, 'bars': bars}
    with open('markets/a/long-backtest.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
