#!/usr/bin/env python3
"""保留原入场/退出，比较全市场选股排序；不使用当前60股作为固定池。"""
from datetime import datetime
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from candidate_monitor import history_issue
from daily_strategy import entry_signal, run
from market_scan import exclude_locked_signals, qualification_changes
from paper_account import update_account
from portfolio import equity_curve, period_metrics
from validate_strategy import load_histories, audit_account
from simulation import rank_stocks

ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'markets/a/.cache/validation'
RANKINGS=('breakout_desc','breakout_asc','liquidity','low_volatility','efficiency','risk_momentum','confirmed_efficiency')


def signal_features(bars):
    sample=bars[-21:]
    prior=[b['close'] for b in sample[:-1]]
    movement=sum(abs(a-b) for a,b in zip(prior,prior[1:]))
    efficiency=(prior[-1]-prior[0])/movement if movement else 0
    ranges=[max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close']))
            for a,b in zip(sample[-15:-1],sample[-14:])]
    atr_pct=sum(ranges)/14/sample[-1]['close']*100
    high=max(b['high'] for b in sample[:-1])
    volume=sum(b['volume'] for b in sample[:-1])/20
    return {'close':sample[-1]['close'],'breakout_distance_pct':round((sample[-1]['close']/high-1)*100,3),
            'turnover':sum(b['close']*b['volume'] for b in sample[-20:])/20,
            'atr_pct':atr_pct,'efficiency':efficiency,
            'risk_momentum':((prior[-1]/prior[0]-1)*100)/atr_pct if atr_pct else 0,
            'volume_ratio':sample[-1]['volume']/volume if volume else 0,
            'bullish_close':sample[-1]['close']>=sample[-1]['open']}


def ranking_key(row,ranking):
    if ranking=='breakout_desc':
        return (-row['breakout_distance_pct'],row['code'])
    if ranking=='breakout_asc':
        return (row['breakout_distance_pct'],-row['turnover'],row['code'])
    if ranking=='liquidity':
        return (-row['turnover'],row['atr_pct'],row['code'])
    if ranking=='low_volatility':
        return (row['atr_pct'],-row['turnover'],row['code'])
    if ranking in ('efficiency','confirmed_efficiency'):
        return (-row['efficiency'],-row['turnover'],row['code'])
    if ranking=='risk_momentum':
        return (-row['risk_momentum'],-row['turnover'],row['code'])
    raise ValueError('未知排序')


def accepts_ranking(row,ranking):
    return ranking!='confirmed_efficiency' or (1.2<=row['volume_ratio']<=3 and row['bullish_close'] and row['turnover']>=100_000_000)


def build_candidates(histories,metadata,calendar):
    indices={d:i for i,d in enumerate(calendar)}
    daily={}
    for code,bars in histories.items():
        for i in range(20,len(bars)):
            day=bars[i]['date']
            if day<'2026-01-01' or day not in indices:
                continue
            sample=bars[i-20:i+1]
            if not entry_signal(sample,'breakout'):
                continue
            j=indices[day]
            if history_issue(sample,calendar[max(0,j-20):j+1],day):
                continue
            row=dict(signal_features(sample),code=code,name=metadata[code]['name'],signal_date=day,status='突破信号',unmet=[])
            accepted,_=exclude_locked_signals([row],{code:bars[i-1:i+1]},metadata)
            if accepted:
                daily.setdefault(day,[]).append(row)
    return daily


def ranked_signals(daily,calendar,ranking,top_n=5):
    result=[]
    previous=[]
    for day in calendar:
        if day<'2026-01-01':
            continue
        rows=sorted((r for r in daily.get(day,[]) if accepts_ranking(r,ranking)),key=lambda r:ranking_key(r,ranking))
        changes=qualification_changes(rows,previous)
        continuing=set(changes['continuing_codes'])
        result.append({'signal_date':day,'stocks':[dict(r,qualification='持续符合' if r['code'] in continuing else '新入选') for r in rows[:top_n]],
                       'eligible_count':len(rows)})
        previous=rows
    return result


def rank_current_original(histories,names,calendar,ranking):
    if ranking not in RANKINGS:
        raise ValueError('未知排序')
    rows,_=rank_stocks(histories,names,calendar)
    enriched=[dict(row,**signal_features(histories[row['code']])) for row in rows]
    return sorted((r for r in enriched if accepts_ranking(r,ranking)),key=lambda r:ranking_key(r,ranking))


def main():
    metadata={s['code']:s for s in json.loads((CACHE/'universe.json').read_text())['stocks']}
    histories=load_histories()
    index=json.loads((CACHE/'adjustment-index.json').read_text())
    calendar=[x['date'] for x in index]
    print('重建相同的原始突破候选',len(histories),flush=True)
    daily=build_candidates(histories,metadata,calendar)
    with gzip.open(CACHE/'selection-candidates.json.gz','wt') as f:json.dump(daily,f,ensure_ascii=False)
    train_cal=[d for d in calendar if d<='2026-06-30']
    training,states,signals={},{},{}
    for ranking in RANKINGS:
        signals[ranking]=ranked_signals(daily,calendar,ranking)
        state=update_account(None,histories,metadata,train_cal,signals[ranking],{},'2026-01-01',historical_research=True,exit_policy='ma20')
        audit=audit_account(state);m=state['performance']
        training[ranking]={'performance':m,'closed_trades':audit['closed_trades'],
                           'eligible':m['return_pct']>0 and m['max_drawdown_pct']>=-20 and audit['closed_trades']>=20,
                           'score':round(m['return_pct']+m['max_drawdown_pct'],4)}
        states[ranking]=state
        print('TRAIN',ranking,training[ranking],flush=True)
    choices=[k for k,v in training.items() if v['eligible']]
    selected=max(choices,key=lambda k:(training[k]['score'],k)) if choices else None
    print('LOCKED',selected,flush=True)
    results={}
    for ranking,state in states.items():
        state=update_account(state,histories,metadata,calendar,signals[ranking],{},'2026-01-01',historical_research=True,exit_policy='ma20')
        results[ranking]={'training':training[ranking],'year':state['performance'],
                          'check_jul_oct':period_metrics(state['curve'],'2026-07-01','2026-10-31'),
                          'monthly':state['monthly'],'months_at_5pct':state['completed_months_at_target'],
                          'audit':audit_account(state),'account':state}
        print('CHECK',ranking,results[ranking]['year'],results[ranking]['check_jul_oct'],flush=True)
    original=json.loads((ROOT/'markets/a/strategy-validation.json').read_text())['results']['year_ma20']
    assert results['breakout_desc']['year']==original['performance'],'原控制组复算不一致'
    print('全市场独立等权对照',flush=True)
    by_stock={}
    for code,bars in histories.items():
        board=metadata[code]['board'];ratio=.3 if board=='北交所' else .2 if board in ('创业板','科创板') else .1
        by_stock[code]=run(bars,ratio,entry_rule='breakout',exit_ma=20,trading_calendar=calendar,min_entry_date='2026-01-01')
    equal_curve=equity_curve(histories,by_stock)
    equal={'year':period_metrics(equal_curve,'2026-01-01','2026-11-13'),
           'check_jul_oct':period_metrics(equal_curve,'2026-07-01','2026-10-31'),'curve':equal_curve,
           'method':'全量当前股票，每股固定独立等额初始资金；1月起空仓模拟，仅作资金分配对照，非建议持有所有股票'}
    print('EQUAL_UNIVERSE',equal['year'],flush=True)
    robust={}
    if selected:
        for name,start,fee in [('double_cost','2026-01-01',.003),('fresh_july','2026-07-01',.0015)]:
            state=update_account(None,histories,metadata,calendar,signals[selected],{},start,historical_research=True,exit_policy='ma20',fee=fee)
            robust[name]={'year_or_period':state['performance'],'check_jul_oct':period_metrics(state['curve'],'2026-07-01','2026-10-31'),
                          'audit':audit_account(state),'account':state}
    report={'generated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'data_as_of':calendar[-1],
            'universe_size':len(histories),'selected_by_training':selected,'results':results,'robustness':robust,'equal_universe_reference':equal,
            'selection_rule':'仅1至6月收益>0、回撤不超过20%、至少20笔平仓，按收益+有符号回撤评分',
            'limitations':['当前名单回看历史、未复权、估算限价和允许碎股影响结果','所有历史时段已被观察，不能称独立样本外',
                           '只比较本轮预先记录的7组；除最后过滤组，仅排序不同','不使用资金流，不称已经验证真实资金撤离']}
    (ROOT/'markets/a/selection-study.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('DONE',flush=True)


if __name__=='__main__':
    main()
