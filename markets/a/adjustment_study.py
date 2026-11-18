#!/usr/bin/env python3
"""复算两轮预先记录的调整实验；研究回放不修改前瞻账户。"""
import argparse
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from adaptive_strategy import build_profiles, market_breadth, PROFILES, PHASE2_PROFILES
from paper_account import update_account
from portfolio import period_metrics
from simulation import fetch_index
from validate_strategy import load_histories, audit_account

ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'markets/a/.cache/validation'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',type=int,choices=(1,2),default=2)
    args=parser.parse_args()
    metadata={s['code']:s for s in json.loads((CACHE/'universe.json').read_text())['stocks']}
    histories=load_histories()
    index_path=CACHE/'adjustment-index.json'
    if index_path.exists():
        index=json.loads(index_path.read_text())
    else:
        index=fetch_index('2026-11-13')
        index_path.write_text(json.dumps(index))
    calendar=[x['date'] for x in index]
    breadth=market_breadth(histories,calendar) if args.phase==2 else {}
    profile_names=PHASE2_PROFILES if args.phase==2 else PROFILES
    policies=('ma20','breadth_trend') if args.phase==2 else ('ma20','trend_flow')
    print('生成预定义方案',args.phase,len(histories),flush=True)
    signals,counts=build_profiles(histories,{c:s['name'] for c,s in metadata.items()},calendar,index,
                                  profile_names=profile_names,breadth=breadth)
    with gzip.open(CACHE/f'adjustment-phase{args.phase}-signals.json.gz','wt') as f:
        json.dump({'profiles':signals,'counts':counts,'breadth':breadth},f,ensure_ascii=False)
    training_calendar=[d for d in calendar if d<='2026-06-30']
    training,states={},{}
    for profile in profile_names:
        for policy in policies:
            key=profile+'__'+policy
            state=update_account(None,histories,metadata,training_calendar,signals[profile],{},'2026-01-01',
                                 historical_research=True,exit_policy=policy,breadth=breadth)
            audit=audit_account(state)
            metrics=state['performance']
            eligible=metrics['return_pct']>0 and metrics['max_drawdown_pct']>=-20 and audit['closed_trades']>=20
            training[key]={'performance':metrics,'closed_trades':audit['closed_trades'],
                           'score':round(metrics['return_pct']+metrics['max_drawdown_pct'],4),'eligible':eligible}
            states[key]=state
    candidates=[k for k,v in training.items() if v['eligible']]
    selected=max(candidates,key=lambda k:(training[k]['score'],k)) if candidates else None
    print('只按训练区间锁定',selected,flush=True)
    results={}
    for key,state in states.items():
        profile,policy=key.split('__')
        state=update_account(state,histories,metadata,calendar,signals[profile],{},'2026-01-01',
                             historical_research=True,exit_policy=policy,breadth=breadth)
        monthly=state['monthly']
        results[key]={'training':training[key],'year':state['performance'],
                      'holdout_jul_oct':period_metrics(state['curve'],'2026-07-01','2026-10-31'),
                      'monthly':monthly,'audit':audit_account(state),'account':state,
                      'holdout_months_at_5pct':sum(v['return_pct']>=5 for m,v in monthly.items() if '2026-07'<=m<='2026-10'),
                      'year_full_months_at_5pct':sum(v['return_pct']>=5 for m,v in monthly.items() if m<'2026-11')}
        print(key,results[key]['year'],results[key]['holdout_jul_oct'],flush=True)
    robustness={}
    if selected:
        profile,policy=selected.split('__')
        for name,fee,start in [('double_cost',.003,'2026-01-01'),('fresh_july_start',.0015,'2026-07-01')]:
            state=update_account(None,histories,metadata,calendar,signals[profile],{},start,
                                 historical_research=True,exit_policy=policy,fee=fee,breadth=breadth)
            robustness[name]={'year_or_period':state['performance'],
                              'holdout_jul_oct':period_metrics(state['curve'],'2026-07-01','2026-10-31'),
                              'monthly':state['monthly'],'audit':audit_account(state),'account':state}
    plan='2026-11-16-a-strategy-adjustment'+('-phase2' if args.phase==2 else '')+'.md'
    report={'generated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'data_as_of':calendar[-1],
            'phase':args.phase,'calibration':['2026-01-01','2026-06-30'],
            'locked_check':['2026-07-01','2026-10-31'],'partial_end':'2026-11-13',
            'selection_rule':'仅训练收益>0、最大回撤不超过20%、至少20笔平仓，按收益+有符号最大回撤排序',
            'selected_by_training':selected,'results':results,'robustness':robustness,'candidate_counts':counts,
            'code_hashes':{n:hashlib.sha256((ROOT/'markets/a'/n).read_bytes()).hexdigest()
                           for n in ('adaptive_strategy.py','paper_account.py','adjustment_study.py')},
            'plan_hash':hashlib.sha256((ROOT/'docs/plans'/plan).read_bytes()).hexdigest(),
            'limitations':['所有时段已被历史研究观察，不是真正独立样本外',
                           '当前名称与名单回看历史，未复权和估算限价影响结果',
                           '未使用资金流，不代表资金策略已验证',
                           '每轮只比较预先记录的8组；第二轮是在第一轮失败后另立的实验']}
    filename='strategy-adjustment'+('-phase2' if args.phase==2 else '')+'.json'
    (ROOT/'markets/a'/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('报告',filename,flush=True)


if __name__=='__main__':
    main()
