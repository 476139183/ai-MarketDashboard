"""A-share candidate-plan paper trading. Independent cash account, T+1 and no shorting."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from decimal import Decimal,ROUND_HALF_UP,ROUND_CEILING,ROUND_FLOOR
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import a_share_feed as A
from market_states import market_session

BJ=ZoneInfo('Asia/Shanghai')
SOURCE='tencent_cn'
VERSION='cn-candidate-plan-paper-v1'
INITIAL_BALANCE=100000.
DEFAULTS=dict(per_stock_budget=20000.,max_positions=3,commission_rate=.0003,min_commission=5.,
              transfer_rate=.00001,stamp_rate=.0005,slippage_ticks=1,exit_ma=10)
RADAR_PATH=Path(__file__).resolve().parents[2]/'a'/'radar-latest.json'


def milliseconds():return int(time.time()*1000)
def day(stamp):return datetime.fromtimestamp(stamp/1000,BJ).date().isoformat()
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def money(x):return float(Decimal(str(x)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def session(stamp):return market_session(datetime.fromtimestamp(stamp/1000,BJ),'cn')['is_rth']


def stock_code(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:60[0135]|688|00[0-3]|30[01])\d{3}',value):
        raise ValueError('模拟仅支持沪深普通A股；北交所、ETF、CDR等暂不接入')
    return value


def minimum_shares(code):return 200 if stock_code(code).startswith('688') else 100


def normalize_config(params):
    if not isinstance(params,dict) or set(params)-set(DEFAULTS):raise ValueError('配置包含未知字段；不允许重置本金')
    cfg=dict(DEFAULTS,**params)
    for key,value in cfg.items():
        if not finite(value):raise ValueError(key+'必须为有限数')
    if cfg['exit_ma'] not in (10,20):raise ValueError('退出均线仅支持MA10或MA20')
    if not 100<=cfg['per_stock_budget']<=100000:raise ValueError('每票预算须在100至100000元之间')
    for key,low,high in [('max_positions',1,10),('slippage_ticks',0,100)]:
        if cfg[key]!=int(cfg[key]) or not low<=cfg[key]<=high:raise ValueError(key+'必须为范围内整数')
        cfg[key]=int(cfg[key])
    if not 0<=cfg['min_commission']<=1000 or any(not 0<=cfg[k]<=.01 for k in ['commission_rate','transfer_rate','stamp_rate']):raise ValueError('模拟费率超出范围')
    return cfg


def load_radar():
    data=json.loads(RADAR_PATH.read_text()) if RADAR_PATH.exists() else {}
    if not isinstance(data,dict):raise ValueError('候选名单结构无效')
    return data


def refresh_radar():
    path=str(RADAR_PATH.parent)
    if path not in sys.path:sys.path.append(path)
    import radar
    return radar.scan()


@lru_cache(maxsize=256)
def daily_rows(code,today):
    from feeds.cn import from_sina
    rows,_=from_sina(code,days=60)
    return [r for r in rows if r['date']<today]


@lru_cache(maxsize=32)
def market_calendar(today):
    url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urlencode({'param':'sh000300,day,,,80,qfq'})
    payload=json.loads(A._read(url))['data']['sh000300']
    rows=payload.get('day') or payload.get('qfqday') or []
    days=[r[0] for r in rows if r[0]<=today]
    if len(days)<2 or days[-1]!=today or days!=sorted(set(days)):raise ValueError('指数交易日序列未更新到今天，暂停新买入')
    return days


def fetch_observations(symbols):
    def one(code):
        try:
            rows=daily_rows(code,day(milliseconds()))
            quote=A.fetch_quote(code)
            quote=dict(quote,symbol=code,source=SOURCE,quote_at=quote['time'].timestamp()*1000,
                       upper_limit=quote['limits'][0],lower_limit=quote['limits'][1])
            quote.pop('time');quote.pop('limits')
            return code,dict(quote=quote,daily=rows)
        except Exception as exc:return code,dict(error=str(exc)[:180])
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='a-paper-feed') as pool:return dict(pool.map(one,symbols))


def valid_quote(q,code,now,source=SOURCE):
    ts=q.get('quote_at');price=q.get('price');lo=q.get('lower_limit');hi=q.get('upper_limit')
    return (q.get('source')==source and q.get('symbol')==code and finite(ts) and 0<=now-ts<=30000
            and day(ts)==day(now) and session(now) and session(ts)
            and finite(price) and finite(lo) and finite(hi) and 0<lo<=price<=hi and hi>lo
            and finite(q.get('volume')) and q['volume']>0)


def historical_check(pack,q,now):
    rows=pack.get('daily') or []
    previous=[r for r in rows if r['date']<day(now)]
    if not previous or [r['date'] for r in previous]!=sorted({r['date'] for r in previous}):raise ValueError('未复权日线缺失或乱序，暂停撮合')
    if any(not finite(r.get('close')) or r['close']<=0 for r in previous):raise ValueError('未复权日线价格无效')
    if not finite(q.get('previous_close')) or abs(previous[-1]['close']-q['previous_close'])>.011:
        raise ValueError('前收与未复权日线不符，需核对除权除息或数据口径；保留旧估值')
    return previous


def approved_plans(radar,previous_day,now):
    generated=datetime.fromisoformat(radar['generated_at'])
    if generated.tzinfo is None:raise ValueError('名单缺少时区')
    local=generated.astimezone(BJ)
    if radar.get('signal_date')!=previous_day or local.date().isoformat()!=previous_day or local.hour*60+local.minute<905 or generated.timestamp()*1000>=now:
        raise ValueError('名单必须在上一交易日盘后生成，旧名单/事后补记不买入')
    if not radar.get('market_pass'):return []
    result=[];seen=set()
    for row in radar.get('strategy_candidates',[]):
        try:
            code=stock_code(row['code']);plan=row['trade_plan'];s=plan['sniper']
            if code in seen or row.get('category')!='策略候选' or row.get('market_pass') is not True or row.get('plan_ready') is not True or plan.get('plan_ready') is not True:continue
            values=[s.get(k) for k in ['stop_loss','secondary_buy','ideal_buy','take_profit']]
            if not all(finite(v) and v>0 for v in values) or not values[0]<values[1]<=values[2]<values[3]:continue
            seen.add(code);result.append(dict(code=code,name=row.get('name',code),signal_date=previous_day,
                generated_at=radar['generated_at'],lower=s['secondary_buy'],upper=s['ideal_buy'],stop=s['stop_loss'],
                target=s['take_profit'],signal_close=row['close'],key=previous_day+':'+code))
            result[-1].update(strategy_version=plan.get('strategy_version',row.get('strategy_version','legacy-ma20-v1')),
                              strategy_rule=plan.get('strategy_rule','legacy'))
        except (ValueError,TypeError,KeyError):continue
    return result


class ASharePaperAccount:
    def __init__(self,path,source=SOURCE):self.path=Path(path);self.source=source
    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path,timeout=5)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS a_paper_state (id INTEGER PRIMARY KEY,body TEXT NOT NULL)');db.execute('BEGIN IMMEDIATE')
            saved=db.execute('SELECT body FROM a_paper_state WHERE id=1').fetchone()
            s=json.loads(saved[0]) if saved else dict(version=VERSION,source=self.source,market='a_share',currency='CNY',enabled=False,
                cash=INITIAL_BALANCE,config=dict(DEFAULTS),positions=[],trades=[],pending={},used_keys=[],
                plans=[],candidate_snapshot={},plan_date=None,plan_error=None,scan_attempt_day=None,scan_error=None,activated_at=0,
                updated_at=None,error=None,note='尚未启用A股自动模拟',fees=0.,realized_pnl=0.,total_trades=0,winning_trades=0)
            if not isinstance(s,dict) or s.get('version')!=VERSION or s.get('market')!='a_share' or s.get('currency')!='CNY' or s.get('source')!=self.source:raise ValueError('A股账本身份不一致，拒绝迁移或重置')
            if not finite(s.get('cash')):raise ValueError('A股账本资金无效')
            yield s
            text=json.dumps(s,ensure_ascii=False,allow_nan=False)
            if not saved or saved[0]!=text:db.execute('INSERT OR REPLACE INTO a_paper_state VALUES (1,?)',(text,))
            db.commit()
        finally:db.close()
    def set_enabled(self,enabled,now=None):
        if not isinstance(enabled,bool):raise ValueError('enabled必须为布尔值')
        with self.transaction() as s:
            if s['enabled']!=enabled:
                s['pending']={};s['enabled']=enabled
                if enabled:s['activated_at']=milliseconds() if now is None else now
            s['note']='等待上一交易日策略候选与盘中计划到价' if enabled else '新买入已暂停；已有持仓仍按T+1与策略管理'
    def configure(self,params):
        cfg=normalize_config(params)
        with self.transaction() as s:
            if s['enabled'] or s['positions']:raise ValueError('请先暂停且空仓，再修改参数')
            s.update(config=cfg,pending={},error=None,note='配置已保存；本金和历史保留，账户仍暂停')
    def recover(self,now=None):
        with self.transaction() as s:
            s['pending']={};s['activated_at']=milliseconds() if now is None else now
            s['note']='服务已恢复，等待新的报价；不补造离线期间成交'
    def snapshot(self,now=None,include_all_trades=False):
        now=milliseconds() if now is None else now
        with self.transaction() as s:out=deepcopy(s)
        if not include_all_trades:out['trades']=out['trades'][-100:]
        market_value=0.;unrealized=0.
        for p in out['positions']:
            value=p['last_price']*p['shares'];market_value+=value
            p['unrealized_pnl']=money(value-p['entry_price']*p['shares']-p['entry_fee']);unrealized+=p['unrealized_pnl']
            p['sellable_shares']=p['shares'] if day(p['quote_at'])>p['entry_day'] else 0
        out.update(initial_balance=INITIAL_BALANCE,available=out['cash'],market_value=money(market_value),equity=money(out['cash']+market_value),
                   unrealized_pnl=money(unrealized),valuation_fresh=all(0<=now-p['quote_at']<=30000 and not p.get('valuation_blocked') for p in out['positions']))
        return out
    @staticmethod
    def fill(price,buy,cfg):
        rounding=ROUND_CEILING if buy else ROUND_FLOOR
        ticks=int((Decimal(str(price))/Decimal('.01')).to_integral_value(rounding=rounding))
        return money((ticks+(cfg['slippage_ticks'] if buy else -cfg['slippage_ticks']))*.01)
    @staticmethod
    def costs(notional,buy,cfg):
        parts=dict(commission=money(max(cfg['min_commission'],notional*cfg['commission_rate'])),
                   transfer=money(notional*cfg['transfer_rate']),stamp=0. if buy else money(notional*cfg['stamp_rate']))
        return dict(parts,total=money(sum(parts.values())))
    @staticmethod
    def exit_reason(position,quote,rows):
        if quote['price']<=position['stop']:return 'stop'
        if quote['price']>=position['target']:return 'target'
        period=position['costs']['exit_ma']
        if len(rows)>=period and rows[-1]['date']>=position['entry_day'] and rows[-1]['close']<sum(r['close'] for r in rows[-period:])/period:
            return 'ma10_exit'
        return None

    def step(self,radar,calendar,observations,now=None):
        now=milliseconds() if now is None else now
        with self.transaction() as s:
            radar=radar if isinstance(radar,dict) else {}
            s['updated_at']=now;s['error']=None;cfg=s['config'];issues=[]
            prev=None
            if calendar and calendar==sorted(set(calendar)) and calendar[-1]==day(now) and len(calendar)>=2:prev=calendar[-2]
            try:
                plans=approved_plans(radar,prev,now) if prev else []
                if prev:s['candidate_snapshot']={k:deepcopy(radar.get(k)) for k in ['signal_date','generated_at','market_pass','market_label','strategy_candidates']}
                s['plan_error']=None if prev else '交易日序列未更新到今天，暂停新买入'
            except (ValueError,KeyError,TypeError) as exc:plans=[];s['plan_error']=str(exc)
            s['plans']=plans;s['plan_date']=radar.get('signal_date')
            checked={}
            for code,pack in observations.items():
                q=pack.get('quote') or {}
                if not valid_quote(q,code,now,self.source):issues.append(code+'：等待连续竞价30秒内有效报价');s['pending'].pop(code,None);continue
                try:rows=historical_check(pack,q,now)
                except (ValueError,TypeError,KeyError) as exc:
                    issues.append(code+'：'+str(exc));s['pending'].pop(code,None)
                    for p in s['positions']:
                        if p['code']==code:p['valuation_blocked']=True
                    continue
                checked[code]=(q,rows)
            for p in list(s['positions']):
                if p['code'] not in checked:continue
                q,rows=checked[p['code']];ts=q['quote_at']
                if ts<=p['quote_at']:continue
                p.update(last_price=q['price'],quote_at=ts,valuation_blocked=False)
                if prev is None:
                    issues.append(p['code']+'：交易日序列不可核验，保留持仓并等待')
                    continue
                reason=self.exit_reason(p,q,rows)
                if not reason:continue
                if day(ts)<=p['entry_day']:s['note']='T+1锁定：当前触发退出条件，当日新买不能卖出；后续重新核验策略';continue
                bid=q.get('bid')
                if not finite(bid) or bid<=q['lower_limit']:issues.append(p['code']+'：跌停或买一缺失，无法模拟卖出');continue
                price=self.fill(bid,False,p['costs'])
                if not q['lower_limit']<=price<=q['upper_limit']:issues.append(p['code']+'：滑点后卖价超涨跌停，等待');continue
                notional=money(price*p['shares']);fees=self.costs(notional,False,p['costs']);net=money(notional-fees['total']-p['entry_price']*p['shares']-p['entry_fee'])
                trade=dict(code=p['code'],name=p['name'],shares=p['shares'],entry_at=p['entry_at'],exit_at=ts,
                    entry_price=p['entry_price'],exit_price=price,signal_date=p['signal_date'],stop=p['stop'],target=p['target'],
                    entry_costs=p['entry_costs'],exit_costs=fees,fees=money(p['entry_fee']+fees['total']),net_pnl=net,
                    return_pct=net/(p['entry_price']*p['shares']+p['entry_fee'])*100,reason=reason,plan=p['plan'],config=p['costs'])
                s['cash']=money(s['cash']+notional-fees['total']);s['fees']=money(s['fees']+fees['total']);s['realized_pnl']=money(s['realized_pnl']+net)
                s['total_trades']+=1;s['winning_trades']+=net>0;s['trades'].append(trade);s['positions'].remove(p)
                s['note']='已按T+1与策略条件模拟卖出：'+p['code']
            if not s['enabled']:s['pending']={};s['error']='；'.join(issues) or None;return
            active={p['code'] for p in plans};s['pending']={k:v for k,v in s['pending'].items() if k in active}
            for plan in plans:
                code=plan['code']
                if len(s['positions'])>=cfg['max_positions']:break
                if code not in checked or any(p['code']==code for p in s['positions']) or plan['key'] in s['used_keys']:continue
                q,rows=checked[code];ask=q.get('ask');name=q.get('name','')
                if 'ST' in name.upper() or '退' in name or rows[-1]['date']!=prev or abs(rows[-1]['close']-plan['signal_close'])>.011:continue
                if not finite(ask) or not plan['lower']<=ask<=plan['upper'] or ask>=q['upper_limit']:
                    s['pending'].pop(code,None);continue
                price=self.fill(ask,True,cfg)
                if not plan['lower']<=price<=plan['upper'] or not q['lower_limit']<price<q['upper_limit']:s['pending'].pop(code,None);continue
                pending=s['pending'].get(code);ts=q['quote_at']
                if not pending or pending['key']!=plan['key']:
                    s['pending'][code]=dict(key=plan['key'],quote_at=ts,observed_at=now);continue
                if ts<=max(pending['quote_at'],pending['observed_at'],s['activated_at']):continue
                budget=min(cfg['per_stock_budget'],s['cash']);shares=min(100000,int(budget/price/100)*100)
                while shares>=minimum_shares(code):
                    fees=self.costs(money(price*shares),True,cfg)
                    if price*shares+fees['total']<=budget:break
                    shares-=100
                if shares<minimum_shares(code):issues.append(code+'：资金不足以覆盖最小买入股数及费用');continue
                s['cash']=money(s['cash']-price*shares-fees['total']);s['fees']=money(s['fees']+fees['total'])
                s['positions'].append(dict(code=code,name=name,shares=shares,entry_price=price,entry_at=ts,entry_day=day(ts),
                    entry_fee=fees['total'],entry_costs=fees,costs=deepcopy(cfg),signal_date=plan['signal_date'],plan=deepcopy(plan),
                    stop=plan['stop'],target=plan['target'],last_price=q['price'],quote_at=ts,valuation_blocked=False))
                s['used_keys'].append(plan['key']);s['pending'].pop(code,None);s['note']='计划到价，已按后续新报价模拟买入：'+code
            s['error']='；'.join(issues) or None
            if not plans and not s['positions']:s['note']='没有可执行策略候选，等待合格盘后名单；不使用观察池买入'


class ASharePaperRunner:
    def __init__(self,account,load_radar=load_radar,refresh=refresh_radar,fetch=fetch_observations,calendar=market_calendar):
        self.account=account;self.load_radar=load_radar;self.refresh=refresh;self.fetch=fetch;self.calendar=calendar
        self.error=None;self.lock=threading.Lock();self.thread=None;self.stop_event=threading.Event()
    def tick(self,now=None):
        explicit_now=now is not None
        now=milliseconds() if now is None else now;s=self.account.snapshot(now=now);self.error=None
        if not s['enabled'] and not s['positions']:self.error=None;return
        local=datetime.fromtimestamp(now/1000,BJ)
        try:radar=self.load_radar()
        except (ValueError,OSError):radar={}
        if not session(now):
            if s['enabled'] and local.weekday()<5 and local.hour*60+local.minute>=910 and s['scan_attempt_day']!=day(now):
                with self.account.transaction() as state:state['scan_attempt_day']=day(now)
                try:
                    if radar.get('signal_date')!=day(now):radar=self.refresh()
                    effective=now if explicit_now else milliseconds()
                    approved_plans(radar,day(effective),effective+1)
                    with self.account.transaction() as state:
                        snapshot={k:deepcopy(radar.get(k)) for k in ['signal_date','generated_at','market_pass','market_label','strategy_candidates']}
                        state.update(candidate_snapshot=snapshot,plan_date=radar.get('signal_date'),scan_error=None,note='盘后候选已归档，等待下一交易日计划到价')
                except Exception as exc:
                    with self.account.transaction() as state:state['scan_error']=str(exc)[:180]
            self.error=None;return
        try:calendar=self.calendar(day(now))
        except Exception as exc:calendar=[];self.error=str(exc)[:180]
        if calendar:
            try:approved_plans(radar,calendar[-2],now)
            except (ValueError,TypeError,KeyError):radar=s.get('candidate_snapshot') or radar
        codes={p['code'] for p in s['positions']}
        if s['enabled'] and calendar:
            try:codes.update(p['code'] for p in approved_plans(radar,calendar[-2],now))
            except (ValueError,TypeError,KeyError):pass
        data=self.fetch(sorted(codes)) if codes else {}
        self.account.step(radar,calendar,data,now=now if explicit_now else milliseconds())
    def start(self):
        with self.lock:
            if self.thread is None:
                try:self.account.recover()
                except (ValueError,OSError,sqlite3.Error) as exc:self.error=str(exc)[:180];return
                self.thread=threading.Thread(target=self.run,daemon=True,name='a-share-paper');self.thread.start()
    def run(self):
        while not self.stop_event.is_set():
            try:self.tick()
            except Exception as exc:self.error=str(exc)[:180]
            self.stop_event.wait(15)
    def snapshot(self,include_all_trades=False):
        s=self.account.snapshot(include_all_trades=include_all_trades)
        try:r=self.load_radar();s['source_plan_date']=r.get('signal_date');s['source_candidate_count']=len(r.get('strategy_candidates',[]));s['market_label']=r.get('market_label')
        except (ValueError,OSError,TypeError):s['source_plan_date']=None;s['source_candidate_count']=0
        return dict(s,runner_error=self.error)


ACCOUNT=ASharePaperAccount(Path(__file__).parent/'cache'/'a-share-paper.sqlite3')
RUNNER=ASharePaperRunner(ACCOUNT)
