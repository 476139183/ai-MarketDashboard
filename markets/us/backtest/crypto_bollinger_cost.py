#!/usr/bin/env python3
"""Strategy06: closed-4h Bollinger reversion, cost-space filter and 2ATR stop.

Research replay only. Input is an immutable normalized OKX data bundle; no live orders.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
from statistics import pstdev

from crypto_replay_engine import Rules,STEP,SMALL,simulate

STRATEGY_ID='crypto-bollinger-cost-v1'
COINS=('BTC','ETH','SOL')
MIN_ROOM=3*2*(.0006+.0005)


def entry_allowed(side,close,middle):
    if side not in (-1,1):return False
    room=side*(middle/close-1)
    return room>=MIN_ROOM or math.isclose(room,MIN_ROOM,rel_tol=1e-12,abs_tol=1e-15)


def wilder(values,n):
    out=[None]*len(values)
    if len(values)<n:return out
    avg=sum(values[:n])/n;out[n-1]=avg
    for i in range(n,len(values)):
        avg=(avg*(n-1)+values[i])/n;out[i]=avg
    return out


def strategy_signals(rows):
    """Return holding state, separate entry eligibility and entry-time risk.

    Key t means all input candles end by t. Filtering entry never changes state exit.
    """
    if any(b['t']-a['t']!=STEP for a,b in zip(rows,rows[1:])):raise ValueError('Non-contiguous closed4h history')
    tr=[];plus=[];minus=[]
    for i,row in enumerate(rows):
        previous=rows[i-1] if i else row
        tr.append(max(row['h']-row['l'],abs(row['h']-previous['c']),abs(row['l']-previous['c'])))
        up=row['h']-previous['h'];down=previous['l']-row['l']
        plus.append(up if up>down and up>0 else 0);minus.append(down if down>up and down>0 else 0)
    atr=wilder(tr,20);atr14=wilder(tr,14);p=wilder(plus,14);m=wilder(minus,14);dx=[]
    for i in range(13,len(rows)):
        di_p=100*p[i]/atr14[i] if atr14[i] else 0;di_m=100*m[i]/atr14[i] if atr14[i] else 0
        dx.append(100*abs(di_p-di_m)/(di_p+di_m) if di_p+di_m else 0)
    adx=[None]*13+wilder(dx,14);closes=[r['c'] for r in rows];state={};entry={};risk={};held=0
    for i,row in enumerate(rows):
        t=row['t']+STEP;trigger=0;middle=None
        if i>=20 and adx[i] is not None:
            current=closes[i-19:i+1];previous=closes[i-20:i];middle=sum(current)/20;old_mid=sum(previous)/20
            lower=middle-2*pstdev(current);upper=middle+2*pstdev(current)
            old_lower=old_mid-2*pstdev(previous);old_upper=old_mid+2*pstdev(previous)
            if held==1 and (row['c']>=middle or adx[i]>=25):held=0
            elif held==-1 and (row['c']<=middle or adx[i]>=25):held=0
            if adx[i]<20:
                if closes[i-1]<old_lower and row['c']>=lower:held=1;trigger=1
                elif closes[i-1]>old_upper and row['c']<=upper:held=-1;trigger=-1
        state[t]=held
        entry[t]=trigger if trigger and entry_allowed(trigger,row['c'],middle) else 0
        risk[t]=dict(stop_pct=min(.04,max(.005,2*atr[i]/row['c'])) if atr[i] is not None else .02,target_pct=None)
    return dict(state=state,entry=entry,risk=risk)


def validate_candles(rows,step,label):
    last=None
    for r in rows:
        stamp=r['t'];prices=[r[k] for k in ('o','h','l','c')]
        if not isinstance(stamp,int) or stamp%step:raise ValueError(label+': invalid timestamp')
        if last is not None and stamp!=last+step:raise ValueError(label+': incomplete or duplicate history')
        if any(not math.isfinite(v) or v<=0 for v in prices):raise ValueError(label+': invalid price')
        if not r['l']<=min(r['o'],r['c'])<=max(r['o'],r['c'])<=r['h']:raise ValueError(label+': invalid OHLC')
        last=stamp


def replay_bundle(bundle,start=None,end=None):
    start=bundle['start'] if start is None else start;end=bundle['end'] if end is None else end
    if start%STEP or end%STEP or start>=end or start<bundle['start'] or end>bundle['end']:raise ValueError('Invalid closed4h replay window')
    fast={};marks={};models={};funding={};specs={};expected=list(range(start,end,SMALL))
    for symbol in COINS:
        if symbol not in bundle['data']:continue
        d=bundle['data'][symbol];fast[symbol]=[r for r in d['fast'] if start<=r['t']<end]
        marks[symbol]=[r for r in d.get('marks',[]) if start<=r['t']<end]
        for name,rows in (('execution',fast[symbol]),('mark',marks[symbol])):
            if [r['t'] for r in rows]!=expected:raise ValueError(symbol+': incomplete '+name+' coverage')
            validate_candles(rows,SMALL,symbol+' '+name)
        slow=[r for r in d['slow'] if r['t']<end];validate_candles(slow,STEP,symbol+' signal')
        if len([r for r in slow if r['t']<start])<60:raise ValueError(symbol+': need60 closed4h warmup bars')
        slow_map={r['t']:r for r in slow};fast_map={r['t']:r for r in fast[symbol]}
        for stamp in range(start,end,STEP):
            if stamp not in slow_map:raise ValueError(symbol+': missing closed4h signal candle')
            children=[fast_map[t] for t in range(stamp,stamp+STEP,SMALL)]
            agg=dict(o=children[0]['o'],h=max(r['h'] for r in children),l=min(r['l'] for r in children),c=children[-1]['c'])
            if any(not math.isclose(agg[k],slow_map[stamp][k],rel_tol=1e-9,abs_tol=1e-8) for k in agg):raise ValueError(symbol+': 5m/4h OHLC mismatch')
        if not d['funding'].get('complete'):raise ValueError(symbol+': incomplete funding history')
        rates={int(t):v for t,v in d['funding']['rates'].items()};stamps=sorted(t for t in rates if start<=t<end)
        if not stamps or stamps[0]-start>8*3600000 or end-stamps[-1]>8*3600000 or any(b-a>8*3600000 for a,b in zip(stamps,stamps[1:])):raise ValueError(symbol+': funding coverage gap')
        if any(t%SMALL or not math.isfinite(rates[t]) for t in stamps):raise ValueError(symbol+': invalid funding settlement')
        specs[symbol]=d['spec']
        if any(float(specs[symbol].get(k,0))<=0 for k in ('ctVal','lotSz','minSz')):raise ValueError(symbol+': invalid contract specification')
        models[symbol]=strategy_signals(slow);funding[symbol]={t:rates[t] for t in stamps}
    if not models:raise ValueError('No BTC/ETH/SOL instruments')
    rules=Rules()
    result=simulate(fast,{s:m['state'] for s,m in models.items()},start,end,rules=rules,specs=specs,marks=marks,funding=funding,
                    entry_signals={s:m['entry'] for s,m in models.items()},risk_schedule={s:m['risk'] for s,m in models.items()})
    for t in result['trades']:
        if t['opening_margin_utilization_pct']>60.000001 or t['initial_margin']>t['equity_at_entry']*.2+1e-7:raise AssertionError('Margin cap exceeded')
    result['equity_curve']=[dict(t=start,equity=rules.initial_balance)]+[p for p in result['equity_curve'] if p['t']%STEP==0 or p['t']==end]
    return dict(strategy=STRATEGY_ID,start=start,end=end,rules=asdict(rules),minimum_signal_room=MIN_ROOM,**result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True,type=Path,help='Normalized immutable OKX JSON bundle (schema in docs/strategies/crypto-bollinger-cost-v1.md)')
    parser.add_argument('--start',type=int,help='Optional UTC epoch milliseconds')
    parser.add_argument('--end',type=int,help='Optional UTC epoch milliseconds')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();raw=args.bundle.read_bytes();result=replay_bundle(json.loads(raw),args.start,args.end)
    result['input_sha256']=hashlib.sha256(raw).hexdigest();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))
    print(f'{STRATEGY_ID}: return={result["return_pct"]:+.4f}%, max_drawdown={result["max_drawdown_pct"]:.4f}%, trades={result["trade_count"]}')
    print('Result:',args.output)

if __name__=='__main__':main()
