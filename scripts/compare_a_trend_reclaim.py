"""固定策略候选的样本检验；无参数搜索，无账户控制。"""
import argparse
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'markets/us/dashboard'),str(ROOT/'markets/a'),str(ROOT)]
import a_paper_research as R
import trend_reclaim as T
import oversold_reversal as M


def compare(sample,out,label,*,strategy='trend_reclaim',rule_version=None):
    out.mkdir(parents=True,exist_ok=True)
    stress=dict(R.P.DEFAULTS)
    for k in ('commission_rate','min_commission','transfer_rate','stamp_rate','slippage_ticks'):stress[k]*=2
    results=[]
    module=M if strategy=='oversold_reversal' else T
    rule_version=rule_version or module.VERSION
    if module is M and rule_version not in M.VERSIONS:raise ValueError('未知反转版本')
    if module is T and rule_version!=T.VERSION:raise ValueError('未知回踩版本')
    title='短期超跌反转' if module is M else '日线趋势回踩收复'
    candidate_factory=((lambda s,d:R.reversal_radar(s,d,version=rule_version)) if module is M else lambda s,d:R.reclaim_radar(s,d,include_legacy=True))
    for case,cfg,factory in [('baseline',R.P.DEFAULTS,R.setups_radar),
                            (strategy,R.P.DEFAULTS,candidate_factory),('cost_double',stress,candidate_factory)]:
        print(label,case,flush=True)
        r=R.replay(sample,cfg,radar_factory=factory);r['case']=case;results.append(r)
        if case==strategy:r['paired_cost_stress']=R.fixed_execution_stress(r)
        (out/(case+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False))
        print(json.dumps(r['summary'],ensure_ascii=False),flush=True)
    payload=dict(label=label,generated_at=datetime.now(R.P.BJ).isoformat(),rule_version=rule_version,replay_version=R.REPLAY_VERSION,
        symbols=len(sample['daily']),start=sample['start'],end=sample['end'],
        sample_sha256=hashlib.sha256(json.dumps(sample,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
        results=results,limitations=R.LIMITATIONS+sample.get('limitations',[]))
    (out/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False))
    lines=[f'# {title}：{label}', '',
           f"固定{payload['symbols']}只；{payload['start']}～{payload['end']}。规则{rule_version}，回放{R.REPLAY_VERSION}。",'',
           '| 方案 | 已平仓 | 胜率 | 累计收益 | 几何月化 | 最大收盘回撤 | 固定成交双倍成本收益 | 费税 | 未平仓 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:
        s=r['summary'];win='未知' if s['win_rate_pct'] is None else f"{s['win_rate_pct']:.2f}%"
        paired=f"{r['paired_cost_stress']['return_pct']:.4f}%" if 'paired_cost_stress' in r else '—'
        lines.append(f"| {r['case']} | {s['closed_trades']} | {win} | {s['return_pct']:.4f}% | {s['monthly_equivalent_pct']:.4f}% | {s['max_drawdown_pct']:.4f}% | {paired} | {s['fees']:.2f} | {len(r['open_positions'])} |")
    lines+=['', '收盘只观察/准备，随后开盘才尝试撮合；同分钟按模型事件序列排序，不是真实毫秒级报价。今日产生信号不能在同一收盘成交。',
            '', '当前报告不改变前向账户，是否发布须汇总三组预先登记的门槛。', '', '## 限制','']+['- '+s for s in payload['limitations']]
    (out/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sample',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--label',required=True)
    p.add_argument('--strategy',choices=('trend_reclaim','oversold_reversal'),default='trend_reclaim')
    p.add_argument('--rule-version')
    a=p.parse_args()
    with gzip.open(a.sample,'rt') as f:sample=json.load(f)
    compare(sample,a.output,a.label,strategy=a.strategy,rule_version=a.rule_version)
