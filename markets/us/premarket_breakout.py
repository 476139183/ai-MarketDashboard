#!/usr/bin/env python3
"""Research first-30-minute premarket range breakout; no live orders."""
import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from collect_sessions import BJ, DEFAULT_DB, NY
from session_replay import aggregate, session_complete
from first_signal_quality import evaluate_path


def replay_day(rows, pre_low, pre_high):
    rows=sorted(rows,key=lambda r:r['timestamp'])
    if len(rows)<2:return None
    pending=None;position=None; done=False
    pv=vol=0
    for i,b in enumerate(rows):
        t=b['timestamp']; start=rows[0]['timestamp']
        if pending:
            if pending['kind']=='enter':
                side=pending['side'];stop=pending['stop'];e=b['open']
                if t==pending['t']+60 and ((side=='做多' and stop<e) or (side=='做空' and stop>e)):
                    position={'side':side,'entry':e,'entry_t':t,'signal_t':pending['t'], 'stop':stop}
                else:
                    done=True
            elif pending['kind']=='exit' and position:
                sign=1 if position['side']=='做多' else -1
                position.update(exit=b['open'],exit_t=t,reason='结构失效',pnl_pct=round(sign*(b['open']/position['entry']-1)*100,4));return position
            pending=None
        if position:
            stop=position['stop']; side=position['side']
            hit=b['low']<=stop if side=='做多' else b['high']>=stop
            if hit:
                px=min(b['open'],stop) if side=='做多' else max(b['open'],stop)
                sign=1 if side=='做多' else -1
                position.update(exit=px,exit_t=t,reason='结构止损',pnl_pct=round(sign*(px/position['entry']-1)*100,4));return position
        v=b['volume'] or 0;vol+=v;pv+=(b['high']+b['low']+b['close'])/3*v
        if position:
            if (t//60)%15==14 and vol>0:
                failed=(b['close']<pv/vol) if position['side']=='做多' else (b['close']>pv/vol)
                if failed: pending={'kind':'exit'}
        elif not done and t-start<30*60 and i<len(rows)-1:
            side='做多' if b['close']>pre_high else '做空' if b['close']<pre_low else None
            if side:
                # Stop is the extreme of the opening period observed up to the signal.
                history=rows[:i+1]
                stop=min(x['low'] for x in history) if side=='做多' else max(x['high'] for x in history)
                pending={'kind':'enter','side':side,'stop':stop,'t':t}
    if position:
        sign=1 if position['side']=='做多' else -1; last=rows[-1]
        position.update(exit=last['close'],exit_t=last['timestamp']+60,reason='时段收盘',pnl_pct=round(sign*(last['close']/position['entry']-1)*100,4));return position
    return None


def run(db=DEFAULT_DB):
    with closing(sqlite3.connect(f'file:{db}?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        rows=conn.execute("select * from bars where source='Massive historical aggregates' and symbol in ('SNDK','MU','NVDA') and session in ('pre','regular') order by symbol,trading_date,timestamp").fetchall()
    g=defaultdict(list)
    for r in rows:g[(r['symbol'],r['trading_date'],r['session'])].append(r)
    results=[]
    for symbol in ('SNDK','MU','NVDA'):
        dates=sorted({day for s,day,phase in g if s==symbol and phase=='regular'});eligible=0;trades=[]
        for day in dates:
            pre=g.get((symbol,day,'pre'),[]);reg=g.get((symbol,day,'regular'),[])
            if not pre or len(reg)!=390 or not session_complete(aggregate(reg),day,'regular'):continue
            # Coverage measured, not falsely equated with no trading in absent minutes.
            pre_low=min(x['low'] for x in pre);pre_high=max(x['high'] for x in pre)
            eligible+=1; t=replay_day(reg,pre_low,pre_high)
            if t:
                t.update(day=day, pre_low=pre_low, pre_high=pre_high, pre_minutes=len(pre), **evaluate_path(reg,t))
                trades.append(t)
        results.append({'symbol':symbol,'eligible_days':eligible,'trades':trades})
    return {'generated_bj':datetime.now(BJ).isoformat(), 'description':'opening 30m first minute close breaks premarket high/low; next minute open entry; initial opening extreme stop checked 1m; 15m close crosses cumulative regular VWAP proxy => exit next minute; close end session; no trading costs', 'results':results}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,default=DEFAULT_DB);p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'.cache'/'premarket-breakout.json');a=p.parse_args()
    result=run(a.db);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(a.output),'trades':{r['symbol']:len(r['trades']) for r in result['results']}},ensure_ascii=False))

if __name__=='__main__':main()
