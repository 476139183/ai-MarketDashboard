#!/usr/bin/env python3
"""Audit each selected trade and compare six frozen, single-change adjustments."""
import argparse
from bisect import bisect_left
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
from statistics import pstdev

from crypto_ema_replay import Rules,STEP,SMALL,COINS,iso
from crypto_experiment_suite import scenario_inputs,run_model,compact
from crypto_strategy_experiments import indicators,daily_sma_bias,strategy_catalog
from crypto_replay_report import write_csv

LABELS=dict(original='原布林20＋ATR2',room_positive='中轨前才入场',cost_room='回归空间≥三倍成本',adx_not_rising='ADX不增强',daily_aligned='日线同向',atr_1_5='ATR1.5止损',atr_2_5='ATR2.5止损')


def allow_entry(name,side,snapshot):
    if name=='room_positive':return snapshot['room']>0
    if name=='cost_room':return snapshot['room']>=3*2*(.0006+.0005)
    if name=='adx_not_rising':return snapshot['adx_old'] is not None and snapshot['adx']<=snapshot['adx_old']
    if name=='daily_aligned':return snapshot['daily']==side
    raise ValueError('Unknown adjustment')


def snapshots(rows):
    ind=indicators(rows);daily=daily_sma_bias(rows);out={}
    closes=[r['c'] for r in rows]
    for i,r in enumerate(rows):
        if i<20 or ind['adx'][i] is None:continue
        now=closes[i-19:i+1];old=closes[i-20:i];mid=sum(now)/20;old_mid=sum(old)/20
        low=mid-2*pstdev(now);high=mid+2*pstdev(now);old_low=old_mid-2*pstdev(old);old_high=old_mid+2*pstdev(old)
        trigger=1 if ind['adx'][i]<20 and closes[i-1]<old_low and r['c']>=low else -1 if ind['adx'][i]<20 and closes[i-1]>old_high and r['c']<=high else 0
        t=r['t']+STEP
        out[t]=dict(signal_bar=r['t'],close=r['c'],middle=mid,lower=low,upper=high,previous_close=closes[i-1],previous_lower=old_low,previous_upper=old_high,
                    independent_trigger=trigger,adx=ind['adx'][i],adx_old=ind['adx'][i-3],atr=ind['atr'][i],daily=daily[t])
    return out


def adjusted_models(rows,seed):
    views=snapshots(rows);out={'original':seed}
    for name in ('room_positive','cost_room','adx_not_rising','daily_aligned'):
        entry={}
        for t,side in seed['entry'].items():
            snap=views.get(t)
            f=dict(snap,room=side*(snap['middle']/snap['close']-1)) if snap else None
            entry[t]=side if side and f and allow_entry(name,side,f) else 0
        out[name]=dict(state=dict(seed['state']),entry=entry,risk=dict(seed['risk']))
    for multiplier,name in ((1.5,'atr_1_5'),(2.5,'atr_2_5')):
        risk={t:dict(stop_pct=min(.04,max(.005,multiplier*views[t]['atr']/views[t]['close'])) if t in views else r['stop_pct'],target_pct=None) for t,r in seed['risk'].items()}
        out[name]=dict(state=dict(seed['state']),entry=dict(seed['entry']),risk=risk)
    return out


def audit_trade(trade,data,model,rules,end,window=STEP):
    """Reconstruct fills, first exit, funding and excursions independently of simulate."""
    t=trade;side=t['side'];issues=[];fast={r['t']:r for r in data['fast']};marks={r['t']:r for r in data['marks']}
    def check(name,actual,expected):
        if isinstance(expected,(int,float)) and not isinstance(expected,bool):
            ok=math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-7)
        else:ok=actual==expected
        if not ok:issues.append(name)
    check('entry_delay',t['entry_at']>=t['observed_at']+SMALL,True)
    check('signal_time',t['observed_at']>=t['signal_at'],True)
    check('entry_boundary',t['entry_at']//window*window,t['signal_at'])
    check('entry_signal',model['entry'].get(t['signal_at']),side)
    check('holding_signal',model['state'].get(t['signal_at']),side)
    reference=fast[t['entry_at']]['o'];entry=reference*(1+side*rules.slippage_rate)
    check('entry_reference',t['entry_reference'],reference);check('entry_price',t['entry_price'],entry)
    quantity=t['contracts']*float(data['spec'].get('ctVal',1));check('quantity',t['quantity'],quantity)
    lot=float(data['spec'].get('lotSz','0.00000001'))
    check('contract_lot',abs(t['contracts']/lot-round(t['contracts']/lot))<1e-6,True)
    check('notional',t['notional'],quantity*entry);check('initial_margin',t['initial_margin'],quantity*entry/rules.leverage)
    if rules.margin_fraction is not None:check('margin_limit',t['initial_margin']<=t['equity_at_entry']*rules.margin_fraction+1e-7,True)
    if rules.max_margin_fraction is not None:check('aggregate_margin',t['opening_margin_utilization_pct']<=100*rules.max_margin_fraction+1e-7,True)
    risk=model['risk'].get(t['signal_at'],{})
    stop=entry*(1-side*risk.get('stop_pct',rules.stop_pct));target=risk.get('target_pct',rules.target_pct)
    check('stop_price',t['stop_price'],stop)
    target_price=entry*(1+side*target) if target is not None else None
    expected=None
    for stamp in range(t['entry_at'],end,SMALL):
        r=fast[stamp]
        if stamp>t['entry_at']:
            reason='stop_loss' if side*(r['o']-stop)<=0 else 'take_profit' if target_price is not None and side*(r['o']-target_price)>=0 else 'signal_invalid' if model['state'].get(stamp//window*window)!=side else None
            if reason:expected=(stamp,r['o'],reason);break
        hit_stop=r['l']<=stop if side==1 else r['h']>=stop
        hit_target=target_price is not None and (r['h']>=target_price if side==1 else r['l']<=target_price)
        if hit_stop or hit_target:expected=(stamp+SMALL-1,stop if hit_stop else target_price,'stop_loss' if hit_stop else 'take_profit');break
    if expected is None:expected=(end-1,fast[end-SMALL]['c'],'end_of_test')
    for key,value in zip(('exit_at','exit_reference','exit_reason'),expected):check(key,t[key],value)
    exit_price=t['exit_reference']*(1-side*rules.slippage_rate)
    gross=side*quantity*(exit_price-entry);fees=quantity*(entry+exit_price)*rules.fee_rate
    funding=sum(side*quantity*marks[int(stamp)]['o']*rate for stamp,rate in data['funding']['rates'].items() if t['entry_at']<int(stamp)<=t['exit_at'])
    check('exit_price',t['exit_price'],exit_price);check('fees',t['fees'],fees);check('gross_pnl',t['gross_pnl'],gross)
    check('funding_cost',t['funding_cost'],funding);check('margin_after_funding',t['margin'],t['initial_margin']-funding)
    check('net_pnl',t['net_pnl'],gross-fees-funding)
    check('slippage_cost',t['slippage_cost'],quantity*(abs(entry-reference)+abs(exit_price-t['exit_reference'])))
    # Exclude the unknown portion of the exit bar; include its known exit reference only.
    end_bar=t['exit_at']//SMALL*SMALL
    held=[r for r in data['fast'] if t['entry_at']<=r['t']<end_bar or (t['exit_reason']=='end_of_test' and r['t']==end_bar)]
    observed=[reference,t['exit_reference']]+[p for r in held for p in (r['h'],r['l'])]
    moves=[side*(p/entry-1)*100 for p in observed]
    return dict(issues=issues,audit_ok=not issues,mfe_pct=max([0]+moves),mae_pct=min([0]+moves),holding_hours=(t['exit_at']-t['entry_at'])/3600000,
                mfe_giveback_pct=max([0]+moves)-side*(exit_price/entry-1)*100)


def run(results_path,output):
    source=json.loads(results_path.read_text());bundle_path=Path(source['source_bundle']);raw=bundle_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=source['source_sha256']:raise ValueError('Frozen bundle changed')
    bundle=json.loads(raw);data=bundle['data'];windows=source['windows'];rules=Rules(**source['rules'])
    output.mkdir(parents=True,exist_ok=True)
    catalogs={s:strategy_catalog(d['slow']) for s,d in data.items()};views={s:snapshots(d['slow']) for s,d in data.items()}
    models={s:adjusted_models(d['slow'],catalogs[s]['bollinger_20']) for s,d in data.items()}
    audits=[]
    for window in ('full','review_month'):
        for index,t in enumerate(source['details']['bollinger_20_atr'][window]['trades'],1):
            s=t['symbol'];checked=audit_trade(t,data[s],models[s]['original'],rules,windows[window][1]);v=views[s][t['signal_at']]
            if v['independent_trigger']!=t['side']:checked['issues'].append('independent_bollinger_trigger');checked['audit_ok']=False
            room=t['side']*(v['middle']/v['close']-1)
            check=dict(model='original',window=window,trade_id=index,**t,**checked,entry_utc=iso(t['entry_at']),exit_utc=iso(t['exit_at']),**v,room_pct=room*100)
            check['notes']=('回归已越过中轨；' if room<=0 else '')+('日线逆向；' if v['daily']!=t['side'] else '')+('ADX增强；' if v['adx']>v['adx_old'] else '')+('亏损但规则执行正确' if t['net_pnl']<0 and checked['audit_ok'] else '规则执行正确' if checked['audit_ok'] else '审计异常')
            audits.append(check)
    inputs={w:scenario_inputs(data,*span) for w,span in windows.items()};ledger=[];stress=[];details={}
    for name in LABELS:
        model={s:models[s][name] for s in COINS};details[name]={}
        for w,(a,b) in windows.items():
            for account,symbols in [(s,[s]) for s in COINS]+[('COMBINED',list(COINS))]:
                row=dict(model=name,window=w,account=account)
                try:
                    result=run_model(model,data,inputs[w],a,b,rules,symbols);row.update(status='ok',**compact(result))
                    if account=='COMBINED':
                        details[name][w]=dict(metrics=compact(result),trades=result['trades'],equity=[dict(t=a,equity=10000)]+[p for p in result['equity_curve'] if p['t']%STEP==0 or p is result['equity_curve'][-1]])
                except ValueError as exc:row.update(status='unresolved',error=str(exc))
                ledger.append(row)
        for w in ('full','review_month'):
            a,b=windows[w]
            try:
                result=run_model(model,data,inputs[w],a,b,replace(rules,fee_rate=rules.fee_rate*2,slippage_rate=rules.slippage_rate*2),list(COINS))
                stress.append(dict(model=name,window=w,status='ok',**compact(result)))
            except ValueError as exc:stress.append(dict(model=name,window=w,status='unresolved',error=str(exc)))
        print(name,{w:round(d['metrics']['return_pct'],4) for w,d in details[name].items()},flush=True)
    baseline=details['original'];base_dd=max(baseline[w]['metrics']['max_drawdown_pct'] for w in ('development_1','development_2'))
    ranks=[]
    for name in LABELS:
        dev=[details[name].get(w,{}).get('metrics') for w in ('development_1','development_2')]
        eligible=all(d and d['return_pct']>0 and d['trade_count']>=5 and d['max_drawdown_pct']<=base_dd+1e-9 for d in dev)
        ranks.append(dict(model=name,eligible=eligible,min_return=min(d['return_pct'] for d in dev) if all(dev) else None,max_drawdown=max(d['max_drawdown_pct'] for d in dev) if all(dev) else None))
    eligible=sorted([r for r in ranks if r['eligible']],key=lambda r:(-r['min_return'],r['max_drawdown']))
    chosen=eligible[0]['model'] if eligible else 'original'
    # Original rerun must exactly match the frozen report before considering adjustments.
    for w in ('full','review_month'):
        if details['original'][w]['trades']!=source['details']['bollinger_20_atr'][w]['trades']:raise AssertionError('Original replay differs')
    for w in ('full','review_month'):
        for index,t in enumerate(details['cost_room'][w]['trades'],1):
            symbol=t['symbol'];checked=audit_trade(t,data[symbol],models[symbol]['cost_room'],rules,windows[w][1]);v=views[symbol][t['signal_at']]
            room=t['side']*(v['middle']/v['close']-1)
            if v['independent_trigger']!=t['side']:checked['issues'].append('independent_bollinger_trigger')
            if room<3*2*(.0006+.0005):checked['issues'].append('cost_room')
            checked['audit_ok']=not checked['issues']
            audits.append(dict(model='cost_room',window=w,trade_id=index,**t,**checked,entry_utc=iso(t['entry_at']),exit_utc=iso(t['exit_at']),**v,room_pct=room*100,notes='规则执行正确' if checked['audit_ok'] else '审计异常'))
    write_csv(output/'trade-audit.csv',[dict(r,issues=';'.join(r['issues'])) for r in audits])
    out=dict(source_results=str(results_path),source_bundle=str(bundle_path),source_sha256=source['source_sha256'],rules=source['rules'],windows=windows,labels=LABELS,
             audits=audits,ledger=ledger,cost_stress=stress,details=details,ranking=ranks,selected=chosen,original_replay_identical=True,
             incumbent='original',shadow_candidate='cost_room',promotion='retain_original; cost_room is retrospective shadow candidate; development winner ATR1.5 loses in review month',
             audit_failures=sum(not r['audit_ok'] for r in audits),run_count=len(ledger)+len(stress),prices=source['prices'])
    (output/'review.json').write_text(json.dumps(out,ensure_ascii=False,allow_nan=False,separators=(',',':')))
    fields=list(dict.fromkeys(k for r in ledger for k in r));write_csv(output/'adjustments.csv',[{k:r.get(k) for k in fields} for r in ledger])
    print('SELECTED',chosen,'AUDIT_FAILURES',out['audit_failures'],'RUNS',out['run_count'],flush=True)
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);root=Path(__file__).resolve().parents[3]
    parser.add_argument('--results',type=Path,default=root/'docs/reports/crypto-experiments-2026-11-07/results.json')
    parser.add_argument('--output',type=Path,default=root/'docs/reports/crypto-trade-review-2026-11-07')
    args=parser.parse_args();run(args.results,args.output)
