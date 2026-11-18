"""A股短期超跌反转：昨日冻结计划，盘中确认，均值/失效退出。"""
from datetime import datetime
from math import isfinite
from stock_setups import cent,valid_bars
from continuation import BJ,completed,vwaps

MODE='oversold_reversal'
VERSION='a-oversold-reversal-v1'
ENTRY_REVISION=VERSION
BUDGET_FACTOR=1.
RISK_FRACTION=.005
MAX_CANDIDATES=20
RELEASED=False


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


def build_plan(rows,benchmark,name):
    if 'ST' in name.upper() or '退' in name or len(rows)<30 or not valid_bars(rows) or not market_allowed(benchmark):return None
    if [r['date'] for r in rows[-25:]]!=[b['date'] for b in benchmark[-25:]]:return None
    c=[float(r['close']) for r in rows];oscillator=rsi2(c)
    ma5=sum(c[-5:])/5
    atr=sum(max(float(y['high'])-float(y['low']),abs(float(y['high'])-float(x['close'])),abs(float(y['low'])-float(x['close']))) for x,y in zip(rows[-15:-1],rows[-14:]))/14
    last=rows[-1];span=float(last['high'])-float(last['low'])
    turnover=sum(float(r['close'])*float(r['volume']) for r in rows[-20:])/20
    if oscillator is None or oscillator>20 or atr<=0 or turnover<1e8 or c[-1]>ma5-.5*atr or span<=0:return None
    location=(c[-1]-float(last['low']))/span
    if location<.6:return None
    stop=cent(float(last['low'])-.25*atr)
    lower=cent(max(c[-1]-.5*atr,stop+.1*atr),True)
    upper=cent(min(c[-1]+.5*atr,ma5-.25*atr))
    target=cent(ma5)
    if not 0<stop<lower<=upper<target or upper-lower<.009999:return None
    return dict(strategy_version=VERSION,strategy_rule=MODE,entry_mode=MODE,strategy_label='短期超跌反转',
        plan_ready=True,signal_date=last['date'],signal_close=c[-1],atr14=atr,reference_low=float(last['low']),
        rsi2=oscillator,close_location=location,avg20_turnover=turnover,budget_factor=BUDGET_FACTOR,
        risk_fraction=RISK_FRACTION,summary='昨日短期超跌并有收复迹象；等待盘中反转确认，目标为冻结MA5',
        sniper=dict(secondary_buy=lower,ideal_buy=upper,stop_loss=stop,take_profit=target),checklist=[])


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
