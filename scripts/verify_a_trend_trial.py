"""只读核对固定检验门槛；不会修改RELEASED、账户或任何持仓。"""
import argparse
import gzip
import hashlib
import json
from math import isfinite
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'markets/us/dashboard'),str(ROOT/'markets/a'),str(ROOT)]
from trend_reclaim import VERSION as RULE_VERSION
from a_paper_research import REPLAY_VERSION


def fingerprint(sample):
    return hashlib.sha256(json.dumps(sample,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def evaluate(reports,samples,*,rule_version=RULE_VERSION,candidate_case='trend_reclaim',max_drawdown=3,require_paired=False):
    reasons=[];counts={};rows=[]
    for name in ('development','time-holdout','stock-holdout'):
        r=reports[name]
        if r.get('rule_version')!=rule_version or r.get('replay_version')!=REPLAY_VERSION:reasons.append(name+'：规则/回放版本不一致')
        if r.get('sample_sha256')!=fingerprint(samples[name]):reasons.append(name+'：原始输入指纹不一致')
        cases={c['case']:c['summary'] for c in r['results']}
        if len(cases)!=len(r['results']) or set(cases)!={'baseline',candidate_case,'cost_double'}:
            reasons.append(name+'：对照方案缺失或重复');continue
        if require_paired:
            candidate=next(c for c in r['results'] if c['case']==candidate_case)
            paired=candidate.get('paired_cost_stress',{}).get('return_pct')
            if not isinstance(paired,(int,float)) or isinstance(paired,bool) or not isfinite(paired) or paired<0:
                reasons.append(name+'：固定成交双倍成本收益无效或亏损')
        valid=True
        for c in cases.values():
            for k in ('return_pct','max_drawdown_pct','closed_trades'):
                v=c.get(k)
                if not isinstance(v,(int,float)) or isinstance(v,bool) or not isfinite(v):valid=False
            n=c.get('closed_trades')
            if not isinstance(n,int) or isinstance(n,bool) or n<0:valid=False
        if not valid:reasons.append(name+'：收益/回撤/笔数无效');continue
        base,normal,stress=(cases[k] for k in ('baseline',candidate_case,'cost_double'))
        counts[name]=normal['closed_trades']
        if (name=='development' and normal['return_pct']<=0) or normal['return_pct']<max(0,base['return_pct']):reasons.append(name+'：收益未改善基线')
        if stress['return_pct']<0:reasons.append(name+'：双倍成本后亏损')
        if normal['closed_trades']>0 and stress['closed_trades']==0:reasons.append(name+'：成本压力无已平仓样本')
        if normal['max_drawdown_pct']>max_drawdown:reasons.append(name+f'：最大收盘回撤超过{max_drawdown}%')
        rows.append(dict(group=name,closed_trades=normal['closed_trades'],return_pct=normal['return_pct'],
                         stress_return_pct=stress['return_pct'],drawdown_pct=normal['max_drawdown_pct']))
    dev=set(samples['development']['daily']);same=set(samples['time-holdout']['daily']);other=set(samples['stock-holdout']['daily'])
    if dev!=same:reasons.append('同股后续的股票集合与开发集不一致')
    if dev&other:reasons.append('独立股票组与开发集重叠')
    if reports['development']['end']>=min(reports[k]['start'] for k in ('time-holdout','stock-holdout')):reasons.append('后续时间窗口与开发窗口重叠')
    total=sum(counts.values());later=sum(counts.get(k,0) for k in ('time-holdout','stock-holdout'))
    if total<20:reasons.append('合计已平仓不足20笔')
    if later<5:reasons.append('后续已平仓不足5笔')
    return dict(passed=not reasons,rule_version=rule_version,replay_version=REPLAY_VERSION,
                total_closed=total,later_closed=later,groups=rows,reasons=reasons)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report-dir',type=Path,required=True)
    p.add_argument('--strategy',choices=('trend_reclaim','oversold_reversal'),default='trend_reclaim')
    p.add_argument('--rule-version',default=RULE_VERSION)
    p.add_argument('--stock-sample',type=Path,default=ROOT/'.cache/a-stock-holdout-nov04-11/sample.json.gz')
    p.add_argument('--stock-report',default='stock-holdout');a=p.parse_args()
    files={'development':ROOT/'.cache/a-paper-research/sample-latest.json.gz',
           'time-holdout':ROOT/'.cache/a-continuation-holdout/sample.json.gz',
           'stock-holdout':a.stock_sample}
    reports={k:json.loads((a.report_dir/(a.stock_report if k=='stock-holdout' else k)/'summary.json').read_text()) for k in files}
    samples={}
    for k,file in files.items():
        with gzip.open(file,'rt') as f:samples[k]=json.load(f)
    result=evaluate(reports,samples,rule_version=a.rule_version,candidate_case=a.strategy,
                    max_drawdown=5 if a.strategy=='oversold_reversal' else 3,require_paired=a.strategy=='oversold_reversal')
    (a.report_dir/'decision.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    lines=['# 固定检验的发布判断','',f"规则：{a.rule_version}；回放：{REPLAY_VERSION}。",'',
           '发布判断：'+('符合已登记门槛；仍需核对运行接入，不代表已启用。' if result['passed'] else '未通过，研究候选保持关闭。'),'',
           '| 组别 | 已平仓 | 收益 | 双倍成本收益 | 最大收盘回撤 |','|---|---:|---:|---:|---:|']
    for r in result['groups']:
        lines.append(f"| {r['group']} | {r['closed_trades']} | {r['return_pct']:.4f}% | {r['stress_return_pct']:.4f}% | {r['drawdown_pct']:.4f}% |")
    lines+=['','## 未通过项','']+['- '+s for s in result['reasons']]
    lines+=['','核验器只读，不切换账户、不迁移持仓、不回填成交。', '',
            '回放修正为收盘只观察、后续开盘执行；1/2毫秒只用于模型排序，不能当成真实报价时间。历史盘口、交易队列及公司行动仍有限制。', '',
            '旧版回放结果保留供追溯；不能把撮合口径改变带来的数字差异视作策略收益提升。']
    (a.report_dir/'verdict.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
