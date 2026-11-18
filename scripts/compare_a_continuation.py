"""固定规则的A股双入口离线对照；不选择参数、不写运行账户。"""
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
import continuation as C


def compare(sample,out):
    out.mkdir(parents=True,exist_ok=True)
    cases=[('setups_v2',R.P.DEFAULTS,R.setups_radar),
           ('continuation_v1',R.P.DEFAULTS,lambda s,d:R.dual_radar(s,d,continuation_only=True)),
           ('dual_v3',R.P.DEFAULTS,R.dual_radar)]
    stress=dict(R.P.DEFAULTS)
    for k in ('commission_rate','min_commission','transfer_rate','stamp_rate','slippage_ticks'):stress[k]*=2
    cases.append(('dual_v3_cost_double',stress,R.dual_radar))
    results=[]
    for name,cfg,factory in cases:
        print('运行',name,flush=True)
        result=R.replay(sample,cfg,radar_factory=factory)
        result['case']=name
        result['periods']=[]
        for label,start,end in [('前段',sample['start'],'2026-09-30'),('后段','2026-10-01',sample['end'])]:
            curve=[r for r in result['curve'] if start<=r['date']<=end]
            if not curve:continue
            prev=[r for r in result['curve'] if r['date']<start]
            if prev:curve=[prev[-1]]+curve
            trades=[t for t in result['trades'] if start<=R.P.day(t['exit_at'])<=end]
            fees=sum(t['fees'] for t in trades)
            summary,_=R.metrics(curve,trades,fees)
            result['periods'].append(dict(label=label,summary=summary))
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        results.append(result)
        print(name,json.dumps(result['summary'],ensure_ascii=False),flush=True)
    payload=dict(generated_at=datetime.now(R.P.BJ).isoformat(),entry_revision=C.ENTRY_REVISION,
        sample_sha256=hashlib.sha256(json.dumps(sample,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
        symbols=len(sample['daily']),start=sample['start'],end=sample['end'],results=results,
        limitations=R.LIMITATIONS+sample.get('limitations',[])+[
            '固定规则后按日期分段展示，当前历史池仍有选择偏差；后段不是严格样本外。',
            '分钟开盘采样只使用该时刻之前已完成K线的成交量及结构，首次确认后须等待后续新采样。',
            '风险预算按计划止损及双边成本估算；T+1、隔夜跳空和跌停可能令实际损失超过预算。'])
    (out/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False))
    lines=['# A股强势延续与双入口固定规则对照','',f"生成：{payload['generated_at']}；样本 {payload['symbols']} 只，{payload['start']}～{payload['end']}。",'',
           '规则先固定后运行，没有在这些结果中搜索最优参数；与前向账本隔离。', '',
           '| 方案 | 已平仓 | 胜率 | 总收益 | 几何月化 | 最大收盘回撤 | 累计费税 | 期末持仓 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:
        s=r['summary'];win=f"{s['win_rate_pct']:.2f}%" if s['win_rate_pct'] is not None else '未知'
        lines.append(f"| {r['case']} | {s['closed_trades']} | {win} | {s['return_pct']:.2f}% | {s['monthly_equivalent_pct']:.2f}% | {s['max_drawdown_pct']:.2f}% | {s['fees']:.2f} | {len(r['open_positions'])} |")
    lines+=['','## 时段分解','','| 方案 | 时段 | 已平仓 | 收益 | 最大收盘回撤 |','|---|---|---:|---:|---:|']
    for r in results:
        for p in r['periods']:
            s=p['summary'];lines.append(f"| {r['case']} | {p['label']} | {s['closed_trades']} | {s['return_pct']:.2f}% | {s['max_drawdown_pct']:.2f}% |")
    lines+=['','## 执行口径','',
            '回踩保留分型v2；强势延续先按昨日相对强度、流动性和上升MA20入池，再等待放量突破开盘区间及估算VWAP，后续完整K线回踩守住或再次放量上破。',
            '延续新仓使用半预算与权益0.25%的含成本计划风险上限。沿用整手、真实后续报价、T+1；不设固定小目标止盈，使用结构止损、确认支撑跟随、两根失守突破位和VWAP或完整日线退出。',
            '关闭网页不停止后台；已有持仓保存原规则。本报告不表示已经切换运行账户。','',
            '## 限制','']+['- '+x for x in payload['limitations']]
    (out/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--sample',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with gzip.open(args.sample,'rt') as file:sample=json.load(file)
    compare(sample,args.output)
