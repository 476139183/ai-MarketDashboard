"""日线趋势回踩、盘中收复确认；固定研究候选，默认未发布。"""
from datetime import datetime
from math import isfinite
from stock_setups import cent,valid_bars
from continuation import BJ,completed,vwaps

MODE='trend_reclaim'
VERSION='a-trend-reclaim-v1'
ENTRY_REVISION=VERSION
RISK_FRACTION=.0025
MAX_CANDIDATES=20
RELEASED=False


def market_allowed(benchmark):
    if len(benchmark)<25:return False
    c=[float(b['close']) for b in benchmark[-25:]]
    return all(isfinite(x) and x>0 for x in c) and c[-1]/c[-6]>.97 and c[-1]/c[-21]>.9


def build_plan(rows,benchmark,name):
    if 'ST' in name.upper() or '退' in name or not valid_bars(rows) or len(rows)<30 or not market_allowed(benchmark):return None
    if [r['date'] for r in rows[-25:]]!=[b['date'] for b in benchmark[-25:]]:return None
    c=[float(r['close']) for r in rows];b=[float(r['close']) for r in benchmark]
    ma5=sum(c[-5:])/5;ma20=sum(c[-20:])/20
    relative=c[-1]/c[-21]-b[-1]/b[-21]
    turnover=sum(float(r['close'])*float(r['volume']) for r in rows[-20:])/20
    if not ma20>sum(c[-25:-5])/20 or c[-1]<ma20*.99 or relative<.02 or turnover<1e8:return None
    atr=sum(max(float(y['high'])-float(y['low']),abs(float(y['high'])-float(x['close'])),abs(float(y['low'])-float(x['close']))) for x,y in zip(rows[-15:-1],rows[-14:]))/14
    if not 0<atr<c[-1]*.1 or c[-1]>ma5+atr or min(float(r['low']) for r in rows[-3:])>ma5+.25*atr:return None
    lower,upper=cent(ma5-.25*atr,True),cent(ma5+.5*atr)
    stop=cent(min(float(r['low']) for r in rows[-5:])-.2*atr)
    if not 0<stop<lower<=upper or (upper-stop)/upper>.06:return None
    return dict(strategy_version=VERSION,strategy_rule=MODE,entry_mode=MODE,strategy_label='日线趋势回踩收复',
        plan_ready=True,signal_date=rows[-1]['date'],signal_close=c[-1],atr14=atr,center=ma5,
        relative_strength=relative,avg20_turnover=turnover,pullback_distance=abs(c[-1]-ma5)/atr,
        budget_factor=.5,risk_fraction=RISK_FRACTION,exit_ma=20,
        summary='昨日确定MA5回踩区；等待盘中收复前线高点与估算均价',
        sniper=dict(secondary_buy=lower,ideal_buy=upper,stop_loss=stop,take_profit=cent(upper+3*atr)),checklist=[])


def assess(plan,minutes,now):
    def wait(reason):return dict(ready=False,reason=reason)
    if datetime.fromtimestamp(now/1000,BJ).strftime('%H:%M')>'14:30':return wait('14:30后不新开回踩收复仓')
    rows=completed(minutes,now)
    if len(rows)<2 or rows[-1]['time'][:10]<=plan['signal_date']:return wait('等待两根完整15分钟线')
    previous,last=rows[-2:];atr=plan['atr14'];ma5=plan['center']
    average=sum(float(r['volume']) for r in rows[max(0,len(rows)-4):-1])/min(3,len(rows)-1)
    if not (float(previous['low'])<=ma5+.5*atr and float(last['close'])>=float(previous['high'])+.01 and
            float(last['close'])>=ma5 and float(last['close'])>vwaps(rows)[-1] and
            float(last['close'])>float(last['open']) and float(last['volume'])>=average*.8):
        return wait('回踩后尚未同时收复前线高点、MA5和均价')
    lower=cent(max(plan['lower'] if 'lower' in plan else plan['sniper']['secondary_buy'],float(last['close'])-.25*atr),True)
    upper=cent(min(plan['upper'] if 'upper' in plan else plan['sniper']['ideal_buy'],float(last['close'])+.25*atr))
    stop=plan['stop'] if 'stop' in plan else plan['sniper']['stop_loss']
    if not stop<lower<=upper or upper-lower<.009999:return wait('收复后已离开昨日回踩区，不追价')
    return dict(ready=True,reason='日线趋势内回踩后收复确认',lower=lower,upper=upper,stop=stop,target=None,
        confirmed_at=int(datetime.fromisoformat(last['time']).replace(tzinfo=BJ).timestamp()*1000),
        signal_id=last['time'],confirmation=last['time'])


def manage(position,rows,now):
    result=dict(stop=position['stop'],exit_reason=None)
    today=datetime.fromtimestamp(now/1000,BJ).date().isoformat()
    rows=[r for r in rows if r['date']<today]
    if len(rows)<20:return result
    if rows[-1]['date']>=position['entry_day'] and float(rows[-1]['close'])<sum(float(r['close']) for r in rows[-20:])/20:
        result['exit_reason']='trend_reclaim_ma20_exit'
    for i in range(2,len(rows)-2):
        if rows[i]['date']<=position['entry_day']:continue
        low=float(rows[i]['low'])
        if not all(low<float(rows[j]['low']) for j in (i-2,i-1,i+1,i+2)):continue
        support=cent(low-.2*position['plan']['atr14'])
        if result['stop']<support<float(rows[-1]['close']):result['stop']=support
    return result
