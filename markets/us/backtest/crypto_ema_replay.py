#!/usr/bin/env python3
"""Reproducible closed-4h-ema-v1 replay on OKX public perpetual candles."""
from __future__ import annotations
import argparse
from bisect import bisect_left
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'dashboard'))
from contract_radar import ema
from paper_trading import RULES

STEP=14400000
SMALL=300000
COINS=('BTC','ETH','SOL')
CACHE=HERE.parent/'.cache'/'crypto-ema-replay'
_GATE=threading.Lock()
_LAST=0.0

@dataclass(frozen=True)
class Rules:
    initial_balance:float=RULES['initial_balance']
    order_budget:float=RULES['order_budget']
    max_positions:int=RULES['max_positions']
    fee_rate:float=RULES['fee_rate']
    slippage_rate:float=RULES['slippage_rate']
    stop_pct:float=RULES['stop_pct']
    target_pct:float=RULES['target_pct']
    leverage:float=1
    margin_fraction:float|None=None
    margin_scope:str='position'
    max_margin_fraction:float|None=None


def iso(stamp):return datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat()


def request(path,params):
    global _LAST
    url='https://www.okx.com/api/v5/'+path+'?'+urlencode(params)
    for attempt in range(5):
        with _GATE:
            delay=.26-(time.monotonic()-_LAST)
            if delay>0:time.sleep(delay)
            _LAST=time.monotonic()
        try:
            with urlopen(Request(url,headers={'User-Agent':'MarketStudio/1.0','Accept':'application/json'}),timeout=20) as response:
                data=json.load(response)
            if data.get('code')!='0':raise ValueError('OKX '+str(data.get('code'))+': '+str(data.get('msg')))
            return data['data']
        except (OSError,ValueError):
            if attempt==4:raise
            time.sleep(min(2**attempt,8))


def normalize_candles(raw,step,start,end):
    unique={}
    for r in raw:
        if len(r)<9 or r[8]!='1':continue
        stamp=int(r[0])
        if not start<=stamp<end:continue
        values=[float(r[i]) for i in (1,2,3,4,6)]
        if stamp%step or not all(math.isfinite(v) for v in values) or min(values[:4])<=0 or values[4]<0:
            raise ValueError('Invalid candle timestamp/OHLCV')
        o,h,l,c,v=values
        if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid candle high/low')
        row=dict(t=stamp,o=o,h=h,l=l,c=c,v=v)
        if stamp in unique and unique[stamp]!=row:raise ValueError('Conflicting duplicate candle')
        unique[stamp]=row
    expected=list(range(start,end,step))
    if sorted(unique)!=expected:
        missing=sorted(set(expected)-set(unique))
        raise ValueError(f'Incomplete candle window: expected={len(expected)}, actual={len(unique)}, first missing={missing[:3]}')
    return [unique[t] for t in expected]


def download(symbol,bar,step,start,end):
    CACHE.mkdir(parents=True,exist_ok=True)
    path=CACHE/f'{symbol}-{bar}-{start}-{end}.json'
    if path.exists():
        saved=json.loads(path.read_text());rows=normalize_candles(saved['raw'],step,start,end)
        return rows,dict(file=str(path.relative_to(HERE.parents[2])),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),rows=len(rows),cached=True)
    raw=[];cursor=end;pages=0
    while cursor>start:
        batch=request('market/history-candles',dict(instId=symbol+'-USDT-SWAP',bar=bar,limit=300,after=cursor))
        if not batch:break
        oldest=min(int(r[0]) for r in batch)
        if oldest>=cursor:raise ValueError('Non-advancing history cursor')
        raw.extend(batch);cursor=oldest;pages+=1
        if pages%10==0 or cursor<=start:print(f'{symbol} {bar}: {pages} pages, earliest {iso(cursor)}',flush=True)
    rows=normalize_candles(raw,step,start,end)
    path.write_text(json.dumps(dict(source='OKX',endpoint='market/history-candles',collected_at=iso(int(time.time()*1000)),start=start,end=end,raw=raw),separators=(',',':')))
    return rows,dict(file=str(path.relative_to(HERE.parents[2])),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),rows=len(rows),cached=False)


def funding_history(symbol,start,end):
    path=CACHE/f'{symbol}-funding-{start}-{end}.json'
    if path.exists():return json.loads(path.read_text())
    rows={};cursor=end+1
    for _ in range(10):
        batch=request('public/funding-rate-history',dict(instId=symbol+'-USDT-SWAP',limit=400,after=cursor))
        if not batch:break
        oldest=min(int(r['fundingTime']) for r in batch)
        if oldest>=cursor:raise ValueError('Non-advancing funding cursor')
        for r in batch:
            if r.get('realizedRate') in (None,''):raise ValueError('Missing realized funding rate')
            rows[int(r['fundingTime'])]=float(r['realizedRate'])
        cursor=oldest
        if cursor<=start:break
    stamps=sorted(rows)
    result=dict(source='OKX realizedRate',coverage_start=stamps[0] if stamps else None,
                complete=bool(stamps and stamps[0]<=start and stamps[-1]>=end-8*3600000),
                rates={str(t):rows[t] for t in stamps if start<=t<end})
    path.write_text(json.dumps(result,separators=(',',':')))
    return result


def strategy_signals(rows,start,end,history=119):
    stamps=[r['t'] for r in rows];result={}
    for boundary in range(start,end,STEP):
        i=bisect_left(stamps,boundary)
        past=rows[max(0,i-history):i]
        if len(past)<60 or past[-1]['t']+STEP!=boundary or any(b['t']-a['t']!=STEP for a,b in zip(past,past[1:])):
            raise ValueError('Missing completed 4h signal history')
        closes=[r['c'] for r in past];fast=ema(closes,20);slow=ema(closes,50)
        result[boundary]=1 if closes[-1]>fast[-1]>slow[-1] and fast[-1]>fast[-7] else -1 if closes[-1]<fast[-1]<slow[-1] and fast[-1]<fast[-7] else 0
    return result


def daily_trend_signals(rows,start,end):
    days={}
    for row in rows:
        day=row['t']//86400000*86400000
        days.setdefault(day,[]).append(row)
    completed=[]
    for day,children in sorted(days.items()):
        children=sorted(children,key=lambda r:r['t'])
        if [r['t'] for r in children]==list(range(day,day+86400000,STEP)):
            completed.append(dict(t=day+86400000,c=children[-1]['c']))
    result={}
    for boundary in range(start,end,STEP):
        closes=[r['c'] for r in completed if r['t']<=boundary]
        if len(closes)<60:raise ValueError('Daily trend filter requires 60 completed days')
        m20=sum(closes[-20:])/20;m60=sum(closes[-60:])/60;old20=sum(closes[-25:-5])/20
        result[boundary]=1 if closes[-1]>m20>m60 and m20>old20 else -1 if closes[-1]<m20<m60 and m20<old20 else 0
    return result


def simulate(candles,signals,start,end,*,rules=None,window=STEP,specs=None,funding=None,marks=None,entry_signals=None,risk_schedule=None):
    rules=rules or Rules();specs=specs or {};funding=funding or {}
    if rules.leverage<=0 or (rules.margin_fraction is not None and not 0<rules.margin_fraction<=1):raise ValueError('Invalid leverage/margin fraction')
    if rules.margin_scope not in ('position','portfolio'):raise ValueError('Invalid margin scope')
    if rules.max_margin_fraction is not None and not 0<rules.max_margin_fraction<=1:raise ValueError('Invalid aggregate margin cap')
    entry_signals=entry_signals if entry_signals is not None else signals
    risk_schedule=risk_schedule or {}
    marks=marks or {};mark_maps={s:{r['t']:r for r in rows} for s,rows in marks.items()}
    symbols=list(candles);maps={s:{r['t']:r for r in rows} for s,rows in candles.items()}
    cash=rules.initial_balance;positions={};trades=[];observed={};used=set();curve=[]
    ambiguous=0;max_positions=0;max_margin_use=0;max_gross_leverage=0;peak=rules.initial_balance;drawdown=0
    def close(symbol,reference,stamp,reason):
        nonlocal cash
        p=positions.pop(symbol);exit_price=reference*(1-p['side']*rules.slippage_rate)
        gross=p['side']*(exit_price-p['entry_price'])*p['quantity']
        exit_fee=exit_price*p['quantity']*rules.fee_rate
        cash+=p['margin']+gross-exit_fee
        trade=dict(p,exit_at=stamp,exit_reference=reference,exit_price=exit_price,exit_reason=reason,
                   fees=p['entry_fee']+exit_fee,gross_pnl=gross,net_pnl=gross-p['entry_fee']-exit_fee-p['funding_cost'])
        trade['slippage_cost']=p['quantity']*(abs(p['entry_price']-p['entry_reference'])+abs(exit_price-reference))
        trades.append(trade)
    def mark_at(symbol,row):return mark_maps.get(symbol,{}).get(row['t'],row)
    def guard_liquidation(symbol,price):
        if rules.leverage<=1:return
        p=positions[symbol];side=p['side'];quantity=p['quantity']
        mmr=float(specs.get(symbol,{}).get('mmr',.004))
        remaining=p['margin']+side*quantity*(price-p['entry_price'])
        required=quantity*price*(mmr+rules.fee_rate)
        if remaining<=required:
            raise ValueError(f'{symbol}: liquidation path uncertain at {iso(stamp)}; mark={price}, side={side}, initial_margin={p["initial_margin"]}, remaining={remaining}, required={required}; finer execution/mark data required')
    def intrabar(symbol,r):
        nonlocal ambiguous
        p=positions[symbol];side=p['side']
        mark=mark_at(symbol,r)
        guard_liquidation(symbol,mark['l'] if side==1 else mark['h'])
        hit_stop=r['l']<=p['stop_price'] if side==1 else r['h']>=p['stop_price']
        hit_target=p['target_price'] is not None and (r['h']>=p['target_price'] if side==1 else r['l']<=p['target_price'])
        if hit_stop and hit_target:ambiguous+=1
        if hit_stop or hit_target:
            close(symbol,p['stop_price'] if hit_stop else p['target_price'],r['t']+SMALL-1,'stop_loss' if hit_stop else 'take_profit')
            return True
        return False
    for stamp in range(start,end,SMALL):
        rows={s:maps[s][stamp] for s in symbols};closed=set();boundary=stamp//window*window
        for symbol in list(positions):
            r=rows[symbol];p=positions[symbol];side=p['side']
            rate=funding.get(symbol,{}).get(stamp)
            if rate is not None and p['entry_at']<stamp:
                cost=side*p['quantity']*mark_at(symbol,r)['o']*rate;p['margin']-=cost;p['funding_cost']+=cost
            guard_liquidation(symbol,mark_at(symbol,r)['o'])
            reason=None
            if side*(r['o']-p['stop_price'])<=0:reason='stop_loss'
            elif p['target_price'] is not None and side*(r['o']-p['target_price'])>=0:reason='take_profit'
            elif signals[symbol].get(boundary)!=side:reason='signal_invalid'
            if reason:close(symbol,r['o'],stamp,reason);closed.add(symbol)
        for symbol in symbols:
            direction=entry_signals[symbol].get(boundary,0)
            if direction!=signals[symbol].get(boundary,0):continue
            if not direction or symbol in closed:continue
            key=(symbol,boundary,direction)
            if key not in observed:observed[key]=stamp;continue
            if key in used or stamp<=observed[key] or symbol in positions or len(positions)>=rules.max_positions:continue
            r=rows[symbol];entry=r['o']*(1+direction*rules.slippage_rate)
            spec=specs.get(symbol,{})
            ct=Decimal(str(spec.get('ctVal',1)));lot=Decimal(str(spec.get('lotSz','0.00000001')))
            equity_at_entry=cash+sum(p['margin']+p['side']*p['quantity']*(mark_at(s,rows[s])['o']-p['entry_price']) for s,p in positions.items())
            fraction=rules.margin_fraction/(len(symbols) if rules.margin_scope=='portfolio' else 1) if rules.margin_fraction is not None else None
            budget=equity_at_entry*fraction*rules.leverage if fraction is not None else rules.order_budget
            if rules.max_margin_fraction is not None:
                existing_margin=sum(p['margin'] for p in positions.values())
                mark_open=mark_at(symbol,r)['o']
                immediate_equity_cost_per_margin=rules.leverage*(rules.fee_rate+direction*(entry-mark_open)/entry)
                denominator=1+rules.max_margin_fraction*immediate_equity_cost_per_margin
                if denominator<=0:raise ValueError('Invalid opening cap denominator')
                allowance=max(0,(rules.max_margin_fraction*equity_at_entry-existing_margin)/denominator)
                budget=min(budget,allowance*rules.leverage)
            contracts=(Decimal(str(budget))/Decimal(str(entry))/ct/lot).to_integral_value(rounding=ROUND_FLOOR)*lot
            if contracts<Decimal(str(spec.get('minSz',lot))):continue
            quantity=float(contracts*ct);notional=quantity*entry;fee=notional*rules.fee_rate
            margin=notional/rules.leverage
            if cash<margin+fee:continue
            if 'tier_maxSz' in spec and contracts>Decimal(str(spec['tier_maxSz'])):raise ValueError('Position exceeds verified maintenance tier')
            risk=risk_schedule.get(symbol,{}).get(boundary,{})
            stop_pct=float(risk.get('stop_pct',rules.stop_pct));target_pct=risk.get('target_pct',rules.target_pct)
            if not 0<stop_pct<1 or (target_pct is not None and target_pct<=0):raise ValueError('Invalid stop/target')
            positions[symbol]=dict(symbol=symbol,side=direction,signal_at=boundary,observed_at=observed[key],entry_at=stamp,
                entry_reference=r['o'],entry_price=entry,quantity=quantity,contracts=float(contracts),notional=notional,margin=margin,initial_margin=margin,leverage=rules.leverage,equity_at_entry=equity_at_entry,
                entry_fee=fee,stop_price=entry*(1-direction*stop_pct),target_price=None if target_pct is None else entry*(1+direction*target_pct),funding_cost=0)
            cash-=margin+fee;used.add(key);max_positions=max(max_positions,len(positions))
            after_equity=cash+sum(p['margin']+p['side']*p['quantity']*(mark_at(s,rows[s])['o']-p['entry_price']) for s,p in positions.items())
            positions[symbol]['opening_margin_utilization_pct']=sum(p['margin'] for p in positions.values())/after_equity*100
        # Only after opening-time decisions may this bar's future high/low release cash or capacity.
        for symbol in list(positions):intrabar(symbol,rows[symbol])
        equity=cash+sum(p['margin']+p['side']*p['quantity']*(mark_at(s,rows[s])['c']-p['entry_price']) for s,p in positions.items())
        if equity<=0:raise ValueError('Non-positive account equity')
        max_margin_use=max(max_margin_use,sum(p['margin'] for p in positions.values())/equity*100)
        max_gross_leverage=max(max_gross_leverage,sum(p['quantity']*mark_at(s,rows[s])['c'] for s,p in positions.items())/equity)
        peak=max(peak,equity);drawdown=max(drawdown,(peak-equity)/peak)
        curve.append(dict(t=stamp+SMALL,equity=equity))
    for symbol in list(positions):close(symbol,maps[symbol][end-SMALL]['c'],end-1,'end_of_test')
    curve[-1]['equity']=cash;peak=max(peak,cash);drawdown=max(drawdown,(peak-cash)/peak)
    pnl=sum(t['net_pnl'] for t in trades)
    if not math.isclose(cash,rules.initial_balance+pnl,abs_tol=1e-7):raise AssertionError('Account cash does not reconcile')
    wins=[t['net_pnl'] for t in trades if t['net_pnl']>0];losses=[t['net_pnl'] for t in trades if t['net_pnl']<=0]
    return dict(initial_balance=rules.initial_balance,final_equity=cash,net_profit=pnl,return_pct=(cash/rules.initial_balance-1)*100,
        max_drawdown_pct=drawdown*100,trades=trades,trade_count=len(trades),winning_trades=len(wins),
        win_rate_pct=len(wins)/len(trades)*100 if trades else 0,profit_factor=sum(wins)/abs(sum(losses)) if sum(losses) else None,
        fees=sum(t['fees'] for t in trades),funding_cost=sum(t['funding_cost'] for t in trades),
        slippage_cost=sum(t['slippage_cost'] for t in trades),ambiguous_bars=ambiguous,max_positions=max_positions,max_margin_utilization_pct=max_margin_use,max_gross_leverage=max_gross_leverage,equity_curve=curve)


def buy_and_hold(candles,start,end,*,rules=None,specs=None,funding=None):
    rules=rules or Rules();specs=specs or {};funding=funding or {};trades=[]
    for symbol,rows in candles.items():
        spec=specs.get(symbol,{});ct=Decimal(str(spec.get('ctVal',1)));lot=Decimal(str(spec.get('lotSz','0.00000001')))
        entry=rows[0]['o']*(1+rules.slippage_rate)
        contracts=(Decimal(str(rules.order_budget))/Decimal(str(entry))/ct/lot).to_integral_value(rounding=ROUND_FLOOR)*lot
        quantity=float(contracts*ct);exit_price=rows[-1]['c']*(1-rules.slippage_rate)
        price_map={r['t']:r['o'] for r in rows}
        funding_cost=sum(quantity*price_map[t]*rate for t,rate in funding.get(symbol,{}).items() if start<t<end)
        fees=quantity*(entry+exit_price)*rules.fee_rate
        pnl=quantity*(exit_price-entry)-fees-funding_cost
        trades.append(dict(symbol=symbol,quantity=quantity,entry_price=entry,exit_price=exit_price,funding_cost=funding_cost,fees=fees,net_pnl=pnl))
    profit=sum(t['net_pnl'] for t in trades)
    # Same nominal allocation as the strategy: 1,000 per coin, remaining cash idle.
    balance=rules.initial_balance;curve=[];paid={s:0 for s in candles}
    for i,r in enumerate(next(iter(candles.values()))):
        stamp=r['t'];value=balance
        for trade in trades:
            symbol=trade['symbol'];row=candles[symbol][i];qty=trade['quantity']
            if stamp>start:paid[symbol]+=qty*row['o']*funding.get(symbol,{}).get(stamp,0)
            value+=qty*(row['c']-trade['entry_price'])-qty*trade['entry_price']*rules.fee_rate-paid[symbol]
        curve.append(dict(t=stamp+SMALL,equity=value))
    curve[-1]['equity']=balance+profit
    return dict(net_profit=profit,return_pct=profit/balance*100,final_equity=balance+profit,trades=trades,equity_curve=curve)


def evaluate_bundle(bundle):
    start,end=bundle['start'],bundle['end'];data=bundle['data'];audit={}
    for symbol,d in data.items():
        fast={r['t']:r for r in d['fast']};slow={r['t']:r for r in d['slow']}
        mismatches=[]
        for stamp in range(start,end,STEP):
            children=[fast[t] for t in range(stamp,stamp+STEP,SMALL)]
            values=dict(o=children[0]['o'],h=max(r['h'] for r in children),l=min(r['l'] for r in children),c=children[-1]['c'])
            if any(not math.isclose(values[k],slow[stamp][k],rel_tol=1e-9,abs_tol=1e-8) for k in values):mismatches.append(stamp)
        rates=d['funding']['rates'];stamps=sorted(map(int,rates))
        if mismatches:raise ValueError(f'{symbol}: 5m/4h OHLC disagreement')
        if not d['funding']['complete']:raise ValueError(f'{symbol}: funding coverage incomplete; cannot label full funding-adjusted returns')
        audit[symbol]=dict(execution_bars=len(d['fast']),signal_bars=len(d['slow']),checked_4h_bars=(end-start)//STEP,
            ohlc_mismatches=mismatches,funding_events=len(rates),funding_first=iso(stamps[0]),funding_last=iso(stamps[-1]),
            funding_max_gap_hours=max(b-a for a,b in zip(stamps,stamps[1:]))/3600000,spec=d['spec'],files=d['audit'])
    signals={s:strategy_signals(d['slow'],start,end) for s,d in data.items()}
    specs={s:d['spec'] for s,d in data.items()}
    funding={s:{int(t):rate for t,rate in d['funding']['rates'].items()} for s,d in data.items()}
    accounts={}
    for name,symbols in [(s,[s]) for s in COINS]+[('COMBINED',list(COINS))]:
        inputs={s:data[s]['fast'] for s in symbols};sigs={s:signals[s] for s in symbols}
        accounts[name]=dict(funded=simulate(inputs,sigs,start,end,specs=specs,funding=funding),
            unfunded=simulate(inputs,sigs,start,end,specs=specs),
            stress=simulate(inputs,sigs,start,end,specs=specs,funding=funding,rules=Rules(fee_rate=2*Rules().fee_rate,slippage_rate=2*Rules().slippage_rate)),
            benchmark=buy_and_hold(inputs,start,end,specs=specs,funding=funding),
            price_change_pct={s:(data[s]['fast'][-1]['c']/data[s]['fast'][0]['o']-1)*100 for s in symbols})
    return dict(start=start,end=end,generated_at=iso(int(time.time()*1000)),strategy=RULES['strategy'],
                source='OKX USDT linear perpetuals',rules=asdict(Rules()),source_audit=audit,accounts=accounts)


def window(now):
    end=now//STEP*STEP;dt=datetime.fromtimestamp(end/1000,timezone.utc)
    month=dt.month-3;year=dt.year
    if month<1:month+=12;year-=1
    start=int(dt.replace(year=year,month=month,day=min(dt.day,monthrange(year,month)[1])).timestamp()*1000)
    return start,end


def collect(start,end):
    warm=start-120*STEP
    def one(symbol):
        fast,fast_audit=download(symbol,'5m',SMALL,start,end)
        slow,slow_audit=download(symbol,'4H',STEP,warm,end)
        funding=funding_history(symbol,start,end)
        spec=request('public/instruments',dict(instType='SWAP',instId=symbol+'-USDT-SWAP'))[0]
        print(f'{symbol}: validated {len(fast)} 5m, {len(slow)} 4h, funding complete={funding["complete"]}',flush=True)
        return symbol,dict(fast=fast,slow=slow,funding=funding,spec={k:spec[k] for k in ('ctVal','lotSz','minSz')},audit=dict(execution=fast_audit,signal=slow_audit))
    with ThreadPoolExecutor(max_workers=3) as pool:return dict(pool.map(one,COINS))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-only',action='store_true')
    parser.add_argument('--end',help='UTC ISO end, aligned down to closed 4h boundary')
    parser.add_argument('--bundle',type=Path,help='Replay a previously validated local bundle without network')
    parser.add_argument('--output',type=Path,help='Report directory')
    args=parser.parse_args()
    if args.bundle:
        bundle_path=args.bundle;bundle=json.loads(bundle_path.read_text())
    else:
        now=int(datetime.fromisoformat(args.end).timestamp()*1000) if args.end else int(time.time()*1000)
        start,end=window(now);print(f'Window {iso(start)} -> {iso(end)}, {(end-start)/86400000:g} days',flush=True)
        data=collect(start,end)
        bundle_path=CACHE/f'bundle-{start}-{end}.json'
        bundle=dict(start=start,end=end,collected_at=iso(int(time.time()*1000)),data=data)
        bundle_path.write_text(json.dumps(bundle,separators=(',',':')))
        print('Validated bundle:',bundle_path,flush=True)
    if args.download_only:return
    result=evaluate_bundle(bundle)
    result['source_bundle']=str(bundle_path)
    result['source_bundle_sha256']=hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    from crypto_replay_report import write_report
    output=args.output or HERE.parents[2]/'docs'/'reports'/('crypto-ema-'+iso(bundle['end'])[:10])
    write_report(result,output)
    for name,row in result['accounts'].items():
        r=row['funded'];print(f'{name}: return={r["return_pct"]:.4f}%, PnL={r["net_profit"]:.4f}, trades={r["trade_count"]}, drawdown={r["max_drawdown_pct"]:.4f}%')
    print('Report:',output/'report.md')

if __name__=='__main__':main()
