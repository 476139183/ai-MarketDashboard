"""Frozen A-share research alternatives; only temporary accounts, never live selection."""
from copy import deepcopy
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean,median

import a_share_paper as P
import a_paper_research as R

POLICIES={'ma10':'原MA10收盘退出','ma20':'MA20收盘退出','confirm2':'连续两日收盘跌破MA10','protect_only':'仅原计划止损/目标'}
STYLES={'trend_pullback':'趋势回踩确认','breakout_retest':'放量突破后回踩','short_reversal':'趋势中的短期反转'}
REFERENCES=[
 {'title':'Qlib官方：信号与组合分开评价','url':'https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md'},
 {'title':'Qlib官方：按时间区分训练/验证/测试','url':'https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml'},
 {'title':'Shi et al. 2015：沪深反转研究，历史月频且有市场状态差异','url':'https://arxiv.org/abs/1505.00328'},
 {'title':'Shi & Zhou 2017：动量/反转依回看期、持有期及标的特征而变','url':'https://arxiv.org/abs/1702.07374'}]


def exit_reason(policy,p,q,rows):
    if policy not in POLICIES:raise ValueError('未知退出研究方案')
    if q['price']<=p['stop']:return 'stop'
    if q['price']>=p['target']:return 'target'
    if policy=='protect_only':return None
    if policy in ('ma10','ma20'):
        period=10 if policy=='ma10' else 20
        original=dict(p,costs=dict(p['costs'],exit_ma=period))
        return P.ASharePaperAccount.exit_reason(original,q,rows)
    if len(rows)<11 or rows[-2]['date']<p['entry_day']:return None
    for offset in (-2,-1):
        end=len(rows)+offset+1
        window=rows[end-10:end]
        if len(window)<10 or rows[offset]['close']>=mean(r['close'] for r in window):return None
    return 'ma10_confirm2'


class PolicyAccount(P.ASharePaperAccount):
    def __init__(self,path,policy):super().__init__(path,source=R.HIST_SOURCE);self.policy=policy
    def exit_reason(self,p,q,rows):return exit_reason(self.policy,p,q,rows)


def full_minute_days(rows):
    groups={}
    for r in rows:groups.setdefault(r['time'][:10],[]).append(r)
    return {d:sorted(g,key=lambda r:r['time']) for d,g in groups.items()
            if len(g)==16 and {r['time'][11:16] for r in g}==set(R.END_TIMES)}


def event_study(sample,rule):
    calendar=[r['date'] for r in sample['benchmark']];bench={r['date']:r['close'] for r in sample['benchmark']}
    events=[]
    for d in calendar:
        if not sample['start']<=d<=sample['end']:continue
        index=calendar.index(d)
        for candidate in R.reconstructed_radar(sample,d,rule)['strategy_candidates']:
            code=candidate['code'];close=candidate['close'];by_day={r['date']:r for r in sample['daily'][code]}
            row=dict(code=code,signal_date=d,plan_ready=candidate['plan_ready'],forward={})
            for horizon in (1,3,5):
                if index+horizon>=len(calendar) or calendar[index+horizon]>sample['end']:continue
                dates=calendar[index+1:index+horizon+1]
                future=[by_day.get(x) for x in dates]
                if any(r is None for r in future):continue
                change=(future[-1]['close']/close-1)*100;benchmark=(bench[dates[-1]]/bench[d]-1)*100
                row['forward'][str(horizon)]=dict(return_pct=change,benchmark_pct=benchmark,excess_pct=change-benchmark,
                    max_up_pct=(max(r['high'] for r in future)/close-1)*100,max_down_pct=(min(r['low'] for r in future)/close-1)*100)
            row['after_next_open']={}
            entry_index=index+1
            if entry_index<len(calendar):
                opening=by_day.get(calendar[entry_index])
                if opening and opening['open']>0:
                    for horizon in (1,3,5):
                        target_index=entry_index+horizon
                        if target_index>=len(calendar) or calendar[target_index]>sample['end']:continue
                        ending=by_day.get(calendar[target_index])
                        if ending:
                            row['after_next_open'][str(horizon)]=dict(entry_date=calendar[entry_index],exit_date=calendar[target_index],return_pct=(ending['close']/opening['open']-1)*100)
            events.append(row)
    summary={}
    for ready in (False,True):
        group=[e for e in events if e['plan_ready'] or not ready];out={}
        for horizon in ('1','3','5'):
            values=[r['forward'][horizon] for r in group if horizon in r['forward']]
            out[horizon]=dict(count=len(values),up_pct=100*sum(v['return_pct']>0 for v in values)/len(values) if values else None,
                mean_return_pct=mean(v['return_pct'] for v in values) if values else None,
                median_return_pct=median(v['return_pct'] for v in values) if values else None,
                mean_excess_pct=mean(v['excess_pct'] for v in values) if values else None)
        summary['ready_plans' if ready else 'all_candidates']=out
    next_open={}
    for horizon in ('1','3','5'):
        values=[e['after_next_open'][horizon]['return_pct'] for e in events if horizon in e['after_next_open']]
        next_open[horizon]=dict(count=len(values),mean_return_pct=mean(values) if values else None,up_pct=100*sum(x>0 for x in values)/len(values) if values else None)
    return dict(rule=rule,events=events,summary=summary,next_open_reference=next_open)


def entry_study(sample,rule,config):
    calendar=[r['date'] for r in sample['benchmark']]
    minute={code:full_minute_days(rows) for code,rows in sample['minutes'].items()}
    records=[]
    for d in calendar:
        index=calendar.index(d)
        if not sample['start']<=d<=sample['end'] or index+1>=len(calendar):continue
        nxt=calendar[index+1]
        if nxt>sample['end']:continue
        for p in R.reconstructed_radar(sample,d,rule)['strategy_candidates']:
            if not p['plan_ready']:continue
            plan=p['trade_plan']['sniper'];lower=plan['secondary_buy'];upper=plan['ideal_buy']
            rows=minute[p['code']].get(nxt,[])
            ask_min=int((Decimal(str(lower))*100).to_integral_value(rounding=ROUND_CEILING))
            fill_max=int((Decimal(str(upper))*100).to_integral_value(rounding=ROUND_FLOOR))
            in_band=[r for r in rows if lower<=r['open']<=upper]
            fillable=[r for r in in_band if lower<=P.ASharePaperAccount.fill(r['open'],True,config)<=upper]
            touched=[r for r in rows if r['low']<=upper and r['high']>=lower]
            records.append(dict(code=p['code'],signal_date=d,entry_date=nxt,lower=lower,upper=upper,
                tick_executable=ask_min+config['slippage_ticks']<=fill_max,full_minute_day=bool(rows),
                open_samples_in_zone=len(in_band),open_samples_after_slippage=len(fillable),intrabar_touches=len(touched),
                min_open=min((r['open'] for r in rows),default=None),max_open=max((r['open'] for r in rows),default=None)))
    return dict(rule=rule,records=records,summary=dict(plans=len(records),no_tick_space=sum(not r['tick_executable'] for r in records),
        sampled_touch_plans=sum(r['open_samples_in_zone']>0 for r in records),sampled_after_cost_plans=sum(r['open_samples_after_slippage']>0 for r in records),
        intrabar_touch_plans=sum(r['intrabar_touches']>0 for r in records)))


def fixed_entry(trade,sample,policy):
    """Per-entry comparison, not a portfolio: keep original entry/size/cost fixed."""
    t=deepcopy(trade);code=t['code'];cfg=t['config'];calendar=[b['date'] for b in sample['benchmark']]
    entry_day=P.day(t['entry_at']);daily=sample['daily'][code]
    p=dict(entry_day=entry_day,stop=t['stop'],target=t['target'],costs=cfg)
    result=dict(code=code,policy=policy,entry_at=t['entry_at'],entry_price=t['entry_price'],shares=t['shares'],entry_fee=t['entry_costs']['total'],closed=False)
    groups=full_minute_days(sample['minutes'][code]);last_date=sample['end']
    for d in calendar:
        if d<=entry_day or d>last_date:continue
        prior=[r for r in daily if r['date']<d]
        if not prior or prior[-1]['date']!=calendar[calendar.index(d)-1]:continue
        ratio=.2 if code.startswith(('300','301','688')) else .1
        lo,hi=P.money(prior[-1]['close']*(1-ratio)),P.money(prior[-1]['close']*(1+ratio))
        for bar in groups.get(d,[]):
            px=bar['open']
            if not lo*1.005<px<hi*.995 or bar['volume']<=0 or bar['high']<=bar['low']:continue
            reason=exit_reason(policy,p,{'price':px},prior)
            if not reason:continue
            fill=P.ASharePaperAccount.fill(px,False,cfg)
            if not lo<=fill<=hi:continue
            end=datetime.fromisoformat(bar['time']+':00').replace(tzinfo=P.BJ)-timedelta(minutes=15)
            exit_at=int(end.timestamp()*1000);notional=P.money(fill*t['shares']);cost=P.ASharePaperAccount.costs(notional,False,cfg)
            net=P.money(notional-cost['total']-t['entry_price']*t['shares']-t['entry_costs']['total'])
            result.update(closed=True,exit_at=exit_at,exit_price=fill,reason=reason,fees=P.money(t['entry_costs']['total']+cost['total']),net_pnl=net,
                return_pct=net/(t['entry_price']*t['shares']+t['entry_costs']['total'])*100,
                holding_sessions=calendar.index(d)-calendar.index(entry_day),mark_date=d)
            return result
    last=next(r for r in reversed(daily) if r['date']<=last_date)
    marked=P.money((last['close']-t['entry_price'])*t['shares']-t['entry_costs']['total'])
    result.update(mark_date=last['date'],mark_price=last['close'],marked_pnl=marked,return_pct=marked/(t['entry_price']*t['shares']+t['entry_costs']['total'])*100,
                  holding_sessions=calendar.index(last['date'])-calendar.index(entry_day))
    return result


def prototype_radar(sample,signal_date,style):
    if style not in STYLES:raise ValueError('未知策略原型')
    bench=[r for r in sample['benchmark'] if r['date']<=signal_date]
    passed=len(bench)>=20 and bench[-1]['close']>mean(r['close'] for r in bench[-20:])
    expected=[r['date'] for r in bench[-21:]];candidates=[]
    if passed:
        for code,full in sorted(sample['daily'].items()):
            rows=[r for r in full if r['date']<=signal_date]
            if len(rows)<65 or [r['date'] for r in rows[-21:]]!=expected or any(r['volume']<=0 for r in rows[-21:]):continue
            closes=[r['close'] for r in rows];close=closes[-1];ma10=mean(closes[-10:]);ma20=mean(closes[-20:]);ma60=mean(closes[-60:])
            atr=mean(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(rows[-15:-1],rows[-14:]))
            if atr<=0:continue
            if style=='trend_pullback':
                if not (close>ma20>ma60 and ma20>mean(closes[-25:-5]) and min(r['low'] for r in rows[-5:])<=ma10 and close<=ma10*1.03):continue
                lower,upper=ma10*.995,ma10*1.005;risk=2*atr
            elif style=='breakout_retest':
                level=max(r['high'] for r in rows[-21:-1])
                if not (level<close<=level*1.05 and rows[-1]['volume']>=1.5*mean(r['volume'] for r in rows[-21:-1])):continue
                lower,upper=level*.995,level*1.005;risk=1.5*atr
            else:
                ret3=close/closes[-4]-1;last=rows[-1]
                if not (-.10<=ret3<=-.03 and close>ma60 and last['high']>last['low'] and (close-last['low'])/(last['high']-last['low'])>=.5):continue
                lower,upper=close*.995,close*1.005;risk=1.5*atr
            stop=max(.01,lower-risk);target=upper+2*(upper-stop)
            if style=='short_reversal':
                target=min(ma10,target)
                if target<=upper:continue
            plan=dict(plan_ready=True,strategy_style=style,sniper=dict(secondary_buy=round(lower,4),ideal_buy=round(upper,4),stop_loss=round(stop,4),take_profit=round(target,4)))
            candidates.append(dict(code=code,name=sample['names'].get(code,code),close=close,category='策略候选',market_pass=True,plan_ready=True,trade_plan=plan))
    return dict(signal_date=signal_date,generated_at=signal_date+'T16:00:00+08:00',market_pass=passed,strategy_candidates=candidates)


def segments(result):
    out={}
    for key,begin,end in [('earlier',result['summary']['start'],'2026-09-30'),('later','2026-10-01',result['summary']['end'])]:
        points=[p for p in result['curve'] if begin<=p['date']<=end]
        trades=[t for t in result['trades'] if begin<=P.day(t['exit_at'])<=end]
        if not points:out[key]=None;continue
        previous=[p for p in result['curve'] if p['date']<points[0]['date']]
        initial=previous[-1]['equity'] if previous else result['summary']['initial_capital']
        out[key]=dict(start=points[0]['date'],end=points[-1]['date'],closed_trades=len(trades),return_pct=(points[-1]['equity']/initial-1)*100,
            win_rate_pct=100*sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else None)
    return out


def run(sample,previous_report,progress=print):
    config=dict(P.DEFAULTS);original=previous_report['results'][-1]['trades']
    report=dict(version='a-strategy-audit-v1',start=sample['start'],end=sample['end'],symbols=len(sample['daily']),references=REFERENCES,
        limitations=R.LIMITATIONS+['固定买入对照忽略其他交易机会和组合容量，只报告逐笔均值；组合测试另行执行资金/仓位约束。',
        '新方案定义前已观察过本时期，前后分段不是真正样本外；三个研究原型不等于引用论文的原策略。'])
    report['selection']={rule:event_study(sample,rule) for rule in ('trend','expanded')}
    report['entry']={rule:entry_study(sample,rule,config) for rule in ('trend','expanded')}
    calendar=[r['date'] for r in sample['benchmark']]
    audits=[]
    for t in original:
        entry=P.day(t['entry_at']);exited=P.day(t['exit_at']);rows=[r for r in sample['daily'][t['code']] if r['date']<exited]
        audits.append(dict(code=t['code'],entry_at=t['entry_at'],exit_at=t['exit_at'],holding_sessions=calendar.index(exited)-calendar.index(entry),
            entry_price=t['entry_price'],exit_price=t['exit_price'],reason=t['reason'],soft_signal_day=rows[-1]['date'],
            previous_close=rows[-1]['close'],ma10=mean(r['close'] for r in rows[-10:]),net_pnl=t['net_pnl']))
    report['original_exits']=audits;report['fixed_entries']={}
    for policy in POLICIES:
        rows=[fixed_entry(t,sample,policy) for t in original];closed=[r for r in rows if r['closed']]
        report['fixed_entries'][policy]=dict(rows=rows,closed=len(closed),open=len(rows)-len(closed),win_rate_pct=100*sum(r['net_pnl']>0 for r in closed)/len(closed) if closed else None,
            average_marked_return_pct=mean(r['return_pct'] for r in rows) if rows else None,median_holding_sessions=median(r['holding_sessions'] for r in rows) if rows else None)
    report['exit_portfolios']={}
    for policy in POLICIES:
        progress('退出规则组合复盘：'+policy)
        result=R.replay(sample,dict(config,exit_ma=20 if policy=='ma20' else 10),candidate_rule='expanded',account_factory=lambda path,mode=policy:PolicyAccount(path,mode))
        result['segments']=segments(result);report['exit_portfolios'][policy]=result
    report['prototypes']={}
    for style in STYLES:
        progress('A股策略原型：'+style)
        factory=lambda data,d,mode=style:prototype_radar(data,d,mode)
        result=R.replay(sample,config,account_factory=lambda path:PolicyAccount(path,'confirm2'),radar_factory=factory)
        result['segments']=segments(result)
        stress=dict(config)
        for k in ['commission_rate','min_commission','transfer_rate','stamp_rate','slippage_ticks']:stress[k]*=2
        stressed=R.replay(sample,stress,account_factory=lambda path:PolicyAccount(path,'confirm2'),radar_factory=factory)
        result['stress_summary']=stressed['summary'];report['prototypes'][style]=result
    report['sample_sha256']=hashlib.sha256(json.dumps(sample,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--sample',required=True,type=Path);parser.add_argument('--previous',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();data=json.load(gzip.open(args.sample,'rt'));previous=json.loads(args.previous.read_text())
    report=run(data,previous,lambda s:print(s,flush=True));args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    for k,v in report['exit_portfolios'].items():print('exit',k,v['summary'],flush=True)
    for k,v in report['prototypes'].items():print('strategy',k,v['summary'],flush=True)
