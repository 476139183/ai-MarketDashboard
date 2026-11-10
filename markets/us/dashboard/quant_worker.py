"""CLI for isolated research only. No live broker/account classes are used."""
import argparse
from datetime import datetime,timedelta
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import a_paper_research as A
import futures_backtest as F
from quant_research import normalize


def stock_id(code):return code+('.XSHG' if code.startswith(('6',)) else '.XSHE')


def risk_lots(equity,distance,price,cfg):
    if not all(math.isfinite(x) and x>0 for x in (equity,distance,price,cfg['multiplier'],cfg['margin_rate'])):return 0
    return min(int(equity*.005/(distance*cfg['multiplier'])),int(equity*cfg['max_margin_fraction']/(price*cfg['multiplier']*cfg['margin_rate'])))


def load_stock(sample_override=None):
    path=ROOT/'.cache/a-paper-research/sample-latest.json.gz'
    if sample_override is None:
        if not path.is_file():raise ValueError('本地A股研究样本不存在，请先采集历史样本')
        with gzip.open(path,'rt') as f:s=json.load(f)
    else:s=sample_override
    daily={stock_id(c):r for c,r in s['daily'].items()};minutes={}
    for code,rows in s['minutes'].items():
        days={}
        for r in rows:days.setdefault(r['time'][:10],[]).append(r)
        minutes[stock_id(code)]=[r for d,group in days.items() if s['start']<=d<=s['end'] and len(group)==16 and {b['time'][11:16] for b in group}==set(A.END_TIMES) for r in group]
    p=dict(market='a_share',daily=daily,minutes=minutes,calendar=[r['date'] for r in s['benchmark']],start=s['start'],end=s['end'],capital=100000,config=dict(A.P.DEFAULTS))
    return s,p


def run_stock(cfg,sample_override=None):
    import rqalpha_local as L
    from rqalpha.api import subscribe,get_position,get_positions,order_shares,get_open_orders
    from rqalpha.model.order import LimitOrder
    s,p=load_stock(sample_override);p['cost_scale']=cfg['cost_scale'];counts=dict(candidate_signals=0,ready_plans=0,market_pass_days=0)
    def init(ctx):
        subscribe(list(p['daily']));ctx.plans={};ctx.used=set();ctx.pending={};ctx.owned={}
    def before(ctx):
        d=ctx.now.date().isoformat();calendar=[x for x in p['calendar'] if x<=d]
        r=A.setups_radar(s,calendar[-2]) if cfg['strategy']=='setups_v2' else A.reconstructed_radar(s,calendar[-2],candidate_rule='expanded' if cfg['strategy']=='expanded' else 'trend')
        counts['market_pass_days']+=r['market_pass'];counts['candidate_signals']+=len(r['strategy_candidates']);counts['ready_plans']+=sum(x['plan_ready'] for x in r['strategy_candidates'])
        stamp=int(ctx.now.replace(tzinfo=A.P.BJ).timestamp()*1000)
        ctx.plans={stock_id(x['code']):x for x in A.P.approved_plans(r,calendar[-2],stamp)};ctx.pending={}
    def handle(ctx,bars):
        env=L.Environment.get_instance();dt=ctx.now;d=dt.date().isoformat()
        for symbol,posplan in list(ctx.owned.items()):
            position=get_position(symbol)
            if position.quantity<=0:continue
            if position.closable<=0 or dt not in L.ACTIVE['source'].points[symbol]:continue
            px=bars[symbol].close;history=[r for r in p['daily'][symbol] if r['date']<d]
            position_data=dict(stop=posplan['stop'],target=posplan['target'],entry_day=posplan['entry_day'],costs=p['config'])
            if A.P.ASharePaperAccount.exit_reason(position_data,{'price':px},history):order_shares(symbol,-position.closable)
        outstanding={o.order_book_id for o in get_open_orders()}
        for symbol,plan in ctx.plans.items():
            if dt not in L.ACTIVE['source'].points[symbol] or symbol in outstanding or plan['key'] in ctx.used:continue
            if get_position(symbol).quantity>0:continue
            px=bars[symbol].close;filled=A.P.ASharePaperAccount.fill(px,True,dict(p['config'],slippage_ticks=cfg['cost_scale']))
            if not plan['lower']<=px<=plan['upper'] or not plan['lower']<=filled<=plan['upper']:
                ctx.pending.pop(symbol,None);continue
            if symbol not in ctx.pending:ctx.pending[symbol]=dt;continue
            if dt<=ctx.pending[symbol]:continue
            if sum(x.quantity>0 for x in get_positions())>=p['config']['max_positions']:break
            available=min(p['config']['per_stock_budget'],env.portfolio.cash)
            shares=min(100000,int(available/filled/100)*100)
            while shares>=A.P.minimum_shares(plan['code']):
                fees=A.P.ASharePaperAccount.costs(shares*filled,True,{k:v*cfg['cost_scale'] if k in ('commission_rate','min_commission','transfer_rate','stamp_rate') else v for k,v in p['config'].items()})['total']
                if shares*filled+fees<=available:break
                shares-=100
            if shares<A.P.minimum_shares(plan['code']):continue
            order_shares(symbol,shares,style=LimitOrder(plan['upper']))
            if get_position(symbol).quantity>0:ctx.used.add(plan['key']);ctx.owned[symbol]=dict(plan,entry_day=d)
    out=L.run_local(p,handle,init,before);out['diagnostics']=counts;out['execution_config']=p['config']
    out['data']=dict(start=p['start'],end=p['end'],symbols=len(p['daily']),sample_sha256=hashlib.sha256(json.dumps(s,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest())
    out['assumptions']=A.LIMITATIONS+['RQAlpha实际撮合与T+1，分钟事件仅暴露开盘采样价；每日收盘仅用于估值。','毛盈亏、税费由独立框架处理；仍缺历史公司行动与实际盘口。']
    return out


def future_payload(cfg):
    file=ROOT/'docs/reports/futures-backtest-2026-11-08'/(cfg['symbol']+'.json');archive=json.loads(file.read_text());config=archive['config']
    actual=hashlib.sha256(json.dumps(archive['sample'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if actual!=archive['sample_sha256']:raise ValueError('期货归档样本指纹不一致，拒绝运行')
    delivery=f"20{cfg['symbol'][-4:-2]}-{cfg['symbol'][-2:]}-01"
    rows=[dict(date=datetime.fromtimestamp(b['time']/1000,F.F.BJ).date().isoformat(),open=b['open'],high=b['high'],low=b['low'],close=b['close'],volume=b.get('volume',1) or 1) for b in archive['sample'] if b.get('complete')]
    rows=[r for r in rows if r['date']<delivery]
    if len(rows)<62:raise ValueError('月份合约样本不足60根预热及后续交易日')
    openings=[dict(time=r['date']+' 09:15',open=r['open'],high=r['open'],low=r['open'],close=r['open'],volume=r['volume']) for r in rows]
    return archive,dict(market='futures',daily={cfg['symbol']:rows},minutes={cfg['symbol']:openings},calendar=[r['date'] for r in rows],start=rows[60]['date'],end=rows[-1]['date'],capital=config['capital'],config=config,cost_scale=cfg['cost_scale'])


def run_future(cfg):
    import rqalpha_local as L
    from rqalpha.api import subscribe,get_position,buy_open,sell_open,buy_close,sell_close,get_open_orders
    from rqalpha.const import POSITION_DIRECTION
    archive,p=future_payload(cfg);symbol=cfg['symbol'];conf=p['config'];counts=dict(margin_skips=0,risk_skips=0,signals=0)
    def init(ctx):subscribe(symbol);ctx.side=0;ctx.stop=None;ctx.pending_distance=None
    def handle(ctx,bars):
        price=bars[symbol].close;day=ctx.now.date().isoformat()
        history=[dict(r,time=i,complete=True) for i,r in enumerate(p['daily'][symbol]) if r['date']<day]
        long=get_position(symbol,POSITION_DIRECTION.LONG);short=get_position(symbol,POSITION_DIRECTION.SHORT)
        side=1 if long.quantity else -1 if short.quantity else 0
        target=F.signal(cfg['strategy'],history,side);counts['signals']+=bool(target)
        if get_open_orders():return
        position=long if side==1 else short
        if side:
            if ctx.side!=side:
                ctx.stop=position.avg_price-side*(ctx.pending_distance or 2*F.atr(history));ctx.side=side
            if target!=side or side*(price-ctx.stop)<=0 or side*(history[-1]['close']-ctx.stop)<=0:
                (sell_close if side==1 else buy_close)(symbol,position.quantity)
            return
        ctx.side=0
        if not target:return
        equity=L.Environment.get_instance().portfolio.total_value;distance=2*F.atr(history)
        lots=conf['lots']
        if cfg['sizing']=='risk':
            lots=risk_lots(equity,distance,price,conf)
            if lots<1:counts['risk_skips']+=1;return
        if lots*price*conf['multiplier']*conf['margin_rate']>equity*conf['max_margin_fraction']:counts['margin_skips']+=1;return
        ctx.pending_distance=distance
        (buy_open if target==1 else sell_open)(symbol,lots)
        opened=get_position(symbol,POSITION_DIRECTION.LONG if target==1 else POSITION_DIRECTION.SHORT)
        if opened.quantity:ctx.side=target;ctx.stop=opened.avg_price-target*distance
    out=L.run_local(p,handle,init);out['diagnostics']=counts;out['execution_config']=dict(conf,risk_fraction=.005 if cfg['sizing']=='risk' else None)
    out['data']=dict(start=p['start'],end=p['end'],symbols=[symbol],sample_sha256=archive['sample_sha256'])
    out['assumptions']=archive['limitations']+['RQAlpha独立日线开盘采样：仅使用前一日完成日线决定，当前开盘撮合；检查开盘跳空及前收保护触发，非旧引擎盘中高低点保护。',
        '期货逐日按收盘盯市；固定保证金和每手费用假设，未还原完整历史结算与涨跌停队列。',
        'risk仓位是研究对照：按2ATR控制单笔计划风险为权益0.5%，并检查发单时50%保证金占用；跳空可能超过计划风险。']
    return out


def run_tq(cfg):
    if not os.getenv('TQ_USER') or not os.getenv('TQ_PASSWORD'):raise ValueError('天勤账号未配置，不能运行历史行情回测')
    from tqsdk import TqApi,TqAuth,TqSim,TqBacktest,BacktestFinished,TargetPosTask
    archive,p=future_payload(cfg)
    symbols={'RB2701':'SHFE.rb2701','M2701':'DCE.m2701','AU2612':'SHFE.au2612','SC2701':'INE.sc2701'}
    acc=TqSim(init_balance=p['capital'])
    # This path deliberately never constructs a live trading account.
    with TqApi(acc,auth=TqAuth(os.environ['TQ_USER'],os.environ['TQ_PASSWORD']),backtest=TqBacktest(start_dt=datetime.fromisoformat(p['start']).date(),end_dt=datetime.fromisoformat(p['end']).date())) as api:
        symbol=symbols[cfg['symbol']];daily=api.get_kline_serial(symbol,86400,data_length=80);quote=api.get_quote(symbol);position=api.get_position(symbol)
        acc.set_commission(symbol,p['config']['fee_fixed'])
        task=TargetPosTask(api,symbol,price='ACTIVE');last=None;entry_side=0;stop=None;distance=None
        try:
            while True:
                api.wait_update()
                side=1 if position.pos_long else -1 if position.pos_short else 0
                if side and side!=entry_side and distance:
                    entry=position.open_price_long if side==1 else position.open_price_short
                    if math.isfinite(entry):entry_side=side;stop=entry-side*distance
                if side and stop is not None and math.isfinite(quote.last_price) and side*(quote.last_price-stop)<=0:
                    task.set_target_volume(0);continue
                if not side:entry_side=0
                if not api.is_changing(daily.iloc[-1],'datetime'):continue
                rows=[dict(time=int(r.datetime/1e6),open=float(r.open),high=float(r.high),low=float(r.low),close=float(r.close),complete=True) for _,r in daily.iloc[:-1].iterrows() if math.isfinite(r.close) and math.isfinite(r.datetime)]
                if len(rows)<60 or rows[-1]['time']==last:continue
                last=rows[-1]['time'];target=F.signal(cfg['strategy'],rows,side);distance=2*F.atr(rows)
                if not math.isfinite(quote.last_price) or not math.isfinite(quote.volume_multiple):continue
                margin=quote.last_price*quote.volume_multiple*p['config']['margin_rate'];acc.set_margin(symbol,margin)
                if not side and target and margin*p['config']['lots']>acc.get_account().balance*p['config']['max_margin_fraction']:continue
                task.set_target_volume(target*p['config']['lots'])
        except BacktestFinished:pass
        return dict(engine='tqsdk',status='completed',statistics=dict(acc.tqsdk_stat),trade_log=acc.trade_log,
            assumptions=['天勤服务端历史行情与TqSim撮合，未迁移前向账户；此入口尚未在本机凭据下完成联网验收。'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    for name in ('engine','market','strategy','symbol','cost-scale','sizing'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();cfg=normalize(dict(engine=args.engine,market=args.market,strategy=args.strategy,symbol=args.symbol,cost_scale=int(args.cost_scale),sizing=args.sizing))
    out=run_tq(cfg) if cfg['engine']=='tqsdk' else run_stock(cfg) if cfg['market']=='a_share' else run_future(cfg)
    out.update(config=cfg,market=cfg['market'],engine_version=importlib.metadata.version(cfg['engine']),generated_at=datetime.now().astimezone().isoformat(),independent_account=True)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False,default=str))
