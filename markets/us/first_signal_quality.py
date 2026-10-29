#!/usr/bin/env python3
"""Audit first simulated signal each day against later intraday price paths."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from research_context import BJ, DEFAULT_DB
from intraminute_hold import compute_asof

HERE=Path(__file__).resolve().parent


def evaluate_path(rows, trade, target_pct=2):
    entry=trade['entry']; long=trade['side']=='做多'; stop=trade['stop']
    # A stop can occur within the exit minute; its high/low order is unknowable.
    stop_exit=trade.get('reason')=='结构止损'
    relevant=[b for b in rows if trade['entry_t']<=b['timestamp'] and (b['timestamp']<trade['exit_t'] or (stop_exit and b['timestamp']==trade['exit_t']))]
    first=None; outcome='neither'; best=0.0; worst=0.0
    for b in relevant:
        fav=(b['high']/entry-1)*100 if long else (entry-b['low'])/entry*100
        adv=(b['low']/entry-1)*100 if long else (entry-b['high'])/entry*100
        stop_hit=b['low']<=stop if long else b['high']>=stop
        target_hit=fav>=target_pct
        if stop_hit:
            if first is None:
                outcome='ambiguous_stop_first' if target_hit else 'stop_first'
            break
        best=max(best,fav);worst=min(worst,adv)
        if target_hit and first is None:
            first=b['timestamp']; outcome='target_first'
    return {'outcome':outcome,'first_target_t':first,'max_favorable_pct':round(best,4),'max_adverse_pct':round(worst,4)}


def forward_direction(rows, trade, horizons=(15,30,60)):
    by_time={r['timestamp']:r for r in rows}
    sign=1 if trade['side']=='做多' else -1
    result={}
    for minutes in horizons:
        # Close of the final minute ending at entry + horizon.
        row=by_time.get(trade['entry_t']+(minutes-1)*60)
        result[str(minutes)]=round(sign*(row['close']/trade['entry']-1)*100,4) if row else None
    return result


def run(db=DEFAULT_DB, source=HERE/'.cache'/'intraminute-hold.json'):
    data=json.loads(source.read_text()); firsts=[]
    for item in data['results']:
        if item['symbol'] not in ('SNDK','MU','NVDA'):continue
        seen=set()
        for t in item['trades']:
            if t['day'] not in seen:
                firsts.append((item['symbol'],t));seen.add(t['day'])
    with closing(sqlite3.connect(f'file:{db}?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        records=[]
        for symbol,t in firsts:
            rows=conn.execute("select * from bars where symbol=? and trading_date=? and session='regular' and source='Massive historical aggregates' order by timestamp",(symbol,t['day'])).fetchall()
            prior=conn.execute("select trading_date from bars where symbol=? and trading_date<? and session='regular' and source='Massive historical aggregates' order by trading_date desc limit 1",(symbol,t['day'])).fetchone()
            if not prior:continue
            prior_bars=conn.execute("select high,low from bars where symbol=? and trading_date=? and session='regular' and source='Massive historical aggregates'",(symbol,prior[0])).fetchall()
            atr=max(x['high'] for x in prior_bars)-min(x['low'] for x in prior_bars)
            hist=[x for x in rows if x['timestamp']<=t['signal_t']]
            signal=compute_asof(hist,atr)
            score=(signal.get('short_trend') or {}).get('score',0)
            direction_score=score if t['side']=='做多' else -score
            records.append({'symbol':symbol,'day':t['day'],'side':t['side'],'direction_score':direction_score,
                'entry_t':t['entry_t'],'exit_t':t['exit_t'],'entry':t['entry'],'stop':t['stop'],
                'exit_pnl_pct':t['pnl_pct'],'exit_reason':t['reason'], 'forward_pct':forward_direction(rows,t), 'holding_minutes':round((t['exit_t']-t['entry_t'])/60, 1), **evaluate_path(rows,t)})
    return {'generated_bj':datetime.now(BJ).isoformat(),'target_pct':2,'records':records,
        'method':'first executed simulated trade per symbol per day; target touched before stop or structure exit; minute OHLC ambiguous stop+target treated as stop first; trend score at signal time'}


def main():
    r=run();out=HERE/'.cache'/'first-signal-quality.json';out.write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps({'output':str(out),'records':len(r['records'])},ensure_ascii=False))

if __name__=='__main__':main()
