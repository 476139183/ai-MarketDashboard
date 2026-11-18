"""冻结昨日全市场候选后回放一天；独立临时账户，不回填真实模拟记录。"""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import gzip
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'markets/a'),str(ROOT/'markets/us/dashboard')]
import a_paper_research as R
import radar
from replay import fetch


def run(screen_file,trade_date,out):
    screen=json.loads(screen_file.read_text());signal_date=screen['signal_date']
    if trade_date<=signal_date:raise ValueError('回放日必须在选股信号日之后')
    rows={r['code']:r for r in screen['candidates']};codes=screen['selected_codes']
    if len(codes)!=len(set(codes)) or any(c not in rows for c in codes):raise ValueError('冻结候选名单不完整')
    benchmark=radar._hs300(trade_date)
    dates=[r['date'] for r in benchmark if r['date']<=trade_date]
    if dates[-2:]!=[signal_date,trade_date]:raise ValueError('信号与执行日必须为相邻、可核验的交易日')
    out.mkdir(parents=True,exist_ok=True);daily={};minutes={};failures={}
    def one(code):
        cached=ROOT/'.cache/a-market-screen'/signal_date/(code+'.json')
        history=json.loads(cached.read_text())['daily'];latest,source=radar._current_history(rows[code],trade_date)
        if not latest or latest[-1]['date']<trade_date:raise ValueError('缺少回放日收盘用于估值')
        # Keep the frozen prior history; append only the execution day's daily valuation.
        history=[r for r in history if r['date']<=signal_date]+[r for r in latest if signal_date<r['date']<=trade_date]
        _,_,bars=fetch(code);bars=[r for r in bars if r['time'][:10]==trade_date]
        if len(bars)!=16 or {r['time'][11:16] for r in bars}!=set(R.END_TIMES):raise ValueError('回放日缺少完整16根分钟线')
        return history,bars
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs={pool.submit(one,c):c for c in codes}
        for job in as_completed(jobs):
            code=jobs[job]
            try:daily[code],minutes[code]=job.result()
            except Exception as exc:failures[code]=str(exc)[:180]
    sample=dict(daily=daily,minutes=minutes,names={c:rows[c]['name'] for c in codes},benchmark=benchmark,
                start=trade_date,end=trade_date,limitations=['按昨日沪深全市场名单与固定规则选股；今日行情仅用于回放，不按今日涨幅增删名单。','采集失败：'+json.dumps(failures,ensure_ascii=False)])
    with gzip.open(out/'input.json.gz','wt') as f:json.dump(sample,f,ensure_ascii=False,allow_nan=False)
    result=R.replay(sample,R.P.DEFAULTS,radar_factory=lambda s,d:R.reversal_radar(s,d,include_legacy=False,version=screen['rule_version']))
    result['summary']['monthly_equivalent_pct']=None  # 单日机会核查不展示外推月收益。
    diagnostics={}
    for code in daily:
        issues=[]
        class TraceAccount(R.P.ASharePaperAccount):
            def step(self,*args,**kwargs):
                super().step(*args,**kwargs)
                error=self.snapshot().get('error')
                if error:issues.append(error)
        single=dict(sample,daily={code:daily[code]},minutes={code:minutes[code]})
        check=R.replay(single,R.P.DEFAULTS,account_factory=lambda p:TraceAccount(p,source=R.HIST_SOURCE),
                       radar_factory=lambda s,d:R.reversal_radar(s,d,include_legacy=False,version=screen['rule_version']))
        diagnostics[code]=dict(single_stock_would_enter=bool(check['open_positions']),issues=list(dict.fromkeys(issues)))
    result['individual_checks']=diagnostics
    result.update(signal_date=signal_date,trade_date=trade_date,frozen_codes=codes,failures=failures,execution_enabled=False)
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    opened={p['code']:p for p in result['open_positions']}
    lines=[f'# 昨日候选在{trade_date}的独立回放','',f'信号日{signal_date}；冻结观察{len(codes)}只，成功采集{len(daily)}只，失败{len(failures)}只。','',
           '采用同一撮合引擎、实际采样开盘加滑点、双边费税、0.5%计划风险与原仓位上限。仅为反事实回放，不回填现有模拟账户。当天新买受T+1限制，无已平仓胜率。','',
           '| 股票 | 昨日计划区 | 当日回放状态 | 含滑点入场 | 日终浮盈亏（未计未来卖出成本） |','|---|---|---|---:|---:|']
    for code in codes:
        row=rows[code];z=row['plan']['sniper'];p=opened.get(code)
        if code in failures:status='采集失败：'+failures[code]
        elif p:status='模拟入场，T+1锁定'
        elif diagnostics[code]['single_stock_would_enter']:status='单票回放满足；组合仓位、资金或优先次序未允许'
        elif any('净目标空间' in e for e in diagnostics[code]['issues']):status='未入场；盘中出现过净目标空间不足'
        else:status='单票也未入场；未同时通过完整反转确认与后续计划报价'
        lines.append(f"| {code} {row['name']} | {z['secondary_buy']}～{z['ideal_buy']} | {status} | {p['entry_price'] if p else '—'} | {p['unrealized_pnl'] if p else '—'} |")
    lines+=['','回放版本：'+R.REPLAY_VERSION+'。没有真实历史盘口；是否买入以逐次采样与账户规则为准，不将日内高低点相交假定为成交。','']
    (out/'report.md').write_text('\n'.join(lines))
    print(json.dumps(dict(summary=result['summary'],opened=[dict(code=p['code'],name=p['name'],entry_price=p['entry_price']) for p in result['open_positions']],failures=failures),ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--screen',type=Path,required=True);p.add_argument('--trade-date',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.screen,a.trade_date,a.output)
