#!/usr/bin/env python3
"""独立记录候选与观察信号，并在后续行情到达时补充观察值。"""
from datetime import datetime
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from daily_strategy import entry_signal
from forward_study import horizon_observation
from large_evaluate import market_above_ma20

LEDGER = Path('markets/a/candidate-ledger.json')


def signal_category(signals, market_ok):
    return '策略候选' if 'trend' in signals and market_ok else '观察池'


def record_timing(signal_date, recorded_at):
    return '信号日记录' if datetime.fromisoformat(recorded_at).date().isoformat() == signal_date else '事后记录'


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
    date = min(hot['as_of_date'], calendar[-1])
    market_pass = date in market_above_ma20(benchmark['bars'])
    captured = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    existing = json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else {'records': []}
    known = {(x['signal_date'], x['code']) for x in existing['records']}
    stale = []
    for row in universe['included']:
        code = row['code']
        bars = [x for x in histories[code] if x['date'] <= date]
        if not bars or bars[-1]['date'] != date:
            stale.append(code)
            continue
        signals = [rule for rule in ('trend', 'reclaim', 'breakout') if entry_signal(bars, rule)]
        if not signals or (date, code) in known:
            continue
        board = hot['metadata'][code]['board']
        existing['records'].append({'code': code, 'name': row['name'], 'signal_date': date,
                                    'recorded_at': captured, 'rules': signals, 'market_pass': market_pass,
                                    'category': signal_category(signals, market_pass), 'close': bars[-1]['close'],
                                    'record_timing': record_timing(date, captured),
                                    'board': board, 'source_universe_time': universe['generated_at']})
    next_date = dict(zip(calendar, calendar[1:]))
    for item in existing['records']:
        item['record_timing'] = record_timing(item['signal_date'], item['recorded_at'])
        code, next_day = item['code'], next_date.get(item['signal_date'])
        if code not in histories or not next_day:
            item['follow_up'] = {'status': '等待下一交易日行情'}
            continue
        bars = histories[code]
        by_date = {x['date']: x for x in bars}
        prior, nxt = by_date.get(item['signal_date']), by_date.get(next_day)
        if not nxt or not prior:
            item['follow_up'] = {'status': '下一交易日行情缺失，未假设入场', 'entry_date': next_day}
            continue
        ratio = .3 if item['board'] == '北交所' else .2 if item['board'] in ('创业板', '科创板') else .1
        op, previous = float(nxt['open']), float(prior['close'])
        if op <= 0 or previous <= 0 or nxt['volume'] <= 0 or abs(op / previous - 1) >= .09 or op >= previous * (1 + ratio) * .995:
            item['follow_up'] = {'status': '开盘异常或接近估算涨停，未模拟入场', 'entry_date': next_day}
            continue
        item['follow_up'] = {'status': '假设入场，观察价不保证成交', 'entry_date': next_day, 'entry_price': op,
                             'day3': horizon_observation(bars, calendar, next_day, op, 3, ratio),
                             'day5': horizon_observation(bars, calendar, next_day, op, 5, ratio)}
    existing.update(last_signal_date=date, last_run_at=captured, market_pass=market_pass,
                    universe_snapshot_time=universe['generated_at'], stale_symbols=stale,
                    limitations=['本次股票池按当前名称、市值和热榜筛选，历史回看有前视偏差',
                                 '未复权，未完整处理历史ST、除权、停牌和真实涨跌停',
                                 '观察池不等于买入建议；收盘观察价不保证可卖出',
                                 '需要手动刷新行情和运行脚本，本程序不会自动定时'])
    LEDGER.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    today = [x for x in existing['records'] if x['signal_date'] == date]
    print(f'{date}: 策略候选 {sum(x["category"] == "策略候选" for x in today)}，观察池 {sum(x["category"] == "观察池" for x in today)}，数据未对齐 {len(stale)}')


if __name__ == '__main__':
    main()
