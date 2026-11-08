#!/usr/bin/env python3
"""Run the predeclared 42-model crypto experiment ledger without live-account writes."""
import argparse
from dataclasses import asdict,replace
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import time

from crypto_ema_replay import CACHE,COINS,STEP,SMALL,Rules,strategy_signals,daily_trend_signals,simulate,iso
from crypto_strategy_experiments import strategy_catalog,factor_features,apply_factor
from crypto_replay_report import write_csv

VERSION='isolated-funding-hard-opening-cap-v2'
GATES=('flow','oi','volume','turnover','poc','crowding','combined')


def variants(data,start,end):
    result={};features={s:factor_features(d,start,end) for s,d in data.items()}
    original={s:strategy_signals(d['slow'],start,end) for s,d in data.items()}
    bias={s:daily_trend_signals(d['slow'],start,end) for s,d in data.items()}
    for name in ('ema_original','ema_reversed','ema_daily_filter'):
        result[name]={}
        for s in COINS:
            signal=original[s] if name=='ema_original' else {t:-v for t,v in original[s].items()} if name=='ema_reversed' else {t:v if v==bias[s][t] else 0 for t,v in original[s].items()}
            result[name][s]=dict(state=signal,entry=dict(signal),risk={})
    catalogs={s:strategy_catalog(d['slow']) for s,d in data.items()}
    for family in catalogs['BTC']:
        for exits in ('fixed','atr'):
            name=family+'_'+exits
            result[name]={s:dict(catalogs[s][family],risk=catalogs[s][family]['risk'] if exits=='atr' else {}) for s in COINS}
    for seed in ('ema_daily_filter','donchian_20_10_atr','momentum_42_atr'):
        for gate in GATES:result[seed+'+'+gate]={s:apply_factor(result[seed][s],features[s],gate) for s in COINS}
    if len(result)!=42:raise AssertionError('Predeclared model count changed')
    return result,features


def scenario_inputs(data,start,end):
    fast={s:[r for r in d['fast'] if start<=r['t']<end] for s,d in data.items()}
    marks={s:[r for r in d['marks'] if start<=r['t']<end] for s,d in data.items()}
    expected=list(range(start,end,SMALL))
    for s in COINS:
        if [r['t'] for r in fast[s]]!=expected or [r['t'] for r in marks[s]]!=expected:raise ValueError('Incomplete execution/mark coverage')
    funding={s:{int(t):rate for t,rate in d['funding']['rates'].items() if start<=int(t)<end} for s,d in data.items()}
    return fast,marks,funding


def run_model(model,data,inputs,start,end,rules,symbols):
    fast,marks,funding=inputs
    result=simulate({s:fast[s] for s in symbols},{s:model[s]['state'] for s in symbols},start,end,rules=rules,
        specs={s:data[s]['spec'] for s in symbols},funding={s:funding[s] for s in symbols},marks={s:marks[s] for s in symbols},
        entry_signals={s:model[s]['entry'] for s in symbols},risk_schedule={s:model[s]['risk'] for s in symbols})
    for trade in result['trades']:
        if trade['opening_margin_utilization_pct']>60.000001:raise AssertionError('Opening-time margin cap violated')
        if trade['initial_margin']/trade['equity_at_entry']>.20000001:raise AssertionError('Per-entry margin exceeded20%')
        if abs(trade['notional']/trade['initial_margin']-10)>1e-9:raise AssertionError('Incorrect leverage')
    return result


def compact(result):
    output={k:v for k,v in result.items() if k not in ('trades','equity_curve')}
    output['long_trades']=sum(t['side']==1 for t in result['trades']);output['short_trades']=sum(t['side']==-1 for t in result['trades'])
    output['long_pnl']=sum(t['net_pnl'] for t in result['trades'] if t['side']==1)
    output['short_pnl']=sum(t['net_pnl'] for t in result['trades'] if t['side']==-1)
    return output


def run(bundle_path,output):
    started=time.monotonic();payload=json.loads(bundle_path.read_text());data=payload['data'];start,end=payload['start'],payload['end']
    output.mkdir(parents=True,exist_ok=True)
    models,features=variants(data,start,end)
    # Persist formulas/features before evaluating any candidate returns.
    definitions={name:dict(entry_family=name.split('+')[0],factor=name.split('+')[1] if '+' in name else None) for name in models}
    (output/'definitions.json').write_text(json.dumps(dict(engine=VERSION,models=definitions),ensure_ascii=False,indent=2))
    feature_rows=[dict(symbol=s,t=t,decision_utc=iso(t),**f) for s,rows in features.items() for t,f in rows.items()]
    write_csv(output/'features.csv',feature_rows)
    sep=int(datetime(2026,9,7,8,tzinfo=timezone.utc).timestamp()*1000);october=int(datetime(2026,10,7,8,tzinfo=timezone.utc).timestamp()*1000)
    windows=dict(full=(start,end),development_1=(start,sep),development_2=(sep,october),review_month=(october,end))
    inputs={name:scenario_inputs(data,a,b) for name,(a,b) in windows.items()}
    rules=Rules(leverage=10,margin_fraction=.2,max_margin_fraction=.6)
    ledger=[];lookup={}
    for index,(name,model) in enumerate(models.items()):
        for window,(a,b) in windows.items():
            for account,symbols in [(s,[s]) for s in COINS]+[('COMBINED',list(COINS))]:
                row=dict(model=name,window=window,account=account,start=a,end=b)
                try:
                    result=run_model(model,data,inputs[window],a,b,rules,symbols)
                    row.update(status='ok',**compact(result))
                except ValueError as exc:
                    row.update(status='path_uncertain' if 'liquidation' in str(exc) else 'error',error=str(exc))
                ledger.append(row);lookup[(name,window,account)]=row
        recent=lookup[name,'review_month','COMBINED'];full=lookup[name,'full','COMBINED']
        print(f'{index+1}/42 {name}: full={full.get("return_pct",full["status"])}, recent={recent.get("return_pct",recent["status"])}',flush=True)
    # Every candidate remains in the ledger, including unresolved5m liquidation paths.
    fields=list(dict.fromkeys(key for row in ledger for key in row))
    write_csv(output/'experiments.csv',[{key:row.get(key) for key in fields} for row in ledger])
    eligible=[];ranking=[]
    for name in models:
        a=lookup[name,'development_1','COMBINED'];b=lookup[name,'development_2','COMBINED']
        valid=a['status']==b['status']=='ok'
        passed=valid and min(a['return_pct'],b['return_pct'])>0 and min(a['trade_count'],b['trade_count'])>=5 and max(a['max_drawdown_pct'],b['max_drawdown_pct'])<=30
        score=min(a['return_pct'],b['return_pct']) if valid else None
        row=dict(model=name,development_eligible=passed,min_development_return=score,
                 max_development_drawdown=max(a['max_drawdown_pct'],b['max_drawdown_pct']) if valid else None,
                 development_1=a,development_2=b,review_month=lookup[name,'review_month','COMBINED'],full=lookup[name,'full','COMBINED'])
        ranking.append(row)
        if passed:eligible.append(row)
    sortkey=lambda row:(-row['min_development_return'],row['max_development_drawdown'])
    eligible.sort(key=sortkey)
    diagnostic=sorted((r for r in ranking if r['min_development_return'] is not None),key=sortkey)
    recent_ok=[name for name in models if lookup[name,'review_month','COMBINED']['status']=='ok']
    recent_best=max(recent_ok,key=lambda name:lookup[name,'review_month','COMBINED']['return_pct']) if recent_ok else None
    chosen=list(dict.fromkeys(['ema_original','ema_reversed','ema_daily_filter']+[r['model'] for r in eligible[:3]]+([diagnostic[0]['model']] if not eligible and diagnostic else [])+([recent_best] if recent_best else [])))
    details={};trades=[];stress=[]
    for name in chosen:
        details[name]={}
        for window in ('full','review_month'):
            a,b=windows[window]
            if lookup[name,window,'COMBINED']['status']!='ok':continue
            r=run_model(models[name],data,inputs[window],a,b,rules,list(COINS))
            sample=[dict(t=a,equity=10000)]+[p for p in r['equity_curve'] if p['t']%STEP==0 or p is r['equity_curve'][-1]]
            details[name][window]=dict(metrics=compact(r),equity=sample,trades=r['trades'])
            trades.extend(dict(model=name,window=window,**t) for t in r['trades'])
            try:
                doubled=run_model(models[name],data,inputs[window],a,b,replace(rules,fee_rate=rules.fee_rate*2,slippage_rate=rules.slippage_rate*2),list(COINS))
                stress.append(dict(model=name,window=window,status='ok',**compact(doubled)))
            except ValueError as exc:stress.append(dict(model=name,window=window,status='path_uncertain',error=str(exc)))
    if trades:
        fields=list(dict.fromkeys(k for t in trades for k in t));write_csv(output/'selected-trades.csv',[{k:t.get(k) for k in fields} for t in trades])
    source_audit={s:dict(fast_bars=len(d['fast']),slow_bars=len(d['slow']),mark_bars=len(d['marks']),stats={k:len(v) for k,v in d['stats'].items()},
        taker_checks=d['audit']['taker_crosschecks'],taker_mismatch_count=len(d['audit']['taker_mismatches']),
        taker_above_1pct=sum(r['relative_error']>.01 for r in d['audit']['taker_mismatches']),
        taker_max_error_pct=max([r['relative_error']*100 for r in d['audit']['taker_mismatches']] or [0]),
        source_files=d['audit']) for s,d in data.items()}
    summary=dict(engine=VERSION,start=start,end=end,rules=asdict(rules),windows=windows,candidate_count=len(models),run_count=len(ledger),
        successful_runs=sum(r['status']=='ok' for r in ledger),unresolved_runs=[r for r in ledger if r['status']!='ok'],
        selection_rule='both development returns>0, each trades>=5, each drawdown<=30%; rank worst development return, then drawdown',
        eligible=eligible,development_ranking=diagnostic,recent_month_best_diagnostic=recent_best,details=details,cost_stress=stress,
        source_bundle=str(bundle_path),source_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),data_audit=source_audit,
        ledger=ledger,elapsed_seconds=time.monotonic()-started,
        prices={s:[dict(time=r['t'],open=r['o'],high=r['h'],low=r['l'],close=r['c']) for r in d['slow'] if start<=r['t']<end] for s,d in data.items()})
    (output/'results.json').write_text(json.dumps(summary,ensure_ascii=False,separators=(',',':'),allow_nan=False))
    print('ELIGIBLE',[(r['model'],r['min_development_return'],r['review_month'].get('return_pct')) for r in eligible],flush=True)
    print('RECENT_BEST_DIAGNOSTIC',recent_best,flush=True)
    print('DONE',len(ledger),'runs',time.monotonic()-started,'seconds',flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,default=CACHE/'experiments-bundle-1786089600000-1794038400000.json')
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[3]/'docs'/'reports'/'crypto-experiments-2026-11-07')
    args=parser.parse_args();run(args.bundle,args.output)

if __name__=='__main__':main()
