#!/usr/bin/env python3
"""Exploratory 15m session replay from historical Massive OHLCV."""
import argparse
import json
import sqlite3
from contextlib import closing
import sys
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path

from research_context import BJ, NY, DEFAULT_DB, SYMBOLS
sys.path.insert(0, str(Path(__file__).resolve().parent / 'dashboard'))
from engine import compute_intraday_signal, short_term_trend


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[int(row['timestamp']) // 900 * 900].append(row)
    out = []
    for t, entries in sorted(groups.items()):
        entries.sort(key=lambda x: x['timestamp'])
        volume = sum(float(x['volume'] or 0) for x in entries)
        pv = sum((x['high'] + x['low'] + x['close']) / 3 * (x['volume'] or 0) for x in entries)
        out.append({'t':t, 'o':entries[0]['open'], 'h':max(x['high'] for x in entries),
                    'l':min(x['low'] for x in entries), 'c':entries[-1]['close'], 'v':volume, 'pv':pv})
    return out


def session_complete(bars, day, session):
    # Require the opening and final quarter hour; missing entire interior buckets
    # are excluded because fills and stop paths cannot be reconstructed.
    bounds = {'pre': (time(4, 0), time(9, 30)), 'regular': (time(9, 30), time(16, 0)), 'post': (time(16, 0), time(20, 0))}
    start_time, end_time = bounds[session]
    date = datetime.fromisoformat(day).date()
    start = int(datetime.combine(date, start_time, NY).timestamp())
    end = int(datetime.combine(date, end_time, NY).timestamp())
    return {x['t'] for x in bars} == set(range(start, end, 900))


def replay_session(bars, atr, trend_filter=False, target_pct=None, trend_threshold=2, min_reward_risk=0.8):
    if len(bars) < 4 or bars[1]['t'] - bars[0]['t'] != 900:
        return None
    orb_h = max(x['h'] for x in bars[:2])
    orb_l = min(x['l'] for x in bars[:2])
    pending = None
    position = None
    for i in range(2, len(bars)):
        b = bars[i]
        if pending is not None and position is None:
            e, s, t = b['o'], pending['S'], pending['T1']
            side = pending['signal']
            if target_pct is not None:
                t = e * (1 + target_pct / 100 * (1 if side == '做多' else -1))
            risk = (e-s) if side == '做多' else (s-e)
            reward = (t-e) if side == '做多' else (e-t)
            if pending['entry_low'] <= e <= pending['entry_high'] and risk > 0 and reward / risk >= min_reward_risk:
                position = {'side': side, 'entry':e, 'entry_t':b['t'], 'stop':s, 'target':t}
            pending = None
        if position:
            side = position['side']
            s, t = position['stop'], position['target']
            if side == '做多':
                exit_price = min(b['o'], s) if b['l'] <= s else max(b['o'], t) if b['h'] >= t else None
                reason = 'stop' if b['l'] <= s else 'target'
            else:
                exit_price = max(b['o'], s) if b['h'] >= s else min(b['o'], t) if b['l'] <= t else None
                reason = 'stop' if b['h'] >= s else 'target'
            if exit_price is not None:
                position.update(exit=exit_price, exit_t=b['t'], reason=reason)
                break
            continue
        if i == len(bars)-1:
            continue
        hist = bars[:i+1]
        volume = sum(x['v'] for x in hist)
        if not volume:
            continue
        pv = sum(x.get('pv', (x['h']+x['l']+x['c'])/3*x['v']) for x in hist)
        sig = compute_intraday_signal(px=b['c'], atr=atr, orb_l=orb_l, orb_h=orb_h,
            vwap=pv/volume, day_o=bars[0]['o'], path_l=min(x['l'] for x in hist),
            path_h=max(x['h'] for x in hist), bars=hist)
        if sig.get('entry_ready') and sig.get('signal') in ('做多','做空') and all(sig.get(x) is not None for x in ('S','T1','entry_low','entry_high')):
            score = short_term_trend(hist).get('score', 0)
            if not trend_filter or (sig['signal'] == '做多' and score >= trend_threshold) or (sig['signal'] == '做空' and score <= -trend_threshold):
                pending = sig
    if not position:
        return None
    if 'exit' not in position:
        position.update(exit=bars[-1]['c'], exit_t=bars[-1]['t']+900, reason='session_close')
    direction = 1 if position['side'] == '做多' else -1
    position['pnl_pct'] = round(direction * (position['exit'] / position['entry'] - 1) * 100, 4)
    return position


def run(db, trend_filter=False, target_pct=None, trend_threshold=2, min_reward_risk=0.8, min_range_pct=0):
    if not db.is_file():
        raise FileNotFoundError(db)
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select * from bars where source='Massive historical aggregates' order by symbol, trading_date, timestamp").fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['symbol'], row['trading_date'], row['session'])].append(row)
    reports = []
    for symbol in SYMBOLS:
        dates = sorted({d for sym, d, s in grouped if sym == symbol and s == 'regular'})
        for session in ('pre', 'regular', 'post'):
            trades, tested = [], 0
            for idx, day in enumerate(dates):
                if idx == 0 or not grouped.get((symbol, day, session)):
                    continue
                prior = grouped[(symbol, dates[idx-1], 'regular')]
                # Only completed prior session is known; intentionally not same-day ATR.
                atr = max(x['high'] for x in prior) - min(x['low'] for x in prior)
                bars = aggregate(grouped[(symbol, day, session)])
                if atr <= 0 or not session_complete(bars, day, session) or atr / bars[0]['o'] * 100 < min_range_pct:
                    continue
                tested += 1
                trade = replay_session(bars, atr, trend_filter=trend_filter, target_pct=target_pct, trend_threshold=trend_threshold, min_reward_risk=min_reward_risk)
                if trade:
                    trade['day'] = day
                    trades.append(trade)
            n = len(trades)
            reports.append({'symbol':symbol, 'session':session, 'n_days':tested, 'trades':n,
                'wins':sum(x['pnl_pct'] > 0 for x in trades),
                'win_rate_pct':round(100*sum(x['pnl_pct'] > 0 for x in trades)/n, 1) if n else None,
                'mean_trade_pct':round(sum(x['pnl_pct'] for x in trades)/n, 3) if n else None,
                'sum_trade_pct':round(sum(x['pnl_pct'] for x in trades), 3), 'details':trades})
    return {'generated_bj':datetime.now(BJ).isoformat(), 'source':'Massive historical aggregates',
        'trend_filter': trend_filter, 'target_pct': target_pct, 'trend_threshold': trend_threshold, 'min_reward_risk': min_reward_risk, 'min_range_pct': min_range_pct,
        'method':'15m ORB first 30m per session, prior regular session range as ATR proxy, minute typical-price volume weighted VWAP proxy, next bar open entry, one trade per session, forced close; simultaneous stop/target assumes stop first; excludes costs and slippage', 'results':reports}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=DEFAULT_DB)
    p.add_argument('--trend-filter', action='store_true', help='仅做强趋势同向信号')
    p.add_argument('--target-pct', type=float, help='按入场价设置固定止盈百分比')
    p.add_argument('--trend-threshold', type=int, default=2)
    p.add_argument('--min-reward-risk', type=float, default=0.8)
    p.add_argument('--min-range-pct', type=float, default=0)
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / '.cache' / 'session-replay.json')
    args=p.parse_args()
    if args.target_pct is not None and not 0 < args.target_pct < 100:
        p.error("target-pct 必须大于 0 且小于 100")
    if not 1 <= args.trend_threshold <= 4 or args.min_reward_risk <= 0 or args.min_range_pct < 0:
        p.error('趋势阈值、收益风险比或波动阈值无效')
    result=run(args.db, trend_filter=args.trend_filter, target_pct=args.target_pct, trend_threshold=args.trend_threshold, min_reward_risk=args.min_reward_risk, min_range_pct=args.min_range_pct)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'output':str(args.output), 'results':[{k:v for k,v in x.items() if k != 'details'} for x in result['results']]}, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
