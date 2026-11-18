"""A股短期超跌反转：昨日冻结计划，盘中确认，均值/失效退出。"""
from datetime import datetime
from math import isfinite
from stock_setups import cent,valid_bars
from continuation import BJ,completed,vwaps

MODE='oversold_reversal'
VERSION='a-oversold-reversal-v3'
ENTRY_REVISION=VERSION
VERSIONS={'a-oversold-reversal-v1':None,'a-oversold-reversal-v2':1.,VERSION:0.}
BUDGET_FACTOR=1.
RISK_FRACTION=.005
MAX_CANDIDATES=20
RELEASED=False
PAPER_TRIAL=False  # 只有用户明确选择未充分验证的前向模拟试运行后才打开。
TRIAL_VERSION=VERSION


def active_version():return TRIAL_VERSION if PAPER_TRIAL and not RELEASED else VERSION


def net_rr(version):return VERSIONS[version]


def rsi2(closes):
    if len(closes)<3 or any(not isfinite(float(c)) or float(c)<=0 for c in closes):return None
    changes=[float(b)-float(a) for a,b in zip(closes,closes[1:])]
    gain=sum(max(x,0) for x in changes[:2])/2;loss=sum(max(-x,0) for x in changes[:2])/2
    for change in changes[2:]:gain=(gain+max(change,0))/2;loss=(loss+max(-change,0))/2
    if gain+loss==0:return 50.
    return 100*gain/(gain+loss)


def market_allowed(benchmark):
    if len(benchmark)<25:return False
    c=[float(b['close']) for b in benchmark[-25:]]
    return all(isfinite(x) and x>0 for x in c) and c[-1]/c[-2]>.975 and c[-1]/c[-6]>.94


def build_plan(rows,benchmark,name,*,version=VERSION):
    return screen(rows,benchmark,name,version=version)['plan']


def screen(rows,benchmark,name,*,version=VERSION):
    metrics={}
    def reject(stage,reason):return dict(stage=stage,reason=reason,metrics=metrics,plan=None)
    if version not in VERSIONS:raise ValueError('未知超跌反转版本')
    if 'ST' in name.upper() or '退' in name:return reject('security','ST或退市风险证券')
    if len(rows)<30 or not valid_bars(rows):return reject('data','不足30根有效日线')
    if [r['date'] for r in rows[-25:]]!=[b['date'] for b in benchmark[-25:]]:return reject('data','最近25个市场交易日日线不同步')
    if not market_allowed(benchmark):return reject('market','指数急跌防守条件未通过')
    c=[float(r['close']) for r in rows];oscillator=rsi2(c)
    ma5=sum(c[-5:])/5
    atr=sum(max(float(y['high'])-float(y['low']),abs(float(y['high'])-float(x['close'])),abs(float(y['low'])-float(x['close']))) for x,y in zip(rows[-15:-1],rows[-14:]))/14
    last=rows[-1];span=float(last['high'])-float(last['low'])
    turnover=sum(float(r['close'])*float(r['volume']) for r in rows[-20:])/20
    metrics.update(rsi2=oscillator,atr14=atr,ma5=ma5,close=c[-1],avg20_turnover=turnover)
    if turnover<1e8:return reject('liquidity','20日平均成交额不足1亿元')
    if atr<=0 or span<=0:return reject('range','当日无有效价格区间')
    if oscillator is None or oscillator>20 or c[-1]>ma5-.5*atr:return reject('oversold','尚未达到短期超跌条件')
    location=(c[-1]-float(last['low']))/span;metrics['close_location']=location
    if location<.6:return reject('recovery','收盘收复不足，仍偏向下跌而非反转')
    stop=cent(float(last['low'])-.25*atr)
    lower=cent(max(c[-1]-.5*atr,stop+.1*atr),True)
    upper=cent(min(c[-1]+.5*atr,ma5-.25*atr))
    target=cent(ma5)
    if not 0<stop<lower<=upper<target or upper-lower<.009999:return reject('plan','冻结止损与均值目标之间没有有效买入区')
    plan=dict(strategy_version=version,strategy_rule=MODE,entry_mode=MODE,strategy_label='短期超跌反转',
        plan_ready=True,signal_date=last['date'],signal_close=c[-1],atr14=atr,reference_low=float(last['low']),
        rsi2=oscillator,close_location=location,avg20_turnover=turnover,budget_factor=BUDGET_FACTOR,
        risk_fraction=RISK_FRACTION,min_net_rr=net_rr(version),summary='昨日短期超跌；等待盘中反转确认，目标为冻结MA5'+('；净目标空间须覆盖止损风险' if net_rr(version) else '；双倍成本后的净目标空间须为正' if net_rr(version)==0 else ''),
        sniper=dict(secondary_buy=lower,ideal_buy=upper,stop_loss=stop,take_profit=target),checklist=[])
    return dict(stage='candidate',reason='盘后计划合格，仍需盘中反转确认和成本检查',metrics=metrics,plan=plan)


def assess(plan,minutes,now):
    def wait(reason):return dict(ready=False,reason=reason)
    if datetime.fromtimestamp(now/1000,BJ).strftime('%H:%M')>'14:30':return wait('14:30后不新开超跌反转仓')
    rows=completed(minutes,now)
    if len(rows)<2 or rows[-1]['time'][:10]<=plan['signal_date']:return wait('等待两根完整15分钟线确认反转')
    previous,last=rows[-2:]
    if not (float(last['close'])>=float(previous['high'])+.01 and float(last['close'])>float(last['open']) and float(last['close'])>vwaps(rows)[-1]):
        return wait('尚未收复前线高点和估算均价')
    bounds=plan.get('sniper',{})
    return dict(ready=True,reason='短期超跌后盘中反转确认',
        lower=plan['lower'] if 'lower' in plan else bounds['secondary_buy'],
        upper=plan['upper'] if 'upper' in plan else bounds['ideal_buy'],
        stop=plan['stop'] if 'stop' in plan else bounds['stop_loss'],
        target=plan['target'] if 'target' in plan else bounds['take_profit'],
        confirmation=last['time'],signal_id=last['time'],
        confirmed_at=int(datetime.fromisoformat(last['time']).replace(tzinfo=BJ).timestamp()*1000))


def manage(position,rows,now):
    today=datetime.fromtimestamp(now/1000,BJ).date().isoformat()
    rows=[r for r in rows if r['date']<today];reason=None
    if rows and rows[-1]['date']>=position['entry_day']:
        if float(rows[-1]['close'])<position['plan']['reference_low']:reason='oversold_reversal_failed'
        elif (rsi2([float(r['close']) for r in rows]) or 0)>=70:reason='oversold_reversal_recovered'
    return dict(stop=position['stop'],exit_reason=reason)
