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
import continuation as C
import trend_reclaim as T
import oversold_reversal as M

CACHE=REPO/'.cache'/'a-paper-research'
END_TIMES=['09:45','10:00','10:15','10:30','10:45','11:00','11:15','11:30','13:15','13:30','13:45','14:00','14:15','14:30','14:45','15:00']
HIST_SOURCE='sina_historical_15m_open'
REPLAY_VERSION='bar-close-observe-next-open-limits-v3'
LIMITATIONS=[
 '当前热榜/大盘过滤后股票池回看，存在选择、幸存者及当前名称筛选偏差；不是当时真实全市场选股。',
 '逐日重算此前可见日线与计划，属于反事实研究，不声称当时已保存这些候选名单。',
 '只用完整16根的15分钟交易日；收盘样本只观察/准备，不在该收盘价成交，后续开盘样本才尝试撮合。',
 '相邻收盘与开盘同分钟时，使用边界后1/2毫秒表示模型内先后顺序，不是真实毫秒报价；成交价仍仅取实际K线开盘并计入滑点、费用。没有历史盘口，属于下一根开盘执行假设。',
 '历史涨跌停按普通板块10%/20%估算；临近涨停不假定可买、临近跌停不假定可卖，保留反方向退出报价。没有真实盘口，不完整复原ST、新股、停牌、除权、分红和订单队列。',
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


def fixed_execution_stress(result):
    """固定原成交时间、数量和标记价，仅将费税与滑点翻倍。"""
    equity=result['summary']['end_equity'];rows=[]
    def stressed(cfg):
        out=dict(cfg)
        for k in ('commission_rate','min_commission','transfer_rate','stamp_rate','slippage_ticks'):out[k]*=2
        return out
    for t in result['trades']:
        cfg=t['config'];stress=stressed(cfg);extra_tick=cfg['slippage_ticks']*.01
        entry=P.money(t['entry_price']+extra_tick);exit=P.money(t['exit_price']-extra_tick);qty=t['shares']
        if exit<=0:raise ValueError('固定成交压力价无效')
        fees=P.ASharePaperAccount.costs(P.money(entry*qty),True,stress)['total']+P.ASharePaperAccount.costs(P.money(exit*qty),False,stress)['total']
        pnl=P.money(P.money(exit*qty)-entry*qty-fees)
        equity+=pnl-t['net_pnl']
        rows.append(dict(code=t['code'],shares=qty,baseline_net_pnl=t['net_pnl'],stress_net_pnl=pnl,stress_fees=P.money(fees)))
    for p in result['open_positions']:
        cfg=p['costs'];stress=stressed(cfg);entry=P.money(p['entry_price']+cfg['slippage_ticks']*.01);qty=p['shares']
        fees=P.ASharePaperAccount.costs(P.money(entry*qty),True,stress)['total']
        equity-=(entry-p['entry_price'])*qty+fees-p['entry_fee']
    equity=P.money(equity);initial=result['summary']['initial_capital']
    return dict(end_equity=equity,return_pct=(equity/initial-1)*100,closed_trades=len(rows),
                win_rate_pct=100*sum(t['stress_net_pnl']>0 for t in rows)/len(rows) if rows else None,trades=rows)


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


def setups_radar(sample,signal_date,*,market_filter=True):
    bench=[b for b in sample['benchmark'] if b['date']<=signal_date]
    passed=len(bench)>=20 and (not market_filter or bench[-1]['close']>sum(b['close'] for b in bench[-20:])/20)
    required=[b['date'] for b in bench[-25:]];candidates=[];confirmed=0
    for code,full in sorted(sample['daily'].items()):
        if 'legacy_eligible' in sample and code not in sample['legacy_eligible']:continue
        rows=[r for r in full if r['date']<=signal_date]
        if len(rows)<25 or [r['date'] for r in rows[-25:]]!=required:continue
        setups=evaluate_setups(rows);setup=selected_setup(setups)
        confirmed+=bool(setups)
        if not passed or not setup or not setup['plan']['plan_ready']:continue
        candidates.append(dict(code=code,name=sample['names'].get(code,code),close=rows[-1]['close'],category='策略候选',
            strategy_version=SETUP_VERSION,market_pass=True,plan_ready=True,trade_plan=setup['plan']))
    return dict(signal_date=signal_date,generated_at=signal_date+'T16:00:00+08:00',strategy_version=SETUP_VERSION,
        confirmed_count=confirmed,market_pass=passed,strategy_candidates=candidates)


def dual_radar(sample,signal_date,*,continuation_only=False):
    radar=setups_radar(sample,signal_date)
    if continuation_only:radar['strategy_candidates']=[]
    bench=[b for b in sample['benchmark'] if b['date']<=signal_date]
    existing={r['code'] for r in radar['strategy_candidates']};extra=[]
    for code,full in sample['daily'].items():
        if code in existing:continue
        name=sample['names'].get(code,code)
        try:P.stock_code(code)
        except ValueError:continue
        rows=[r for r in full if r['date']<=signal_date]
        plan=C.build_plan(rows,bench,name)
        if plan:extra.append(dict(code=code,name=name,close=rows[-1]['close'],category='策略候选',
            market_pass=True,plan_ready=True,trade_plan=plan))
    extra.sort(key=lambda r:(-r['trade_plan']['relative_strength'],-r['trade_plan']['avg20_turnover'],r['code']))
    radar['strategy_candidates'].extend(extra[:C.MAX_CANDIDATES])
    radar.update(continuation_market_pass=C.market_allowed(bench),continuation_enabled=True,entry_revision=C.ENTRY_REVISION)
    return radar


def reclaim_radar(sample,signal_date,*,include_legacy=False):
    radar=setups_radar(sample,signal_date)
    if not include_legacy:radar['strategy_candidates']=[]
    existing={r['code'] for r in radar['strategy_candidates']};extra=[]
    bench=[b for b in sample['benchmark'] if b['date']<=signal_date]
    for code,full in sample['daily'].items():
        if code in existing:continue
        try:P.stock_code(code)
        except ValueError:continue
        rows=[r for r in full if r['date']<=signal_date];name=sample['names'].get(code,code)
        plan=T.build_plan(rows,bench,name)
        if plan:extra.append(dict(code=code,name=name,close=rows[-1]['close'],category='策略候选',market_pass=True,plan_ready=True,trade_plan=plan))
    extra.sort(key=lambda r:(r['trade_plan']['pullback_distance'],-r['trade_plan']['relative_strength'],r['code']))
    radar['strategy_candidates'].extend(extra[:T.MAX_CANDIDATES])
    radar.update(trend_reclaim_enabled=True,trend_reclaim_market_pass=T.market_allowed(bench),entry_revision=T.ENTRY_REVISION)
    return radar


def reversal_radar(sample,signal_date,*,include_legacy=True,version=M.VERSION):
    radar=setups_radar(sample,signal_date)
    if not include_legacy:radar['strategy_candidates']=[]
    existing={r['code'] for r in radar['strategy_candidates']};extra=[]
    bench=[b for b in sample['benchmark'] if b['date']<=signal_date]
    for code,full in sample['daily'].items():
        if code in existing:continue
        try:P.stock_code(code)
        except ValueError:continue
        rows=[r for r in full if r['date']<=signal_date];name=sample['names'].get(code,code)
        plan=M.build_plan(rows,bench,name,version=version)
        if plan:extra.append(dict(code=code,name=name,close=rows[-1]['close'],category='策略候选',market_pass=True,plan_ready=True,trade_plan=plan))
    extra.sort(key=lambda r:(-r['trade_plan']['close_location'],r['trade_plan']['rsi2'],-r['trade_plan']['avg20_turnover'],r['code']))
    radar['strategy_candidates'].extend(extra[:M.MAX_CANDIDATES])
    radar.update(oversold_reversal_enabled=True,oversold_reversal_market_pass=M.market_allowed(bench),entry_revision=version)
    return radar


def bar_observations(history,minute_map,names,bar_time,*,at_close=False):
    """已完成收盘只能观察；开盘执行只读取当根open和已完成前缀。"""
    finish=datetime.fromisoformat(bar_time).replace(tzinfo=P.BJ)
    boundary=finish if at_close else finish-timedelta(minutes=15)
    stamp=int(boundary.timestamp()*1000)+(1 if at_close else 2)
    observations={}
    for code,rows in history.items():
        bar=minute_map.get((code,bar_time))
        if not bar or not rows:continue
        known=[minute_map[(code,bar_time[:10]+' '+e)] for e in END_TIMES
               if (code,bar_time[:10]+' '+e) in minute_map and
               datetime.fromisoformat(bar_time[:10]+'T'+e).replace(tzinfo=P.BJ)<=boundary]
        volume=sum(float(r['volume']) for r in known)
        if volume<=0:continue
        px=float(bar['close'] if at_close else bar['open']);prior=rows[-1]['close']
        ratio=.2 if code.startswith(('300','301','688')) else .1
        lo,hi=P.money(prior*(1-ratio)),P.money(prior*(1+ratio))
        if not lo<=px<=hi:continue
        observations[code]=dict(daily=rows,quote=dict(symbol=code,source=HIST_SOURCE,name=names.get(code,code),price=px,
            bid=px if px>lo*1.005 else None,ask=px if px<hi*.995 else None,volume=volume,previous_close=prior,quote_at=stamp,lower_limit=lo,upper_limit=hi),minutes=known)
    return stamp,observations


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
            history={code:rows for code,rows in history.items() if rows and rows[-1]['date']==previous}
            for end in END_TIMES:
                now,observations=bar_observations(history,bars,sample['names'],d+' '+end)
                account.step(radar,calendar[:index+1],observations,now=now)
                now,observations=bar_observations(history,bars,sample['names'],d+' '+end,at_close=True)
                account.step(radar,calendar[:index+1],observations,now=now,prepare_only=True)
            # Valuation only: historical daily close is now known, without generating a fill.
            with account.transaction() as state:
                for p in state['positions']:
                    close=next((r['close'] for r in sample['daily'][p['code']] if r['date']==d),None)
                    if close is not None:p.update(last_price=close,quote_at=int(datetime.fromisoformat(d+'T15:00:00+08:00').timestamp()*1000))
            out=account.snapshot(now=now,include_all_trades=True);curve.append(dict(date=d,equity=out['equity']))
        out=account.snapshot(now=now,include_all_trades=True)
    summary,months=metrics(curve,out['trades'],out['fees'])
    return dict(replay_version=REPLAY_VERSION,config=config,candidate_rule=candidate_rule,diagnostics=diagnostics,summary=summary,monthly=months,trades=out['trades'],curve=curve,open_positions=out['positions'],incomplete_minute_days=missing_days)


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
    for key,label,cfg,factory in [
        ('continuation_v1','研究：强势延续确认 / 结构跟随',config,lambda s,d:dual_radar(s,d,continuation_only=True)),
        ('dual_v3','研究：回踩＋强势延续',config,dual_radar),
        ('dual_v3_cost_double','研究双入口：费税与滑点翻倍',stress,dual_radar)]:
        results.append(dict(replay(sample,cfg,radar_factory=factory),case=key,label=label,strategy_version=C.ENTRY_REVISION))
    for key,label,cfg in [('trend_reclaim_v1','研究：日线趋势回踩收复',config),('trend_reclaim_cost_double','研究：趋势回踩收复 / 成本翻倍',stress)]:
        results.append(dict(replay(sample,cfg,radar_factory=lambda s,d:reclaim_radar(s,d,include_legacy=True)),case=key,label=label,strategy_version=T.VERSION))
    encoded=json.dumps(sample,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    return dict(version='a-candidate-research-v3',replay_version=REPLAY_VERSION,results=results,sample_sha256=hashlib.sha256(encoded).hexdigest(),
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
            sample=collect(months,size,progress);progress('正在按相同账本规则复盘既有策略和研究候选')
            result=compare(sample,config);CACHE.mkdir(parents=True,exist_ok=True)
            (CACHE/'latest-result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
            with self.lock:self.jobs[key].update(status='done',result=result)
        except Exception as exc:
            with self.lock:self.jobs[key].update(status='failed',error=str(exc)[:240])
    def snapshot(self,key=None):
        value=None
        with self.lock:
            if key:
                if key not in self.jobs:raise KeyError('复盘任务不存在或服务已重启')
                value=dict(self.jobs[key])
        if value is None:
            file=CACHE/'latest-result.json'
            value=dict(status='idle',result=json.loads(file.read_text()) if file.exists() else None)
        result=value.get('result')
        value.update(current_replay_version=REPLAY_VERSION,needs_rerun=bool(result and result.get('replay_version')!=REPLAY_VERSION))
        return value


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
