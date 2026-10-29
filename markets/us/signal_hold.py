#!/usr/bin/env python3
"""Replay entry and exit on successive 15-minute signal changes."""
import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

from research_context import BJ, DEFAULT_DB, SYMBOLS
from session_replay import aggregate, session_complete, compute_intraday_signal


def signal_at_close(bars, atr):
    volume=sum(b['v'] for b in bars)
    if not volume:
        return {'signal':'观望'}
    return compute_intraday_signal(px=bars[-1]['c'], atr=atr,
        orb_l=min(b['l'] for b in bars[:2]), orb_h=max(b['h'] for b in bars[:2]),
        vwap=sum(b['pv'] for b in bars)/volume, day_o=bars[0]['o'],
        path_l=min(b['l'] for b in bars), path_h=max(b['h'] for b in bars), bars=bars)


def replay(bars, atr, mode="signal", stop_mode="intrabar", confirmations=1):
    if len(bars)<4:
        return []
    trades=[]; position=None; pending=None; last_signal=None; streak=0
    for i in range(2,len(bars)):
        bar=bars[i]
        if pending:
            if pending['action']=='enter' and position is None:
                stop=pending.get('stop')
                valid_stop = mode != 'structure' or (stop is not None and ((pending['side']=='做多' and stop < bar['o']) or (pending['side']=='做空' and stop > bar['o'])))
                if valid_stop:
                    position={'side':pending['side'],'entry':bar['o'],'entry_t':bar['t'],
                              'entry_signal_t':pending['signal_t'], 'signals':[pending['side']], 'stop':stop}
            elif pending['action']=='exit' and position:
                sign=1 if position['side']=='做多' else -1
                position.update(exit=bar['o'],exit_t=bar['t'],reason=pending['reason'],
                    pnl_pct=round(sign*(bar['o']/position['entry']-1)*100,4))
                trades.append(position); position=None
            pending=None
        if position and mode == 'structure':
            stop=position['stop']
            hit=(bar['l'] <= stop) if position['side']=='做多' else (bar['h'] >= stop)
            if hit:
                if stop_mode == 'bar-close':
                    pending={'action':'exit','reason':'结构止损','signal_t':bar['t']}
                else:
                    px=min(bar['o'], stop) if position['side']=='做多' else max(bar['o'], stop)
                    sign=1 if position['side']=='做多' else -1
                    position.update(exit=px,exit_t=bar['t'],reason='结构止损',pnl_pct=round(sign*(px/position['entry']-1)*100,4))
                    trades.append(position);position=None
        result=signal_at_close(bars[:i+1],atr)
        signal=result.get('signal') or '观望'
        if position:
            last_signal=None; streak=0
            position['signals'].append(signal)
            if mode == 'structure':
                desired='多头' if position['side']=='做多' else '空头'
                explicit_exit='平多' if position['side']=='做多' else '平空'
                should_exit = result.get('structure') != desired or signal == explicit_exit
                reason='结构失效'
            else:
                should_exit=signal != position['side']
                reason=signal
            if should_exit and pending is None and i<len(bars)-1:
                pending={'action':'exit','reason':reason,'signal_t':bar['t']}
        elif signal in ('做多','做空') and i<len(bars)-1:
            streak=streak+1 if signal==last_signal else 1
            last_signal=signal
            if streak >= confirmations:
                pending={'action':'enter','side':signal,'signal_t':bar['t'],'stop':result.get('S')}
                streak=0;last_signal=None
        else:
            last_signal=None;streak=0
    if position:
        sign=1 if position['side']=='做多' else -1
        position.update(exit=bars[-1]['c'],exit_t=bars[-1]['t']+900,reason=('结构止损' if pending and pending.get('reason')=='结构止损' else '时段收盘'),
            pnl_pct=round(sign*(bars[-1]['c']/position['entry']-1)*100,4))
        trades.append(position)
    return trades


def run(db, mode="signal", stop_mode="intrabar", confirmations=1):
    with closing(sqlite3.connect(f'file:{db}?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        rows=conn.execute("select * from bars where source='Massive historical aggregates' and session='regular' order by symbol,trading_date,timestamp").fetchall()
    grouped=defaultdict(list)
    for row in rows: grouped[(row['symbol'],row['trading_date'])].append(row)
    results=[]
    for symbol in SYMBOLS:
        dates=sorted(d for sym,d in grouped if sym==symbol); trades=[]; days=0
        for i,day in enumerate(dates):
            if i==0: continue
            bars=aggregate(grouped[(symbol,day)])
            if not session_complete(bars,day,'regular'): continue
            prior=grouped[(symbol,dates[i-1])]
            atr=max(x['high'] for x in prior)-min(x['low'] for x in prior)
            if atr<=0: continue
            days+=1
            for trade in replay(bars,atr,mode=mode,stop_mode=stop_mode,confirmations=confirmations):
                trade['day']=day;trades.append(trade)
        results.append({'symbol':symbol,'days':days,'trades':trades})
    return {'generated_bj':datetime.now(BJ).isoformat(), 'mode':mode, 'stop_mode':stop_mode, 'confirmations':confirmations, 'method': (('RTH 15m; enter next open; continue while structure matches; stop checked after bar closes with exit next open; exit next open on structure loss; forced session close; no costs; prior session range' if stop_mode=='bar-close' else 'RTH 15m; enter next open; continue while structure matches; initial structure stop with adverse open gap; exit next open on structure loss; forced session close; no costs; prior session range') if mode=='structure' else 'RTH 15m; existing signal at bar close; enter/exit next open on directional / nonmatching signal; repeat signals hold; forced session close; no stop, take-profit or trading costs; ATR proxy prior session range'), 'results':results}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--mode',choices=['signal','structure'],default='signal')
    p.add_argument('--stop-mode',choices=['intrabar','bar-close'],default='intrabar')
    p.add_argument('--confirmations',type=int,choices=[1,2],default=1)
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'.cache'/'signal-hold.json')
    a=p.parse_args(); result=run(a.db,mode=a.mode,stop_mode=a.stop_mode,confirmations=a.confirmations); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(a.output),'trades':{x['symbol']:len(x['trades']) for x in result['results']}},ensure_ascii=False))

if __name__=='__main__': main()
