#!/usr/bin/env python3
"""Research 1-minute entries and 15-minute position structure checks."""
import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

from research_context import BJ, DEFAULT_DB, SYMBOLS
from session_replay import aggregate, session_complete, compute_intraday_signal


def compute_asof(rows, atr):
    bars=aggregate(rows)
    if len(bars)<3:
        return {'signal':'观望','structure':'震荡'}
    volume=sum(x['v'] for x in bars)
    if volume<=0:
        return {'signal':'观望','structure':'震荡'}
    return compute_intraday_signal(px=bars[-1]['c'],atr=atr,
        orb_l=min(x['l'] for x in bars[:2]),orb_h=max(x['h'] for x in bars[:2]),
        vwap=sum(x['pv'] for x in bars)/volume,day_o=bars[0]['o'],
        path_l=min(x['l'] for x in bars),path_h=max(x['h'] for x in bars),bars=bars)


def replay(rows, atr):
    if len(rows)<31: return []
    rows=sorted(rows,key=lambda x:x['timestamp'])
    start=rows[0]['timestamp']; trades=[];position=None;pending=None
    for i,row in enumerate(rows):
        ts=row['timestamp']
        if pending:
            if pending['action']=='entry' and position is None:
                stop=pending['stop']; side=pending['side']; e=row['open']
                if ts==pending['t']+60 and stop is not None and ((side=='做多' and stop<e) or (side=='做空' and stop>e)):
                    position={'side':side,'entry':e,'entry_t':ts,'signal_t':pending['t'],'stop':stop,'checks':[]}
            elif pending['action']=='exit' and position:
                sign=1 if position['side']=='做多' else -1
                position.update(exit=row['open'],exit_t=ts,reason='结构失效',pnl_pct=round(sign*(row['open']/position['entry']-1)*100,4))
                trades.append(position);position=None
            pending=None
        if position:
            stop=position['stop'];side=position['side']
            hit=row['low']<=stop if side=='做多' else row['high']>=stop
            if hit:
                px=min(row['open'],stop) if side=='做多' else max(row['open'],stop)
                sign=1 if side=='做多' else -1
                position.update(exit=px,exit_t=ts,reason='结构止损',pnl_pct=round(sign*(px/position['entry']-1)*100,4))
                trades.append(position);position=None
        if ts-start < 30*60 or i==len(rows)-1:
            continue
        if position is None:
            sig=compute_asof(rows[:i+1],atr)
            if sig.get('signal') in ('做多','做空'):
                pending={'action':'entry','side':sig['signal'],'stop':sig.get('S'),'t':ts}
        elif (ts//60)%15==14:
            sig=compute_asof(rows[:i+1],atr)
            desired='多头' if position['side']=='做多' else '空头'
            exit_sig='平多' if position['side']=='做多' else '平空'
            position['checks'].append({'t':ts+60,'structure':sig.get('structure'),'signal':sig.get('signal')})
            if sig.get('structure')!=desired or sig.get('signal')==exit_sig:
                pending={'action':'exit','t':ts}
    if position:
        last=rows[-1];sign=1 if position['side']=='做多' else -1
        position.update(exit=last['close'],exit_t=last['timestamp']+60,reason='时段收盘',pnl_pct=round(sign*(last['close']/position['entry']-1)*100,4))
        trades.append(position)
    return trades


def run(db):
    with closing(sqlite3.connect(f'file:{db}?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row
        rows=c.execute("select * from bars where source='Massive historical aggregates' and session='regular' order by symbol,trading_date,timestamp").fetchall()
    groups=defaultdict(list)
    for r in rows:groups[(r['symbol'],r['trading_date'])].append(r)
    results=[]
    for symbol in SYMBOLS:
        dates=sorted(d for s,d in groups if s==symbol); trades=[]; days=0
        for i,day in enumerate(dates):
            if not i:continue
            day_rows=groups[(symbol,day)];bars=aggregate(day_rows)
            # Incomplete minute bars are excluded; otherwise next-minute execution is not demonstrable.
            if not session_complete(bars,day,'regular') or len(day_rows)!=390:continue
            prev=groups[(symbol,dates[i-1])];atr=max(x['high'] for x in prev)-min(x['low'] for x in prev)
            if atr<=0:continue
            days+=1
            for trade in replay(day_rows,atr):trade['day']=day;trades.append(trade)
        results.append({'symbol':symbol,'days':days,'trades':trades})
    return {'generated_bj':datetime.now(BJ).isoformat(),'method':'1m bar-close signal, next-minute open entry, 15m structure close check, next-minute exit, 1m structural stop, forced session close, prior-day range proxy; no fees/slippage', 'results':results}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,default=DEFAULT_DB);p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'.cache'/'intraminute-hold.json')
    a=p.parse_args();r=run(a.db);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(a.output),'trades':{x['symbol']:len(x['trades']) for x in r['results']}},ensure_ascii=False))

if __name__=='__main__':main()
