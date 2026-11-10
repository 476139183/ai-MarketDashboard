"""Research-only factorial audit; no runner or live account is controlled here."""
import gzip
import hashlib
import json
from pathlib import Path
import a_paper_research as R
from a_strategy_diagnostics import segments

MODES=('strict','near_ma20','off')


def market_gate(bench,mode):
    if mode not in MODES:raise ValueError('未知市场过滤对照')
    if len(bench)<20:return False
    if mode=='off':return True
    ma=sum(r['close'] for r in bench[-20:])/20
    return bench[-1]['close']>ma if mode=='strict' else bench[-1]['close']>=ma*.98


def radar(sample,d,rule,mode):
    if rule not in ('trend','expanded'):raise ValueError('未知候选口径')
    bench=[r for r in sample['benchmark'] if r['date']<=d]
    passed=market_gate(bench,mode);required=[r['date'] for r in bench[-21:]];candidates=[]
    if passed:
        for code,full in sorted(sample['daily'].items()):
            rows=[r for r in full if r['date']<=d]
            if len(rows)<21 or [r['date'] for r in rows[-21:]]!=required or any(r['volume']<=0 for r in rows[-21:]):continue
            rules=('trend',) if rule=='trend' else ('trend','reclaim','breakout')
            if not any(R.entry_signal(rows,r) for r in rules):continue
            plan=R.build_trade_plan(rows)
            candidates.append(dict(code=code,name=sample['names'].get(code,code),close=rows[-1]['close'],category='策略候选',market_pass=True,plan_ready=plan['plan_ready'],trade_plan=plan))
    return dict(signal_date=d,generated_at=d+'T16:00:00+08:00',market_pass=passed,strategy_candidates=candidates)


def run(sample,previous):
    fingerprint=hashlib.sha256(json.dumps(sample,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if previous.get('sample_sha256')!=fingerprint:raise ValueError('原对照样本指纹不一致')
    results=[]
    for rule in ('trend','expanded'):
        for mode in MODES:
            print(rule,mode,flush=True)
            if mode=='strict':
                base=previous['results'][0 if rule=='trend' else -1]
                if base['candidate_rule']!=rule or base['config']!=R.P.DEFAULTS:raise ValueError('原对照配置不一致')
                result=dict(base)
            else:
                result=R.replay(sample,R.P.DEFAULTS,candidate_rule=rule,radar_factory=lambda s,d,r=rule,m=mode:radar(s,d,r,m))
            result.update(filter_mode=mode,segments=segments(result));results.append(result)
    stress=dict(R.P.DEFAULTS)
    for k in ('commission_rate','min_commission','transfer_rate','stamp_rate','slippage_ticks'):stress[k]*=2
    stressed=R.replay(sample,stress,candidate_rule='expanded',radar_factory=lambda s,d:radar(s,d,'expanded','off'))
    return dict(version='a-market-filter-audit-v1',sample_sha256=fingerprint,
                limitations=R.LIMITATIONS,results=results,expanded_off_stress=stressed)


if __name__=='__main__':
    root=Path(__file__).resolve().parents[3]
    with gzip.open(root/'.cache/a-paper-research/sample-latest.json.gz','rt') as f:sample=json.load(f)
    prior=json.loads((root/'docs/reports/a-share-plan-2026-11-09/summary.json').read_text())
    out=root/'docs/reports/a-share-market-filter-2026-11-10';out.mkdir(parents=True,exist_ok=True)
    old=json.loads((root/'markets/a/radar-latest.json').read_text())
    (out/'radar-snapshot.json').write_text(json.dumps(old,ensure_ascii=False,indent=2))
    result=run(sample,prior)
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    for r in result['results']:print(r['candidate_rule'],r['filter_mode'],r['diagnostics'],r['summary'],flush=True)
