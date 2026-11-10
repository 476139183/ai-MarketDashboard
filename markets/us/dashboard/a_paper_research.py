"""Counterfactual historical plan research with the live A-share accounting engine."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime,timedelta
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import uuid

import a_share_paper as P

REPO=Path(__file__).resolve().parents[3]
A_DIR=REPO/'markets'/'a'
if str(A_DIR) not in sys.path:sys.path.append(str(A_DIR))
from daily_strategy import entry_signal
from trade_plan import build_trade_plan
from replay import fetch as fetch_minutes
from stock_setups import VERSION as SETUP_VERSION, evaluate_setups, selected_setup

CACHE=REPO/'.cache'/'a-paper-research'
END_TIMES=['09:45','10:00','10:15','10:30','10:45','11:00','11:15','11:30','13:15','13:30','13:45','14:00','14:15','14:30','14:45','15:00']
HIST_SOURCE='sina_historical_15m_open'
LIMITATIONS=[
 '当前热榜/大盘过滤后股票池回看，存在选择、幸存者及当前名称筛选偏差；不是当时真实全市场选股。',
 '逐日重算此前可见日线与计划，属于反事实研究，不声称当时已保存这些候选名单。',
 '只用完整16根的15分钟交易日，在每根开盘时采样；没有历史盘口，买卖价用开盘价近似，可能漏掉区间内到价。',
 '历史实际涨跌停价不可得，按普通板块10%/20%估算并排除临近边界；不完整复原ST、新股、停牌、除权、分红和订单队列。',
 '与前向模拟共用计划、T+1及费税账目，但行情采样和历史限制更粗；结果不能直接认定为前向真实成交胜率。',
 '账户期末不强卖，权益含按未复权日线收盘估值的持仓，尚未扣未来卖出费税；胜率只统计已平仓交易。',
 '同一批数据比较参数是样本内探索，不自动选择赢家或修改任何运行账户。']


def metrics(curve,trades,fees):
    first,last=curve[0],curve[-1];initial=first['equity'];final=last['equity'];peak=initial;drawdown=0
    for row in curve:
        peak=max(peak,row['equity']);drawdown=max(drawdown,(peak-row['equity'])/peak*100 if peak>0 else 0)
    span=max(1,(date.fromisoformat(last['date'])-date.fromisoformat(first['date'])).days+1)
    monthly={};prior=initial
    for row in curve:monthly[row['date'][:7]]=row['equity']
    months=[]
    for month,value in monthly.items():
        months.append(dict(month=month,return_pct=(value/prior-1)*100 if prior>0 else None,end_equity=value));prior=value
    out=dict(initial_capital=initial,end_equity=final,return_pct=(final/initial-1)*100,
        monthly_equivalent_pct=((final/initial)**(30.4375/span)-1)*100 if final>0 else None,
        max_drawdown_pct=drawdown,closed_trades=len(trades),win_rate_pct=100*sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else None,
        fees=fees,start=first['date'],end=last['date'],calendar_days=span)
    return out,months


def reconstructed_radar(sample,signal_date,candidate_rule='trend'):
    bench=[b for b in sample['benchmark'] if b['date']<=signal_date]
    market_pass=len(bench)>=20 and bench[-1]['close']>sum(b['close'] for b in bench[-20:])/20
    candidates=[];required=[b['date'] for b in bench[-21:]]
    if market_pass:
        for code,full in sorted(sample['daily'].items()):
            rows=[r for r in full if r['date']<=signal_date]
            if len(rows)<21 or [r['date'] for r in rows[-21:]]!=required or any(r['volume']<=0 for r in rows[-21:]):continue
            rules=('trend',) if candidate_rule=='trend' else ('trend','reclaim','breakout')
            if not any(entry_signal(rows,rule) for rule in rules):continue
            plan=build_trade_plan(rows)
            candidates.append(dict(code=code,name=sample['names'].get(code,code),close=rows[-1]['close'],category='策略候选',market_pass=True,plan_ready=plan['plan_ready'],trade_plan=plan))
    return dict(signal_date=signal_date,generated_at=signal_date+'T16:00:00+08:00',market_pass=market_pass,strategy_candidates=candidates)


def setups_radar(sample,signal_date):
    bench=[b for b in sample['benchmark'] if b['date']<=signal_date]
    passed=len(bench)>=20 and bench[-1]['close']>sum(b['close'] for b in bench[-20:])/20
    required=[b['date'] for b in bench[-25:]];candidates=[];confirmed=0
    for code,full in sorted(sample['daily'].items()):
        rows=[r for r in full if r['date']<=signal_date]
        if len(rows)<25 or [r['date'] for r in rows[-25:]]!=required:continue
        setups=evaluate_setups(rows);setup=selected_setup(setups)
        confirmed+=bool(setups)
        if not passed or not setup or not setup['plan']['plan_ready']:continue
        candidates.append(dict(code=code,name=sample['names'].get(code,code),close=rows[-1]['close'],category='策略候选',
            strategy_version=SETUP_VERSION,market_pass=True,plan_ready=True,trade_plan=setup['plan']))
    return dict(signal_date=signal_date,generated_at=signal_date+'T16:00:00+08:00',strategy_version=SETUP_VERSION,
        confirmed_count=confirmed,market_pass=passed,strategy_candidates=candidates)


def replay(sample,config,candidate_rule='trend',account_factory=None,radar_factory=None):
    config=P.normalize_config(config)
    if candidate_rule not in ('trend','expanded'):raise ValueError('未知研究候选口径')
    diagnostics=dict(market_pass_days=0,candidate_signals=0,ready_plans=0)
    dates=[b['date'] for b in sample['benchmark'] if sample['start']<=b['date']<=sample['end']]
    if not dates:raise ValueError('样本没有可复盘交易日')
    calendar=[b['date'] for b in sample['benchmark']]
    bars={}
    missing_days=0
    for code,rows in sample['minutes'].items():
        grouped={}
        for row in rows:grouped.setdefault(row['time'][:10],[]).append(row)
        for d,group in grouped.items():
            if len(group)!=16 or {r['time'][11:16] for r in group}!=set(END_TIMES):missing_days+=1;continue
            for r in group:bars[(code,r['time'])]=r
    curve=[dict(date=dates[0],equity=P.INITIAL_BALANCE)]
    with tempfile.TemporaryDirectory(prefix='a-paper-replay-') as directory:
        path=Path(directory)/'research.sqlite3'
        account=account_factory(path) if account_factory else P.ASharePaperAccount(path,source=HIST_SOURCE)
        account.configure(config)
        account.set_enabled(True,now=int(datetime.fromisoformat(dates[0]+'T09:29:00+08:00').timestamp()*1000))
        for d in dates:
            index=calendar.index(d)
            if index==0:continue
            previous=calendar[index-1];radar=radar_factory(sample,previous) if radar_factory else reconstructed_radar(sample,previous,candidate_rule)
            diagnostics['market_pass_days']+=radar['market_pass']
            diagnostics['candidate_signals']+=len(radar['strategy_candidates'])
            diagnostics['ready_plans']+=sum(p['plan_ready'] for p in radar['strategy_candidates'])
            history={code:[r for r in rows if r['date']<d] for code,rows in sample['daily'].items()}
            for end in END_TIMES:
                finish=datetime.fromisoformat(d+'T'+end+':00+08:00');now=int((finish-timedelta(minutes=15)).timestamp()*1000)
                observations={}
                for code,rows in history.items():
                    bar=bars.get((code,d+' '+end))
                    if not bar or not rows or rows[-1]['date']!=previous or bar.get('volume',0)<=0 or bar['high']<=bar['low']:continue
                    px=bar['open'];prior=rows[-1]['close'];ratio=.2 if code.startswith(('300','301','688')) else .1
                    lo,hi=P.money(prior*(1-ratio)),P.money(prior*(1+ratio))
                    if not lo*1.005<px<hi*.995:continue
                    observations[code]=dict(daily=rows,quote=dict(symbol=code,source=HIST_SOURCE,name=sample['names'].get(code,code),price=px,
                        bid=px,ask=px,volume=bar['volume'],previous_close=prior,quote_at=now,lower_limit=lo,upper_limit=hi))
                account.step(radar,calendar[:index+1],observations,now=now)
            # Valuation only: historical daily close is now known, without generating a fill.
            with account.transaction() as state:
                for p in state['positions']:
                    close=next((r['close'] for r in sample['daily'][p['code']] if r['date']==d),None)
                    if close is not None:p.update(last_price=close,quote_at=int(datetime.fromisoformat(d+'T15:00:00+08:00').timestamp()*1000))
            out=account.snapshot(now=now,include_all_trades=True);curve.append(dict(date=d,equity=out['equity']))
        out=account.snapshot(now=now,include_all_trades=True)
    summary,months=metrics(curve,out['trades'],out['fees'])
    return dict(config=config,candidate_rule=candidate_rule,diagnostics=diagnostics,summary=summary,monthly=months,trades=out['trades'],curve=curve,open_positions=out['positions'],incomplete_minute_days=missing_days)


def compare(sample,config):
    config=P.normalize_config(config)
    stress=dict(config)
    for k in ['commission_rate','min_commission','transfer_rate','stamp_rate','slippage_ticks']:stress[k]*=2
    alternate=20 if config['exit_ma']==10 else 10
    cases=[('baseline','旧版重返MA20 / MA'+str(config['exit_ma']),config),('exit_ma'+str(alternate),'旧版对照：MA'+str(alternate)+'退出',dict(config,exit_ma=alternate)),('cost_double','旧版压力：费税与滑点翻倍',stress)]
    results=[]
    for key,label,cfg in cases:results.append(dict(replay(sample,cfg),case=key,label=label))
    for key,label,cfg in [('setups_v2','分型v2 / MA'+str(config['exit_ma']),config),('setups_v2_cost_double','分型v2：费税与滑点翻倍',stress)]:
        results.append(dict(replay(sample,cfg,radar_factory=setups_radar),case=key,label=label,strategy_version=SETUP_VERSION))
    results.append(dict(replay(sample,config,candidate_rule='expanded'),case='candidate_expanded',label='研究对照：纳入收复/突破候选'))
    encoded=json.dumps(sample,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    return dict(version='a-candidate-research-v2',results=results,sample_sha256=hashlib.sha256(encoded).hexdigest(),
        symbols=list(sample['daily']),start=sample['start'],end=sample['end'],limitations=LIMITATIONS+sample.get('limitations',[]))


def collect(months=3,sample_size=20,progress=lambda text:None):
    if type(months) is not int or not 1<=months<=6 or type(sample_size) is not int or not 1<=sample_size<=46:raise ValueError('月份1至6，样本票数1至46')
    histories=json.load(gzip.open(A_DIR/'hot-history.json.gz','rt'))
    meta=json.loads((A_DIR/'hot-backtest.json').read_text())
    universe=json.loads((A_DIR/'large-universe.json').read_text())['included']
    benchmark=json.loads((A_DIR/'benchmark-results.json').read_text())['bars']
    end=benchmark[-1]['date'];requested=(date.fromisoformat(end)-timedelta(days=months*31)).isoformat()
    candidates=[]
    for row in universe:
        code=row['code']
        try:P.stock_code(code)
        except ValueError:continue
        name=row.get('name',code)
        if 'ST' in name.upper() or '退' in name or len(histories.get(code,[]))<200:continue
        if '未复权' not in meta.get('sources',{}).get(code,{}).get('source',''):continue
        candidates.append((code,name))
    candidates=candidates[:sample_size]
    if not candidates:raise ValueError('没有满足来源与历史覆盖要求的本地股票样本')
    CACHE.mkdir(parents=True,exist_ok=True);minute_data={};names=dict(candidates);failures=[]
    def fetch(code):
        file=CACHE/(code+'-'+date.today().isoformat()+'.json')
        if file.exists():rows=json.loads(file.read_text())
        else:_,_,rows=fetch_minutes(code);file.write_text(json.dumps(rows,ensure_ascii=False))
        return [r for r in rows if r['time'][:10]<=end]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(fetch,code):code for code,_ in candidates}
        for i,future in enumerate(as_completed(jobs),1):
            code=jobs[future]
            try:
                rows=future.result()
                if len({r['time'][:10] for r in rows})<15:raise ValueError('完整历史分钟覆盖不足15个日期')
                minute_data[code]=rows
            except Exception as exc:failures.append(code+': '+str(exc)[:100])
            progress(f'分钟样本 {i}/{len(jobs)} · 成功 {len(minute_data)}')
    if not minute_data:raise ValueError('未取得任何可用15分钟真实样本')
    start=max(requested,max(min(r['time'][:10] for r in rows) for rows in minute_data.values()))
    if start>=end:raise ValueError('公共分钟样本没有共同可复盘区间')
    sample=dict(daily={code:histories[code] for code in minute_data},minutes=minute_data,benchmark=benchmark,names=names,start=start,end=end,
        limitations=[f'请求最近{months}个月；本地日线/指数截止{end}，公共分钟覆盖后实际区间{start}至{end}。',f'请求{sample_size}票，可用{len(minute_data)}票；失败：'+('；'.join(failures) or '无')])
    with gzip.open(CACHE/'sample-latest.json.gz','wt') as file:json.dump(sample,file,ensure_ascii=False)
    return sample


class ResearchJobs:
    def __init__(self):self.pool=ThreadPoolExecutor(max_workers=1);self.lock=threading.Lock();self.jobs={}
    def start(self,params,config=None):
        if not isinstance(params,dict) or set(params)-{'months','sample_size','config'}:raise ValueError('历史复盘参数包含未知字段')
        months=params.get('months',3);size=params.get('sample_size',20)
        if type(months) is not int or not 1<=months<=6 or type(size) is not int or not 1<=size<=46:raise ValueError('历史复盘参数超出范围')
        overrides=params.get('config',{})
        if not isinstance(overrides,dict):raise ValueError('复盘config必须是参数对象')
        config=P.normalize_config(dict(config or P.DEFAULTS,**overrides))
        with self.lock:
            if any(j['status']=='running' for j in self.jobs.values()):raise ValueError('历史复盘正在运行，请稍候')
            if len(self.jobs)>=8:self.jobs.pop(next(iter(self.jobs)))
            key=uuid.uuid4().hex;self.jobs[key]=dict(id=key,status='running',progress='准备样本',error=None)
        self.pool.submit(self.run,key,config,months,size)
        return self.snapshot(key)
    def run(self,key,config,months,size):
        try:
            def progress(text):
                with self.lock:self.jobs[key]['progress']=text
            sample=collect(months,size,progress);progress('正在按相同账本规则复盘四个方案')
            result=compare(sample,config);CACHE.mkdir(parents=True,exist_ok=True)
            (CACHE/'latest-result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
            with self.lock:self.jobs[key].update(status='done',result=result)
        except Exception as exc:
            with self.lock:self.jobs[key].update(status='failed',error=str(exc)[:240])
    def snapshot(self,key=None):
        with self.lock:
            if key:
                if key not in self.jobs:raise KeyError('复盘任务不存在或服务已重启')
                return dict(self.jobs[key])
        file=CACHE/'latest-result.json'
        return dict(status='idle',result=json.loads(file.read_text()) if file.exists() else None)


JOBS=ResearchJobs()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--sample',type=Path);parser.add_argument('--config',type=Path);parser.add_argument('--months',type=int,default=3);parser.add_argument('--sample-size',type=int,default=20);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    sample=json.load(gzip.open(args.sample,'rt')) if args.sample else collect(args.months,args.sample_size,lambda s:print(s,flush=True))
    config=P.normalize_config(json.loads(args.config.read_text())) if args.config else P.DEFAULTS
    result=compare(sample,config);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    CACHE.mkdir(parents=True,exist_ok=True);(CACHE/'latest-result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    for r in result['results']:print(r['label'],r['summary'],flush=True)
