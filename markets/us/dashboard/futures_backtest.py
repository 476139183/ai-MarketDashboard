"""Fixed-rule daily futures research. No continuous-contract fills or account writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import math
import re
import threading
import time
import uuid

import futures_cn as F

VERSION = 'cn-daily-v1'
STRATEGIES = {'sma': '双均线20/60方向基准', 'donchian': '唐奇安20/10收盘突破'}
SPECS = {'RB':(10,1),'AU':(1000,.02),'AG':(15,1),'CU':(5,10),'AL':(5,5),
         'SC':(1000,.1),'FU':(10,1),'M':(10,1),'Y':(10,2),'P':(10,2),'C':(10,1),'I':(100,.5)}
DEFAULTS = dict(capital=100000, lots=1, multiplier=10, tick_size=1, margin_rate=.15,
                max_margin_fraction=.5, fee_fixed=3, fee_rate=0, close_today_factor=1,
                slippage_ticks=1, start='', end='')
REFERENCES = [
    {'name':'VeighNa双均线示例','url':'https://github.com/vnpy/vnpy_ctastrategy/blob/main/vnpy_ctastrategy/strategies/double_ma_strategy.py'},
    {'name':'VeighNa海龟信号示例','url':'https://github.com/vnpy/vnpy_ctastrategy/blob/main/vnpy_ctastrategy/strategies/turtle_signal_strategy.py'},
    {'name':'Backtrader期货费用模型','url':'https://www.backtrader.com/docu/commission-schemes/commission-schemes/'},
    {'name':'TqSdk回测模式','url':'https://github.com/shinnytech/tqsdk-python/blob/master/doc/usage/backtest.rst'},
]


def normalize_config(params):
    if not isinstance(params,dict) or set(params)-set(DEFAULTS)-{'symbol'}:
        raise ValueError('回测参数包含未知字段')
    symbol=F.validate_symbol(params.get('symbol','RB2701'))
    if not re.fullmatch(r'[A-Z]{1,2}[0-9]{4}',symbol):
        raise ValueError('回测只支持四位月份合约，如RB2701；连续合约不能用于成交回测')
    root=re.match(r'[A-Z]+',symbol)[0]
    cfg=dict(DEFAULTS,symbol=symbol,multiplier=SPECS[root][0],tick_size=SPECS[root][1])
    cfg.update(params)
    cfg['symbol']=symbol
    for key in set(DEFAULTS)-{'start','end'}:
        if isinstance(cfg[key],bool):raise ValueError(key+'不能是布尔值')
        try:value=float(cfg[key])
        except (ValueError,TypeError):raise ValueError(key+'必须是数值') from None
        if not math.isfinite(value):raise ValueError(key+'必须为有限数')
        cfg[key]=value
    for key in ('capital','multiplier','tick_size'):
        if not (1 if key=='capital' else 1e-8)<=cfg[key]<=1e9:raise ValueError(key+'超出范围')
    for key in ('lots','slippage_ticks'):
        if cfg[key]!=int(cfg[key]) or not (1 if key=='lots' else 0)<=cfg[key]<=10000:
            raise ValueError(key+'必须是范围内的整数')
        cfg[key]=int(cfg[key])
    if not 0<cfg['margin_rate']<=1 or not 0<cfg['max_margin_fraction']<=1:
        raise ValueError('保证金比例与占用上限应在0至1之间')
    if not 0<=cfg['fee_fixed']<=100000 or not 0<=cfg['fee_rate']<=.1 or not 0<=cfg['close_today_factor']<=100:
        raise ValueError('费用参数超出范围')
    for key in ('start','end'):
        if cfg[key]:
            try:cfg[key]=datetime.strptime(str(cfg[key]),'%Y-%m-%d').date().isoformat()
            except (ValueError,TypeError):raise ValueError('日期格式应为YYYY-MM-DD') from None
        else:cfg[key]=''
    if cfg['start'] and cfg['end'] and cfg['start']>cfg['end']:raise ValueError('起始日期晚于结束日期')
    return cfg


def atr(history):
    return sum(max(b['high']-b['low'],abs(b['high']-p['close']),abs(b['low']-p['close']))
               for p,b in zip(history[-15:-1],history[-14:]))/14


def signal(strategy, history, side):
    if len(history)<60:return 0
    if strategy=='sma':
        fast=sum(b['close'] for b in history[-20:])/20
        slow=sum(b['close'] for b in history[-60:])/60
        return 1 if fast>slow else -1 if fast<slow else 0
    if strategy!='donchian':raise ValueError('未知期货回测策略')
    price=history[-1]['close'];prior=history[-21:-1]
    if price>max(b['high'] for b in prior):return 1
    if price<min(b['low'] for b in prior):return -1
    exits=history[-11:-1]
    if side==1 and price<min(b['low'] for b in exits):return 0
    if side==-1 and price>max(b['high'] for b in exits):return 0
    return side


def replay(bars, cfg, strategy, trade_start=None, cost_scale=1):
    cfg=normalize_config(cfg)
    if strategy not in STRATEGIES:raise ValueError('未知期货回测策略')
    symbol=cfg['symbol'];delivery=f'20{symbol[-4:-2]}-{symbol[-2:]}-01'
    def day(bar):return datetime.fromtimestamp(bar['time']/1000,F.BJ).date().isoformat()
    data=[]
    for bar in bars:
        if not bar.get('complete'):continue
        if day(bar)>=delivery or cfg['end'] and day(bar)>cfg['end']:continue
        values=[bar.get(k) for k in ('time','open','high','low','close')]
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in values):raise ValueError('回测价格无效')
        if not bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high']:raise ValueError('回测高低价无效')
        if data and bar['time']<=data[-1]['time']:raise ValueError('回测K线必须严格按时间递增')
        data.append(bar)
    first=next((i for i,b in enumerate(data) if i>=60 and (not cfg['start'] or day(b)>=cfg['start'])
                and (trade_start is None or b['time']>=trade_start)),len(data))
    if len(data)-first<2:raise ValueError('缺少60根预热线或至少2根可回测完整日线')
    qty=cfg['lots'];mult=cfg['multiplier'];tick=cfg['tick_size'];capital=cfg['capital']
    cash=capital;position=None;trades=[];curve=[dict(time=data[first-1]['time'],equity=capital)]
    fees=0.;slippage=0.;rejected=0;halt=False

    def fill(raw, direction):
        ticks=math.ceil(raw/tick-1e-9) if direction>0 else math.floor(raw/tick+1e-9)
        price=(ticks+direction*cfg['slippage_ticks']*cost_scale)*tick
        if price<=0:raise ValueError('滑点使成交价非正，请核对最小价位')
        return price

    def fee(price, factor=1):return qty*(cfg['fee_fixed']+price*mult*cfg['fee_rate'])*factor*cost_scale

    def close(raw, bar, reason, signal_at=None):
        nonlocal cash,position,fees,slippage
        p=position;price=fill(raw,-p['side']);same=day(bar)==p['entry_day']
        cost=fee(price,cfg['close_today_factor'] if same else 1)
        gross=(price-p['entry_price'])*p['side']*qty*mult
        cash+=gross-cost;fees+=cost;slippage+=abs(price-raw)*qty*mult
        trades.append(dict(direction='long' if p['side']>0 else 'short',lots=qty,
                           entry_at=p['entry_at'],exit_at=bar['time'],signal_at=p['signal_at'],exit_signal_at=signal_at,
                           entry_price=p['entry_price'],exit_price=price,raw_entry=p['raw_entry'],raw_exit=raw,
                           entry_fee=p['entry_fee'],exit_fee=cost,close_today=same,
                           gross_before_cost=(raw-p['raw_entry'])*p['side']*qty*mult,
                           net_pnl=gross-p['entry_fee']-cost,reason=reason))
        position=None

    for i in range(first,len(data)):
        bar=data[i];history=data[:i];last=i==len(data)-1;was_halted=halt
        tradable=isinstance(bar.get('volume'),(int,float)) and math.isfinite(bar['volume']) and bar['volume']>0 and bar['high']>bar['low']
        desired=signal(strategy,history,position['side'] if position else 0)
        stopped=False
        if position and tradable:
            side=position['side'];stop=position['stop']
            if was_halted:close(bar['open'],bar,'margin_exit',history[-1]['time'])
            elif side*(bar['open']-stop)<=0:
                close(bar['open'],bar,'gap_stop');stopped=True
            elif desired!=side:close(bar['open'],bar,'signal',history[-1]['time'])
        if not position and desired and tradable and not last and not stopped and not halt:
            price=fill(bar['open'],desired);cost=fee(price)
            margin=price*mult*qty*cfg['margin_rate']
            if cash>0 and margin<=cash*cfg['max_margin_fraction'] and margin+cost<=cash:
                cash-=cost;fees+=cost;slippage+=abs(price-bar['open'])*qty*mult
                position=dict(side=desired,entry_price=price,entry_at=bar['time'],entry_day=day(bar),
                              signal_at=history[-1]['time'],entry_fee=cost,raw_entry=bar['open'],
                              stop=price-desired*2*atr(history))
            else:rejected+=1
        if position and tradable:
            side=position['side'];stop=position['stop']
            if side==1 and bar['low']<=stop or side==-1 and bar['high']>=stop:
                close(stop,bar,'atr_stop')
        if position and last and tradable:close(bar['close'],bar,'sample_end')
        equity=cash+(bar['close']-position['entry_price'])*position['side']*qty*mult if position else cash
        if equity<=0 or position and equity<bar['close']*mult*qty*cfg['margin_rate']:halt=True
        curve.append(dict(time=bar['time'],equity=equity))
    peak=capital;maxdd=0
    for point in curve:
        peak=max(peak,point['equity'])
        maxdd=max(maxdd,(peak-point['equity'])/peak*100)
    profits=sum(t['net_pnl'] for t in trades if t['net_pnl']>0)
    losses=-sum(t['net_pnl'] for t in trades if t['net_pnl']<0)
    warnings=[]
    if len(trades)<10:warnings.append('完整交易不足10笔，结果仅供小样本研究。')
    if rejected:warnings.append(f'{rejected}次开仓因保证金或占用上限被拒绝。')
    if halt:warnings.append('发生权益或保证金不足，已停止新开仓；退出按下一可成交开盘模拟。')
    if position:warnings.append('末根不可撮合，保留未平头寸；期末权益含浮盈亏且尚未扣未来平仓费用。')
    if cfg['start'] and day(data[0])>cfg['start']:warnings.append('请求开始日期早于源覆盖，实际回测从可用数据及预热之后开始。')
    return dict(strategy=strategy,label=STRATEGIES[strategy],trades=trades,curve=curve,warnings=warnings,cost_scale=cost_scale,
                open_position=position,summary=dict(initial_capital=capital,end_equity=curve[-1]['equity'],
                return_pct=(curve[-1]['equity']/capital-1)*100,max_drawdown_pct=maxdd,
                trade_count=len(trades),win_rate_pct=100*sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else None,
                profit_factor=profits/losses if losses else None,fees=fees,slippage_cost=slippage,
                rejected_entries=rejected,margin_halted=halt,start_at=data[first]['time'],end_at=data[-1]['time'],
                bars=len(data)-first,warmup_bars=first))


def compare(bars, params):
    cfg=normalize_config(params)
    if sum(bool(b.get('complete')) for b in bars)<80:raise ValueError('日线不足80根，无法提供两个固定基准的有效比较')
    results=[]
    for strategy in STRATEGIES:
        base=replay(bars,cfg,strategy)
        if base['summary']['bars']<20:raise ValueError('日期或交割边界筛选后不足20根可回测日线')
        # Re-run the last 30% from a fresh, flat account with only earlier bars for warmup.
        curve=base['curve'][1:];tail_at=curve[int(len(curve)*.7)]['time']
        base['stress']=replay(bars,cfg,strategy,cost_scale=2)
        base['tail']=replay(bars,cfg,strategy,tail_at)
        results.append(base)
    encoded=json.dumps(bars,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return dict(version=VERSION,config=cfg,results=results,references=REFERENCES,
                sample_sha256=hashlib.sha256(encoded).hexdigest(),sample=bars,
                limitations=['单月份合约日线；没有自动换月或连续合约成交。',
                             '末根按可成交收盘价结束；无成交量或一字线保守不撮合。',
                             '收盘权益回撤；未完整重建日内强平、涨跌停队列、历史动态保证金及结算规则。',
                             '费用与保证金为输入的固定研究假设，非历史交易所或券商费率。',
                             '尾段重跑不是严格样本外验证；未优化参数，也不自动选择上线策略。'])


def load_daily(symbol):
    url='https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.getDailyKLine?symbol='+symbol
    return F.parse_bars(F.read_source(url),'day',time.time()*1000)


class BacktestJobs:
    def __init__(self,fetch=load_daily):
        self.fetch=fetch;self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='futures-backtest')
        self.lock=threading.Lock();self.jobs={}

    def start(self,params):
        cfg=normalize_config(params)
        with self.lock:
            if sum(j['status']=='running' for j in self.jobs.values())>=2:raise ValueError('回测任务正在运行，请稍后再试')
            if len(self.jobs)>=16:
                oldest=next(k for k,v in self.jobs.items() if v['status']!='running');self.jobs.pop(oldest)
            job=dict(id=uuid.uuid4().hex,status='running',config=cfg,created_at=int(time.time()*1000),error=None)
            self.jobs[job['id']]=job
        self.pool.submit(self.run,job['id'],cfg)
        return self.snapshot(job['id'])

    def run(self,job_id,cfg):
        try:
            bars=self.fetch(cfg['symbol']);result=compare(bars,cfg)
            with self.lock:self.jobs[job_id].update(status='done',result=result,finished_at=int(time.time()*1000))
        except Exception as exc:
            with self.lock:self.jobs[job_id].update(status='failed',error=str(exc)[:240],finished_at=int(time.time()*1000))

    def snapshot(self,job_id):
        with self.lock:
            if job_id not in self.jobs:raise KeyError('回测任务不存在或服务已重启，请重新运行')
            return dict(self.jobs[job_id])


JOBS=BacktestJobs()


if __name__=='__main__':
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description='用已导出的样本与参数离线复跑期货回测，不联网')
    parser.add_argument('--replay',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    recorded=json.loads(args.replay.read_text())
    output=compare(recorded['sample'],recorded['config'])
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))
    print(f"已离线复跑：{args.output}；样本指纹 {output['sample_sha256']}")
