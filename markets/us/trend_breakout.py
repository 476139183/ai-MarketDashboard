#!/usr/bin/env python3
"""Experimental trend breakout replay; no execution or live signal changes."""
import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

from collect_sessions import BJ, DEFAULT_DB, SYMBOLS
from session_replay import aggregate, session_complete, short_term_trend


def breakout_signal(bars, orb_high, orb_low, threshold=3):
    if len(bars) < 5:
        return None
    volume = sum(b['v'] for b in bars)
    if volume <= 0:
        return None
    vwap = sum(b['pv'] for b in bars) / volume
    last = bars[-1]['c']
    score = short_term_trend(bars)['score']
    if score >= threshold and last > orb_high and last > vwap:
        return {'side': '做多', 'stop': min(b['l'] for b in bars[-2:]), 'signal_t': bars[-1]['t']}
    if score <= -threshold and last < orb_low and last < vwap:
        return {'side': '做空', 'stop': max(b['h'] for b in bars[-2:]), 'signal_t': bars[-1]['t']}
    return None


def replay_breakout(bars, atr, target_pct=2.2, threshold=3, min_reward_risk=1):
    if len(bars) < 6:
        return None
    high = max(b['h'] for b in bars[:2]); low = min(b['l'] for b in bars[:2])
    pending = None; position = None
    for i in range(4, len(bars)):
        b = bars[i]
        if pending and position is None:
            e, s = b['o'], pending['stop']
            side = pending['side']
            risk = (e-s) if side == '做多' else (s-e)
            target = e * (1 + target_pct/100 * (1 if side == '做多' else -1))
            if risk > 0 and risk <= e*target_pct/100/min_reward_risk and abs(e-bars[i-1]['c']) <= 0.25*atr:
                position = {'side':side,'entry':e,'entry_t':b['t'],'stop':s,'target':target,'signal_t':pending['signal_t']}
            pending = None
        if position:
            s, t = position['stop'], position['target']
            if position['side'] == '做多':
                hit_s, hit_t = b['l'] <= s, b['h'] >= t
                px = min(b['o'], s) if hit_s else max(b['o'],t) if hit_t else None
            else:
                hit_s, hit_t = b['h'] >= s, b['l'] <= t
                px = max(b['o'], s) if hit_s else min(b['o'],t) if hit_t else None
            if px is not None:
                position.update(exit=px,exit_t=b['t'],reason='stop' if hit_s else 'target')
                break
            continue
        if i < len(bars)-1:
            pending = breakout_signal(bars[:i+1], high, low, threshold)
    if not position:
        return None
    if 'exit' not in position:
        position.update(exit=bars[-1]['c'],exit_t=bars[-1]['t']+900,reason='session_close')
    sign = 1 if position['side']=='做多' else -1
    position['pnl_pct'] = round(sign*(position['exit']/position['entry']-1)*100,4)
    return position


def run(db, threshold=3, min_reward_risk=1):
    with closing(sqlite3.connect(f'file:{db}?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        rows=conn.execute("select * from bars where source='Massive historical aggregates' and session='regular' order by symbol,trading_date,timestamp").fetchall()
    grouped=defaultdict(list)
    for row in rows: grouped[(row['symbol'],row['trading_date'])].append(row)
    results=[]
    for symbol in SYMBOLS:
        dates=sorted(day for sym,day in grouped if sym==symbol)
        trades=[]; eligible=0
        for i, day in enumerate(dates):
            if i==0: continue
            prior=grouped[(symbol,dates[i-1])]
            atr=max(x['high'] for x in prior)-min(x['low'] for x in prior)
            bars=aggregate(grouped[(symbol,day)])
            if not session_complete(bars,day,'regular') or atr/bars[0]['o']*100 < 3: continue
            eligible+=1
            trade=replay_breakout(bars,atr,threshold=threshold,min_reward_risk=min_reward_risk)
            if trade:
                trade['day']=day
                trades.append(trade)
        results.append({'symbol':symbol,'eligible_days':eligible,'trades':trades})
    return {'generated_bj':datetime.now(BJ).isoformat(),'trend_threshold':threshold,'min_reward_risk':min_reward_risk,'model':'exploratory 15m RTH breakout, prior range >=3%, 2.2% target, next bar open, 2-bar structure stop, stop before target, close at end, no costs', 'results':results}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--trend-threshold',type=int,choices=[3,4],default=3)
    p.add_argument('--min-reward-risk',type=float,default=1)
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'.cache'/'breakout-replay.json')
    args=p.parse_args()
    if args.min_reward_risk <= 0: p.error('min-reward-risk 必须大于 0')
    result=run(args.db, threshold=args.trend_threshold, min_reward_risk=args.min_reward_risk); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(args.output),'trades':{r['symbol']:len(r['trades']) for r in result['results']}},ensure_ascii=False))

if __name__=='__main__': main()
