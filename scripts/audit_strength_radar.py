"""只读历史截断审计；未来五根收盘变化是标签，不作为筛选输入或交易收益。"""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'markets/a'))
import strength_radar as S


def evaluate_stock(args):
    stock,rows,start,calendar=args;daily={}
    stats=defaultdict(lambda:dict(signals=0,positive=0,rise5=0,sum_change=0));dates={d:i for i,d in enumerate(calendar)}
    for i in range(59,len(rows)-5):
        if rows[i]['date']<start:continue
        j=dates[rows[i]['date']]
        if [r['date'] for r in rows[i+1:i+6]]!=calendar[j+1:j+6]:continue
        result=S.evaluate(stock,rows[:i+1],rows[i]['date'])
        if result['stage'] not in ('matched','shape','extended'):continue
        future=(float(rows[i+5]['close'])/float(rows[i]['close'])-1)*100
        groups=['baseline_eligible']+(['v2_any']+result['patterns']+(['v1_without_recovery'] if any(p!='base_recovery' for p in result['patterns']) else []) if result['patterns'] else [])
        for group in groups:
            s=stats[group];s['signals']+=1;s['positive']+=future>0;s['rise5']+=future>=5;s['sum_change']+=future
            if group in ('baseline_eligible','v2_any'):
                d=daily.setdefault(rows[i]['date'],{}).setdefault(group,dict(signals=0,positive=0,sum_change=0))
                d['signals']+=1;d['positive']+=future>0;d['sum_change']+=future
    return dict(summary=dict(stats),daily=daily)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--day',default='2026-11-17');parser.add_argument('--start',default='2026-10-15');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--examples-start',default='2026-09-01');parser.add_argument('--examples-only',action='store_true');args=parser.parse_args()
    universe=S.CACHE/args.day/'universe.json'
    if not universe.exists():universe=S.CACHE/'universe.json'
    raw=json.loads(universe.read_text());folder=S.CACHE/args.day
    hist={p.stem:json.loads(p.read_text()) for p in folder.glob('*.json') if p.stem.isdigit()};calendar=sorted({r['date'] for rows in hist.values() for r in rows})
    if args.examples_only:
        original=json.loads((args.output/'audit.json').read_text())
        if original['rule_version']!=S.VERSION or original['as_of']!=args.day or original['start']!=args.start:raise ValueError('案例补录必须保留同一冻结全市场统计口径')
        result=original['results'];weighted=original['same_date_weighted_baseline']
    else:
        totals=defaultdict(lambda:dict(signals=0,positive=0,rise5=0,sum_change=0));daily={};jobs=[(stock,hist.get(stock['code'],[]),args.start,calendar) for stock in raw['stocks']]
        with ProcessPoolExecutor(max_workers=4) as pool:
            for i,result in enumerate(pool.map(evaluate_stock,jobs,chunksize=30),1):
                for day,groups in result['daily'].items():
                    for group,data in groups.items():
                        d=daily.setdefault(day,{}).setdefault(group,dict(signals=0,positive=0,sum_change=0))
                        for key,value in data.items():d[key]+=value
                for group,data in result['summary'].items():
                    for k,v in data.items():totals[group][k]+=v
                if i%1000==0:print('audited',i,flush=True)
        result={k:dict(v,positive_pct=round(v['positive']/v['signals']*100,2),rise5_pct=round(v['rise5']/v['signals']*100,2),mean_forward5_pct=round(v['sum_change']/v['signals'],3)) for k,v in totals.items()}
        weight=sum(d.get('v2_any',{}).get('signals',0) for d in daily.values())
        weighted=dict(positive_pct=round(sum(d['baseline_eligible']['positive']/d['baseline_eligible']['signals']*d.get('v2_any',{}).get('signals',0) for d in daily.values())/weight*100,2),mean_forward5_pct=round(sum(d['baseline_eligible']['sum_change']/d['baseline_eligible']['signals']*d.get('v2_any',{}).get('signals',0) for d in daily.values())/weight,3)) if weight else None
    examples=[]
    for code,name in [('300324','旋极信息'),('301209','联合化学'),('002242','九阳股份'),('002685','华东重机'),('301313','凡拓数创')]:
        stock=dict(code=code,name=name,board='创业板' if code.startswith('3') else '主板');rows=hist.get(code,[]);events=S.replay_stock(stock,rows,start=args.examples_start);last=S.evaluate(stock,rows,args.day)
        examples.append(dict(**stock,latest_stage=last['stage'],latest_reason=last['reason'],latest_metrics=last['metrics'],first_eligible_date=rows[59]['date'] if len(rows)>=60 else None,events=events))
    current=json.loads(S.LATEST.read_text());args.output.mkdir(parents=True,exist_ok=True)
    payload=dict(rule_version=S.VERSION,start=args.start,as_of=args.day,examples_start=args.examples_start,universe_sha256=original['universe_sha256'] if args.examples_only else hashlib.sha256(universe.read_bytes()).hexdigest(),examples_universe_sha256=hashlib.sha256(universe.read_bytes()).hexdigest(),results=result,same_date_weighted_baseline=weighted,examples=examples,coverage=current['coverage'],pattern_counts=current['pattern_counts'],today_matches=len(current['items']),tracking=len(current.get('tracking_items',[])),limitations=['当前证券名单回看，非历史成分；未复权，历史风险警示未还原','未来5根收盘变化仅为标签，不含交易时机、成本、停牌和排队，不是策略收益','同股连续信号重叠，不能当作独立样本或未来胜率','先看到样例后增加修复分型；新增案例仅核对，不改变固定分型阈值，尚非独立样本外验证'])
    (args.output/'audit.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False))
    lines=['# 走强雷达回看核验','',f'规则：{S.VERSION}；行情截止：{args.day}；回看起点：{args.start}。','',f"证券快照{raw['total']}只，风险名称剔除{len(raw['excluded'])}只；有效日线{current['coverage']['ready']} / {current['coverage']['total']}，当前命中{len(current['items'])}只，历史信号跟踪{len(current.get('tracking_items',[]))}只。",'', '| 规则组 | 信号次数 | 未来5根上涨比例 | 未来5根≥5%比例 | 平均5根收盘变化 |','|---|---:|---:|---:|---:|']
    for k,v in result.items():lines.append(f"| {k} | {v['signals']} | {v['positive_pct']}% | {v['rise5_pct']}% | {v['mean_forward5_pct']}% |")
    lines += ['',f'按命中日期频率加权的同期对照：{weighted}。该对照排除信号日期分布不同带来的部分偏差，仍非可成交策略收益。']
    lines+=['','## 用户样例（逐日截断）','',f'案例回看从{args.examples_start}开始，独立于上表从{args.start}开始的全市场统计；历史命中不等于当时实际发出提醒。']
    for example in examples:
        lines+=[f"### {example['name']} {example['code']}",f"截至{args.day}：{example['latest_stage']}；{example['latest_reason']}。"]
        lines += [f"- {e['date']}：{' / '.join(S.LABELS[p] for p in e['patterns'])}，当日收盘{e['close']}；后续窗口不足时不计算五根变化。" for e in example['events']]
    lines+=['','## 实验边界','',*['- '+x for x in payload['limitations']],'','v1仅要求已形成趋势，回看未捕捉到两个样例；v2增加底部修复独立分型，MA20仍向下时明确标为高风险。没有为个别股票设置白名单，也没有据本次回看修改任何模拟交易账户。','']
    (args.output/'report.md').write_text('\n'.join(lines));print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
