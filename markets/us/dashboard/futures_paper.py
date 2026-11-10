"""Independent CNY forward simulator for domestic month contracts; no broker calls."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time

import futures_cn as F
import futures_backtest as B

VERSION = 'cn-futures-paper-sma-v1'
INITIAL_BALANCE = 100000.0
CONFIG_KEYS = {'symbol','strategy','lots','multiplier','tick_size','margin_rate',
               'max_margin_fraction','fee_fixed','fee_rate','close_today_factor','slippage_ticks'}


def milliseconds():return int(time.time()*1000)


def normalize_config(params):
    if not isinstance(params,dict) or set(params)-CONFIG_KEYS:
        raise ValueError('期货模拟配置包含未知字段；本金不能重置')
    if params.get('strategy','sma') != 'sma':
        raise ValueError('期货模拟仅支持双均线20/60基准')
    cfg=B.normalize_config({k:v for k,v in params.items() if k!='strategy'})
    root=re.match(r'[A-Z]+',cfg['symbol'])[0]
    if (cfg['multiplier'],cfg['tick_size']) != B.SPECS[root]:
        raise ValueError('合约乘数或最小价位与品种预设不一致')
    return {**{k:v for k,v in cfg.items() if k in CONFIG_KEYS},'strategy':'sma'}


def day(stamp):return datetime.fromtimestamp(stamp/1000,F.BJ).date().isoformat()


def in_day_session(stamp):
    dt=datetime.fromtimestamp(stamp/1000,F.BJ)
    seconds=dt.hour*3600+dt.minute*60+dt.second
    return dt.weekday()<5 and any(a<=seconds<b for a,b in ((9*3600,10*3600+15*60),
           (10*3600+30*60,11*3600+30*60),(13*3600+30*60,15*3600)))


def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def quote_valid(packet,cfg,now):
    q=packet.get('quote') or {};ts=q.get('quote_at');price=q.get('price')
    return (packet.get('source')==F.SOURCE and packet.get('symbol')==cfg['symbol']
            and packet.get('period')=='day' and q.get('source')==F.SOURCE
            and q.get('symbol')==cfg['symbol'] and not q.get('continuous',True) and not q.get('error')
            and finite(price) and price>0 and finite(ts) and 0<=now-ts<=30000
            and finite(q.get('volume')) and q['volume']>0
            and day(ts)==day(now) and in_day_session(now) and in_day_session(ts))


def daily_signal(packet,now):
    bars=packet.get('candles') or []
    # A current source day anchors the preceding bars; old daily data cannot open a new position.
    if not bars or day(bars[-1]['time'])!=day(now):
        raise ValueError('等待源当日日线，不能用旧日线补造新信号')
    history=[b for b in bars if b.get('complete') and day(b['time'])<day(now)]
    if len(history)<60:raise ValueError('至少需要60根此前完整日线')
    last_time=0
    for bar in history:
        values=[bar.get(k) for k in ('time','open','high','low','close')]
        if not all(finite(v) and v>0 for v in values) or not bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high'] or bar['time']<=last_time:
            raise ValueError('期货日线价格或时序无效')
        last_time=bar['time']
    distance=2*B.atr(history)
    if not finite(distance) or distance<=0:raise ValueError('ATR无效，暂不生成信号')
    return dict(side=B.signal('sma',history,0),signal_at=history[-1]['time'],distance=distance)


class FuturesPaperAccount:
    def __init__(self,path):self.path=Path(path)

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=5)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS futures_paper_state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            saved=db.execute('SELECT body FROM futures_paper_state WHERE id=1').fetchone()
            state=json.loads(saved[0]) if saved else dict(version=VERSION,market='domestic_futures',currency='CNY',
                enabled=False,balance=INITIAL_BALANCE,config=normalize_config({}),positions=[],trades=[],
                pending=None,used_key=None,updated_at=None,activated_at=0,last_quote_at=0,
                error=None,note='尚未启用自动模拟',halted=False,fees=0.,realized_pnl=0.,total_trades=0,winning_trades=0)
            if state.get('version')!=VERSION or state.get('market')!='domestic_futures' or state.get('currency')!='CNY':
                raise ValueError('期货账本身份不一致，拒绝迁移或重置')
            if not finite(state.get('balance')) or len(state.get('positions',[]))>1:
                raise ValueError('期货账本资金或持仓无效')
            normalize_config(state['config'])
            yield state
            encoded=json.dumps(state,ensure_ascii=False,allow_nan=False)
            if not saved or saved[0]!=encoded:db.execute('INSERT OR REPLACE INTO futures_paper_state VALUES (1,?)',(encoded,))
            db.commit()
        finally:db.close()

    def set_enabled(self,enabled,now=None):
        if not isinstance(enabled,bool):raise ValueError('enabled必须为布尔值')
        now=milliseconds() if now is None else now
        with self.transaction() as state:
            if enabled and state['halted']:raise ValueError('保证金风险已停止开仓，请暂停并在空仓后调整参数')
            if state['enabled']!=enabled:
                state['enabled']=enabled;state['pending']=None
                if enabled:state['activated_at']=now
            state['note']='等待日盘新鲜报价与日线信号' if enabled else '新开仓暂停；已有仓位继续按日盘采样管理'

    def configure(self,params,now=None):
        cfg=normalize_config(params)
        with self.transaction() as state:
            if state['enabled'] or state['positions']:raise ValueError('请先暂停且保持空仓，再修改合约或成本参数')
            state.update(config=cfg,pending=None,error=None,last_quote_at=0,halted=False,
                         note='配置已保存；账户仍暂停，本金与历史保留')
        return cfg

    def recover(self,now=None):
        with self.transaction() as state:
            state['pending']=None;state['activated_at']=milliseconds() if now is None else now
            state['note']='服务已恢复，等待新的日盘源报价；不补造停机期间成交'

    @staticmethod
    def totals(state):
        p=state['positions'][0] if state['positions'] else None
        unrealized=(p['last_price']-p['entry_price'])*p['side']*p['lots']*p['multiplier'] if p else 0.
        margin=p['last_price']*p['lots']*p['multiplier']*p['margin_rate'] if p else 0.
        equity=state['balance']+unrealized
        return dict(unrealized_pnl=unrealized,margin=margin,equity=equity,available=equity-margin)

    def snapshot(self,now=None):
        now=milliseconds() if now is None else now
        with self.transaction() as state:out=deepcopy(state)
        out.update(self.totals(out),initial_balance=INITIAL_BALANCE)
        for p in out['positions']:p['unrealized_pnl']=(p['last_price']-p['entry_price'])*p['side']*p['lots']*p['multiplier']
        out['valuation_fresh']=not out['positions'] or 0<=now-out['positions'][0]['quote_at']<=30000
        return out

    @staticmethod
    def fill(raw,side,cfg):
        tick=cfg['tick_size']
        ticks=math.ceil(raw/tick-1e-9) if side>0 else math.floor(raw/tick+1e-9)
        price=(ticks+side*cfg['slippage_ticks'])*tick
        if price<=0:raise ValueError('滑点后价格非正，拒绝模拟成交')
        return price

    @staticmethod
    def fee(price,cfg,factor=1):
        return cfg['lots']*(cfg['fee_fixed']+price*cfg['multiplier']*cfg['fee_rate'])*factor

    def close(self,state,quote,reason):
        p=state['positions'][0];cfg=p['costs'];raw=quote['price'];ts=quote['quote_at']
        price=self.fill(raw,-p['side'],cfg)
        cost=self.fee(price,cfg,cfg['close_today_factor'] if day(ts)==p['entry_day'] else 1)
        gross=(price-p['entry_price'])*p['side']*p['lots']*p['multiplier']
        net=gross-p['entry_fee']-cost
        state['balance']+=gross-cost;state['fees']+=cost;state['realized_pnl']+=net
        state['total_trades']+=1;state['winning_trades']+=net>0
        trade=dict(symbol=p['symbol'],side=p['side'],lots=p['lots'],multiplier=p['multiplier'],
                   entry_at=p['entry_at'],exit_at=ts,signal_at=p['signal_at'],entry_price=p['entry_price'],
                   exit_price=price,entry_fee=p['entry_fee'],exit_fee=cost,fees=p['entry_fee']+cost,
                   raw_exit=raw,reason=reason,net_pnl=net,close_today=day(ts)==p['entry_day'])
        state['trades']=(state['trades']+[trade])[-100:];state['positions']=[];state['pending']=None
        state['note']='已按新报价模拟平仓：'+reason

    def step(self,packet,now=None):
        now=milliseconds() if now is None else now
        with self.transaction() as state:
            state['updated_at']=now;cfg=state['config']
            if not quote_valid(packet,cfg,now):
                state['pending']=None;state['error']='等待日盘30秒内、同月份合约的新鲜有效报价；未执行撮合'
                return
            q=packet['quote'];ts=q['quote_at'];price=q['price']
            if ts<=state['last_quote_at']:return
            state['last_quote_at']=ts;state['error']=None
            try:signal=daily_signal(packet,now)
            except (ValueError,KeyError,TypeError,OverflowError) as exc:
                signal=None;state['error']=str(exc)[:180];state['pending']=None
            if state['positions']:
                p=state['positions'][0];p.update(last_price=price,quote_at=ts)
                totals=self.totals(state)
                reason=None
                if totals['equity']<=0 or totals['available']<0:
                    reason='margin_exit';state['halted']=True;state['enabled']=False
                elif p['side']*(price-p['stop'])<=0:reason='atr_stop'
                elif signal and signal['side']!=p['side']:reason='signal_exit'
                if reason:self.close(state,q,reason)
                return
            if not state['enabled'] or state['halted'] or not signal:
                state['pending']=None;return
            delivery='20'+cfg['symbol'][-4:-2]+'-'+cfg['symbol'][-2:]+'-01'
            if day(now)>=delivery:
                state['pending']=None;state['error']='已进入交割月份或合约已过期，禁止新开仓；请空仓暂停后更换月份'
                return
            side=signal['side']
            key=f"{cfg['symbol']}:{cfg['strategy']}:{signal['signal_at']}:{side}"
            if not side or key==state['used_key']:
                state['pending']=None;state['note']='等待下一根完整日线的新方向；同一信号不重复开仓';return
            pending=state['pending']
            if not pending or pending['key']!=key:
                state['pending']=dict(signal,key=key,quote_at=ts,observed_at=now)
                state['note']='已观察到日线信号，等待观察时间之后的新报价'
                return
            if ts<=max(pending['quote_at'],pending['observed_at'],state['activated_at']):return
            fill=self.fill(price,side,cfg);cost=self.fee(fill,cfg)
            margin=price*cfg['multiplier']*cfg['lots']*cfg['margin_rate']
            equity_after_cost=state['balance']-cost+(price-fill)*side*cfg['lots']*cfg['multiplier']
            if equity_after_cost<=0 or margin>equity_after_cost*cfg['max_margin_fraction'] or margin>equity_after_cost:
                state['error']='保证金或开仓占用上限不足，未模拟开仓';return
            stop=fill-side*signal['distance']
            if stop<=0:state['error']='ATR保护价无效，未模拟开仓';return
            state['balance']-=cost;state['fees']+=cost;state['used_key']=key;state['pending']=None
            state['positions']=[dict(symbol=cfg['symbol'],side=side,lots=cfg['lots'],multiplier=cfg['multiplier'],
                margin_rate=cfg['margin_rate'],entry_price=fill,raw_entry=price,entry_at=ts,entry_day=day(ts),
                signal_at=signal['signal_at'],entry_fee=cost,stop=stop,last_price=price,quote_at=ts,costs=deepcopy(cfg))]
            state['note']='已按新报价模拟开仓；固定2ATR保护价'


def fetch_observation(symbol):
    try:return F.fetch_chart(symbol,'day')
    except (OSError,ValueError) as exc:
        # Historical data failure must not suppress protective exits on valid fresh quotes.
        quote=F.fetch_quotes([symbol])[0]
        return dict(F.identity(symbol),period='day',candles=[],quote=quote,history_error=str(exc)[:180])


class FuturesPaperRunner:
    def __init__(self,account,fetch=fetch_observation):
        self.account=account;self.fetch=fetch;self.error=None
        self.lock=threading.Lock();self.stop_event=threading.Event();self.thread=None

    def tick(self,now=None):
        state=self.account.snapshot(now=now)
        if state['enabled'] or state['positions']:
            self.account.step(self.fetch(state['config']['symbol']),now=now)
        self.error=None

    def start(self):
        with self.lock:
            if self.thread is None:
                try:self.account.recover()
                except (ValueError,OSError,sqlite3.Error) as exc:
                    self.error='期货模拟未启动：'+str(exc)[:160]
                    return
                self.thread=threading.Thread(target=self.run,daemon=True,name='cn-futures-paper')
                self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:self.tick()
            except Exception as exc:self.error=str(exc)[:180]
            self.stop_event.wait(10)

    def snapshot(self):
        return dict(self.account.snapshot(),runner_error=self.error,
                    specs={root:dict(multiplier=spec[0],tick_size=spec[1]) for root,spec in B.SPECS.items()})


ACCOUNT=FuturesPaperAccount(Path(__file__).parent/'cache'/'futures-paper.sqlite3')
RUNNER=FuturesPaperRunner(ACCOUNT)
