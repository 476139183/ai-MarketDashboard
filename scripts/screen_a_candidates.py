"""按指定历史证券名单筛选昨日计划；只读研究，不写交易账本。"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'markets/a'),str(ROOT/'markets/us/dashboard')]
import oversold_reversal as M
import radar
from strategy import classify

STAGE_LABELS=dict(candidate='盘后候选',security='风险证券',suspended='停牌',data='数据未就绪',
                  liquidity='流动性不足',market='指数急跌防守',range='价格区间不足',oversold='未达超跌条件',
                  recovery='收盘收复不足',plan='计划空间不足')


def universe_rows(payload,signal_date):
    if payload.get('as_of')!=signal_date:raise ValueError('必须使用信号日证券名单，不能冒充全市场历史覆盖')
    result={}
    for row in payload['stocks']:
        try:symbol,board=classify(row['code'])
        except ValueError:continue
        code=symbol[2:]
        if code in result:raise ValueError('证券名单有重复股票')
        result[code]=dict(code=code,name=row['code_name'],board=board,trading=row['tradeStatus']=='1')
    return result


def evaluate_stock(stock,rows,benchmark,signal_date):
    def unavailable(reason):return dict(stage='data',reason=reason,plan=None,metrics={})
    if not stock['trading']:return dict(stage='suspended',reason='信号日停牌',plan=None,metrics={})
    rows=[r for r in rows if r['date']<=signal_date]
    benchmark=[r for r in benchmark if r['date']<=signal_date]
    if not rows or rows[-1]['date']!=signal_date:return unavailable('信号日日线未落地')
    if not benchmark or benchmark[-1]['date']!=signal_date:return unavailable('指数日线未更新到信号日')
    return M.screen(rows,benchmark,stock['name'])


def publish(payload,path=None):
    from selection_snapshot import LATEST
    path=path or LATEST
    snapshot={k:payload[k] for k in ('signal_date','rule_version','source','scope','total','stages','selected_codes')}
    snapshot.update(kind='a_share_selection_research',execution_enabled=False,candidate_count=len(payload['candidates']),
                    candidates=payload['candidates'][:M.MAX_CANDIDATES])
    temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False));temporary.replace(path)


def run(universe,signal_date,out,*,fetch=False,workers=8):
    date.fromisoformat(signal_date)
    raw=json.loads(universe.read_text());stocks=universe_rows(raw,signal_date)
    cache=ROOT/'.cache/a-market-screen'/signal_date;cache.mkdir(parents=True,exist_ok=True)
    out.mkdir(parents=True,exist_ok=True)
    audit=ROOT/'docs/reports/a-share-opportunity-audit-2026-11-12/input.json.gz'
    cached=json.load(gzip.open(audit,'rt')) if audit.exists() else {}
    histories=dict(cached.get('daily',{}));benchmark=cached.get('benchmark',[])
    for path in (ROOT/'.cache/a-reversal-expanded/sample.json.gz',ROOT/'.cache/a-paper-research/sample-latest.json.gz'):
        if path.exists():
            sample=json.load(gzip.open(path,'rt'))
            for code,rows in sample['daily'].items():
                if rows and (code not in histories or rows[-1]['date']>histories[code][-1]['date']):histories[code]=rows
    if not benchmark or benchmark[-1]['date']<signal_date:
        if not fetch:raise ValueError('缺少指定日期指数日线，请显式使用--fetch')
        benchmark=radar._hs300(signal_date)
    benchmark=[r for r in benchmark if r['date']<=signal_date]
    def one(stock):
        code=stock['code'];path=cache/(code+'.json')
        if not stock['trading']:return evaluate_stock(stock,[],benchmark,signal_date)
        try:
            if path.exists():rows=json.loads(path.read_text())['daily']
            else:
                rows=histories.get(code,[])
                if not rows or not any(r['date']==signal_date for r in rows):
                    if not fetch:raise ValueError('未采集，不能当成无机会')
                    rows,source=radar._current_history(stock,signal_date)
                rows=[r for r in rows if r['date']<=signal_date]
                if not rows or rows[-1]['date']!=signal_date:raise ValueError('未取得信号日日线')
                path.write_text(json.dumps(dict(daily=rows),ensure_ascii=False))
            return evaluate_stock(stock,rows,benchmark,signal_date)
        except Exception as exc:return dict(stage='data',reason=str(exc)[:180],plan=None,metrics={})
    results=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,s):s for s in stocks.values()}
        for job in as_completed(jobs):
            results.append(dict(**jobs[job],**job.result()))
            if len(results)%100==0:print('筛选',len(results),'/',len(stocks),flush=True)
    results.sort(key=lambda r:r['code'])
    candidates=sorted((r for r in results if r['plan']),key=lambda r:(-r['plan']['close_location'],r['plan']['rsi2'],-r['plan']['avg20_turnover'],r['code']))
    payload=dict(signal_date=signal_date,rule_version=M.VERSION,source=raw['source'],
                 universe_sha256=hashlib.sha256(universe.read_bytes()).hexdigest(),
                 scope='信号日沪深主板、创业板、科创板；不含北交所、基金和指数',
                 total=len(stocks),stages=dict(Counter(r['stage'] for r in results)),
                 candidates=candidates,selected_codes=[r['code'] for r in candidates[:M.MAX_CANDIDATES]],results=results,
                 released=M.RELEASED,paper_trial=M.PAPER_TRIAL)
    (out/'screen.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False))
    lines=[f'# {signal_date}全市场盘后选股核查','',payload['scope']+'。', '',
           f"名单共{payload['total']}只，按冻结超跌反转规则得到{len(candidates)}只盘后候选；优先观察最多{M.MAX_CANDIDATES}只。",'',
           '候选不是买入指令；盘中必须出现完整反转确认，后续报价仍在计划区，且通过数量、成本和风险检查。策略仍未通过收益验证，不改现有账户。','',
           '| 首个未通过环节 | 数量 |','|---|---:|']
    lines += [f'| {STAGE_LABELS.get(k,k)} | {v} |' for k,v in sorted(payload['stages'].items())]
    lines += ['', '| 股票 | RSI2 | 均额/亿元 | 昨日收盘 | 次日计划区间 | 止损 | 均值目标 |','|---|---:|---:|---:|---|---:|---:|']
    for r in candidates:
        p=r['plan'];z=p['sniper'];lines.append(f"| {r['code']} {r['name']} | {p['rsi2']:.2f} | {p['avg20_turnover']/1e8:.2f} | {p['signal_close']:.2f} | {z['secondary_buy']:.2f}～{z['ideal_buy']:.2f} | {z['stop_loss']:.2f} | {z['take_profit']:.2f} |")
    lines += ['', '完整逐票排除原因见screen.json；数据失败与无形态分开，不能将未采集股票说成没有机会。行情未复权，成交额由收盘价乘股数近似；不保证候选盈利。','']
    (out/'report.md').write_text('\n'.join(lines))
    publish(payload)
    print(json.dumps({k:payload[k] for k in ('total','stages','selected_codes')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--universe',type=Path,required=True);p.add_argument('--signal-date',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fetch',action='store_true');p.add_argument('--workers',type=int,default=8)
    a=p.parse_args();run(a.universe,a.signal_date,a.output,fetch=a.fetch,workers=a.workers)
