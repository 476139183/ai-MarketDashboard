"""Local-data extension for the real RQAlpha engine, used only in isolated workers."""
from datetime import datetime,time,timedelta
from collections import defaultdict,deque
import math
import numpy as np
import pandas as pd
from rqalpha import run_func
from rqalpha.const import INSTRUMENT_TYPE,TRADING_CALENDAR_TYPE,POSITION_EFFECT,SIDE,COMMISSION_TYPE
from rqalpha.core.events import EVENT,Event
from rqalpha.environment import Environment
from rqalpha.interface import AbstractDataSource,AbstractMod,AbstractEventSource,AbstractTransactionCostDecider,TransactionCost
from rqalpha.model.instrument import Instrument
from rqalpha.data.base_data_source.storages import FuturesTradingParameters

ACTIVE=None


def date_int(dt):return int(dt.strftime('%Y%m%d%H%M%S'))


class LocalSource(AbstractDataSource):
    def __init__(self,payload):
        self.p=payload;self.daily=payload['daily'];self.daymaps={k:{r['date']:r for r in rows} for k,rows in self.daily.items()};self.points={};self.instruments={}
        cfg=payload.get('config',{});future=payload['market']=='futures'
        for symbol,rows in self.daily.items():
            data=dict(order_book_id=symbol,symbol=symbol,type='Future' if future else 'CS',round_lot=1 if future else 100,
                listed_date=rows[0]['date'],de_listed_date='2099-01-01',board_type='KSH' if symbol.startswith('688') else 'MainBoard',
                exchange=('DCE' if symbol.startswith('M') else 'INE' if symbol.startswith('SC') else 'SHFE') if future else 'XSHG' if symbol.endswith('XSHG') else 'XSHE',special_type='Normal',market_tplus=0 if future else 1,
                contract_multiplier=cfg.get('multiplier',1),underlying_symbol=''.join(c for c in symbol if c.isalpha()),
                trading_hours='09:00-11:30,13:30-15:00' if future else '09:30-11:30,13:00-15:00',settlement_method='CashSettlement',maturity_date='2099-01-01')
            self.instruments[symbol]=Instrument(data,lambda ins:cfg.get('tick_size',1))
            points={}
            for r in payload.get('minutes',{}).get(symbol,[]):
                dt=datetime.fromisoformat(r['time'])-timedelta(minutes=15)
                points[dt]=dict(date=dt.date().isoformat(),open=r['open'],close=r['open'],high=r['open'],low=r['open'],volume=r['volume'])
            self.points[symbol]=points
    def get_instruments(self,id_or_syms=None,types=None):
        ids=([id_or_syms] if isinstance(id_or_syms,str) else id_or_syms) if id_or_syms is not None else self.instruments
        return [self.instruments[s] for s in ids if s in self.instruments and (types is None or self.instruments[s].type in types)]
    def get_trading_calendars(self):
        dates=pd.DatetimeIndex(self.p['calendar'])
        return {TRADING_CALENDAR_TYPE.CN_STOCK:dates}
    def available_data_range(self,frequency):return datetime.fromisoformat(self.p['start']).date(),datetime.fromisoformat(self.p['end']).date()
    def get_bar(self,instrument,dt,frequency):
        symbol=instrument.order_book_id;d=dt.date().isoformat() if isinstance(dt,datetime) else dt.isoformat();rows=self.daily[symbol]
        if frequency=='1m':
            row=self.points[symbol].get(dt)
            if row is None:
                past=[r for r in rows if r['date']<d or r['date']==d and dt.time()>=time(15)]
                if not past:return None
                px=past[-1]['close'];row=dict(date=d,open=px,high=px,low=px,close=px,volume=1)
        else:row=self.daymaps[symbol].get(d)
        if row is None:return None
        previous=[r for r in rows if r['date']<d];prev=previous[-1]['close'] if previous else row['open']
        ratio=.2 if symbol.startswith(('300','301','688')) else .1
        cfg=self.p.get('config',{});limit_up,limit_down=(prev*10,prev*.01) if self.p['market']=='futures' else (round(prev*(1+ratio),2),round(prev*(1-ratio),2))
        return dict(row,datetime=date_int(dt if isinstance(dt,datetime) else datetime.combine(dt,time(15))),last=row['close'],prev_close=prev,
                    limit_up=limit_up,limit_down=limit_down,total_turnover=row['close']*row.get('volume',1),settlement=row['close'],open_interest=1)
    def get_dividend(self,ins):return None
    def get_split(self,ins):return None
    def get_share_transformation(self,symbol):return None
    def get_yield_curve(self,start_date,end_date,tenor=None):return pd.DataFrame({'0S':[0.]},index=[pd.Timestamp(start_date)])
    def get_settle_price(self,instrument,dt):return self.daymaps[instrument.order_book_id][str(dt)[:10]]['close']
    def is_suspended(self,symbol,dates):return [str(d)[:10] not in self.daymaps[symbol] for d in dates]
    def is_st_stock(self,symbol,dates):return [False]*len(dates)
    def history_bars(self,instrument,bar_count,frequency,fields,dt,skip_suspended=True,include_now=False,adjust_type='pre',adjust_orig=None):
        if frequency!='1d':raise ValueError('本地扩展只开放日线历史查询')
        d=dt.date().isoformat();rows=[r for r in self.daily[instrument.order_book_id] if r['date']<d or r['date']==d and include_now and dt.time()>=time(15)][-bar_count:]
        names=[fields] if isinstance(fields,str) else list(fields)
        out=np.array([tuple(int(r['date'].replace('-',''))*1000000 if k=='datetime' else r.get(k,0) for k in names) for r in rows],dtype=[(k,'u8' if k=='datetime' else 'f8') for k in names])
        return out[fields] if isinstance(fields,str) else out
    def get_futures_trading_parameters(self,instrument,dt):
        cfg=self.p.get('config',{});fee=cfg.get('fee_fixed',3)*self.p.get('cost_scale',1)
        return FuturesTradingParameters(fee,fee*cfg.get('close_today_factor',1),COMMISSION_TYPE.BY_VOLUME,fee,cfg.get('margin_rate',.15),cfg.get('margin_rate',.15))


class LocalEvents(AbstractEventSource):
    def __init__(self,p):self.p=p
    def events(self,start_date,end_date,frequency):
        for d in self.p['calendar']:
            day=datetime.fromisoformat(d)
            if not start_date<=day.date()<=end_date:continue
            pre=day.replace(hour=8,minute=45);yield Event(EVENT.BEFORE_TRADING,calendar_dt=pre,trading_dt=pre)
            times=sorted({t for points in ACTIVE['source'].points.values() for t in points if t.date()==day.date()}) if frequency=='1m' else [day.replace(hour=15)]
            for dt in times:yield Event(EVENT.BAR,calendar_dt=dt,trading_dt=dt)
            end=day.replace(hour=15,minute=30);yield Event(EVENT.AFTER_TRADING,calendar_dt=end,trading_dt=end)


class StockCosts(AbstractTransactionCostDecider):
    def calc(self,args):
        cfg=ACTIVE['p'].get('config',{});scale=ACTIVE['p'].get('cost_scale',1);amount=args.quantity*args.price
        return TransactionCost(round(max(cfg.get('min_commission',5)*scale,amount*cfg.get('commission_rate',.0003)*scale),2),
            round(amount*cfg.get('stamp_rate',.0005)*scale,2) if args.side==SIDE.SELL else 0.,round(amount*cfg.get('transfer_rate',.00001)*scale,2))


class BridgeMod(AbstractMod):
    def start_up(self,env,mod_config):
        source=LocalSource(ACTIVE['p']);ACTIVE['source']=source;env.set_data_source(source);env.set_event_source(LocalEvents(ACTIVE['p']))
        if ACTIVE['p']['market']=='a_share':env.set_transaction_cost_decider(INSTRUMENT_TYPE.CS,StockCosts())
        def trade(event):
            t=event.trade
            ACTIVE['fills'].append(dict(symbol=t.order_book_id,datetime=t.trading_datetime.isoformat(),side=t.side.name,
                position_effect=t.position_effect.name,quantity=t.last_quantity,price=t.last_price,fees=float(t.transaction_cost)))
        def rejected(event):ACTIVE['rejects'].append(str(getattr(event,'reason',getattr(event,'order','订单被拒绝'))))
        def daily(event):
            ACTIVE['curve'].append(dict(date=env.trading_dt.date().isoformat(),equity=float(env.portfolio.total_value)))
        def mark_close(event):
            # Update the framework's position marks only, after orders have expired.
            # No BAR event or new order is generated at the daily close.
            d=env.trading_dt.date().isoformat()
            for position in env.portfolio.get_positions():
                row=source.daymaps[position.order_book_id].get(d)
                if row is not None:position.update_last_price(row['close'])
        env.event_bus.add_listener(EVENT.TRADE,trade);env.event_bus.add_listener(EVENT.ORDER_CREATION_REJECT,rejected)
        env.event_bus.add_listener(EVENT.POST_AFTER_TRADING,mark_close)
        env.event_bus.add_listener(EVENT.POST_SETTLEMENT,daily)
    def tear_down(self,code,exception=None):return None


def load_mod():return BridgeMod()


def summarize(p,fills,curve,rejects):
    queues=defaultdict(deque);closed=[];mult=p.get('config',{}).get('multiplier',1) if p['market']=='futures' else 1
    for f in fills:
        opening=f['side']=='BUY' if p['market']=='a_share' else f['position_effect']=='OPEN'
        direction=1 if f['side']=='BUY' else -1
        if opening:queues[(f['symbol'],direction)].append(dict(f,remaining=f['quantity']));continue
        key=(f['symbol'],-direction);left=f['quantity']
        while left and queues[key]:
            entry=queues[key][0];qty=min(left,entry['remaining']);fee=entry['fees']*qty/entry['quantity']+f['fees']*qty/f['quantity']
            pnl=(f['price']-entry['price'])*key[1]*qty*mult-fee
            closed.append(dict(symbol=f['symbol'],entry_at=entry['datetime'],exit_at=f['datetime'],quantity=qty,net_pnl=round(pnl,2)))
            entry['remaining']-=qty;left-=qty
            if not entry['remaining']:queues[key].popleft()
    initial=p.get('capital',100000);peak=initial;dd=0
    for r in curve:peak=max(peak,r['equity']);dd=max(dd,(peak-r['equity'])/peak*100)
    final=curve[-1]['equity'] if curve else initial
    positions=[dict(symbol=s,direction=side,quantity=sum(x['remaining'] for x in q)) for (s,side),q in queues.items() if q]
    marked=sum((next(r['close'] for r in reversed(p['daily'][s]) if r['date']<=p['end'])-e['price'])*side*e['remaining']*mult-e['fees']*e['remaining']/e['quantity'] for (s,side),q in queues.items() for e in q)
    expected=initial+sum(t['net_pnl'] for t in closed)+marked
    if abs(expected-final)>.05:raise ValueError(f'独立引擎权益核对失败：框架{final:.2f}，逐笔及持仓{expected:.2f}')
    span=max(1,(datetime.fromisoformat(p['end'])-datetime.fromisoformat(p['start'])).days+1)
    return dict(initial_capital=initial,end_equity=final,return_pct=(final/initial-1)*100,max_drawdown_pct=dd,
        monthly_equivalent_pct=((final/initial)**(30.4375/span)-1)*100 if final>0 else None,
        closed_trades=len(closed),win_rate_pct=100*sum(t['net_pnl']>0 for t in closed)/len(closed) if closed else None,
        fees=sum(f['fees'] for f in fills),rejected_orders=len(rejects)),closed,positions


def run_local(payload,handle_bar,init=None,before_trading=None):
    global ACTIVE
    ACTIVE=dict(p=payload,source=None,fills=[],curve=[],rejects=[])
    minute=bool(payload.get('minutes'));capital=payload.get('capital',100000)
    config=dict(base=dict(start_date=payload['start'],end_date=payload['end'],frequency='1m' if minute else '1d',accounts={'stock' if payload['market']=='a_share' else 'future':capital},run_type='b',capital_gain_tax_rate=0),
        extra=dict(log_level='error'),mod=dict(sys_analyser=dict(enabled=False),sys_progress=dict(enabled=False),
        sys_simulation=dict(enabled=True,matching_type=payload.get('matching_type','current_bar'),volume_limit=False,price_limit=True,
            slippage_model='TickSizeSlippage',slippage=payload.get('cost_scale',1)),
        local_bridge=dict(enabled=True,lib=__name__,priority=10000)))
    funcs={k:v for k,v in dict(init=init,handle_bar=handle_bar,before_trading=before_trading).items() if v is not None}
    run_func(config=config,**funcs)
    summary,closed,positions=summarize(payload,ACTIVE['fills'],ACTIVE['curve'],ACTIVE['rejects'])
    return dict(engine='rqalpha',summary=summary,fills=ACTIVE['fills'],trades=closed,open_positions=positions,curve=ACTIVE['curve'],rejections=ACTIVE['rejects'])
