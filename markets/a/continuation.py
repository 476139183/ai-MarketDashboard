"""A股强势延续研究规则；只消费调用方提供的已完成行情。"""
from datetime import datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo
from stock_setups import cent, valid_bars

VERSION='a-continuation-v1'
ENTRY_REVISION='a-dual-entry-v3'
BJ=ZoneInfo('Asia/Shanghai')
RISK_FRACTION=.0025
MAX_CANDIDATES=20
RELEASED=False  # 固定规则回放与后续检验未通过前，盘后雷达不发布新通道计划。


def market_allowed(benchmark):
    if len(benchmark)<25:return False
    c=[float(b['close']) for b in benchmark]
    return all(isfinite(x) and x>0 for x in c) and c[-1]>=sum(c[-20:])/20*.98 and c[-1]/c[-6]>.97


def build_plan(rows,benchmark,name):
    if 'ST' in name.upper() or '退' in name or not valid_bars(rows) or len(rows)<30 or not market_allowed(benchmark):return None
    required=[b['date'] for b in benchmark[-25:]]
    if [r['date'] for r in rows[-25:]]!=required:return None
    c=[float(r['close']) for r in rows];b=[float(r['close']) for r in benchmark]
    ma20=sum(c[-20:])/20;previous_ma20=sum(c[-25:-5])/20
    relative=c[-1]/c[-21]-b[-1]/b[-21]
    turnover=sum(float(r['close'])*float(r['volume']) for r in rows[-20:])/20
    if not c[-1]>ma20>previous_ma20 or relative<.03 or turnover<100000000:return None
    atr=sum(max(float(y['high'])-float(y['low']),abs(float(y['high'])-float(x['close'])),abs(float(y['low'])-float(x['close']))) for x,y in zip(rows[-15:-1],rows[-14:]))/14
    if not 0<atr<c[-1]*.1:return None
    return dict(strategy_version=VERSION,strategy_rule='strong_continuation',strategy_label='强势延续确认',
                entry_mode='continuation',plan_ready=True,signal_date=rows[-1]['date'],atr14=atr,
                signal_close=c[-1],relative_strength=relative,avg20_turnover=turnover,
                budget_factor=.5,risk_fraction=RISK_FRACTION,
                summary='昨日相对强势；等待盘中放量突破后回踩或再次确认',
                sniper=dict(secondary_buy=cent(c[-1]-atr),ideal_buy=cent(c[-1]+atr),
                            stop_loss=cent(c[-1]-2*atr),take_profit=cent(c[-1]+3*atr)),
                checklist=[],sniper_pending=True)


def completed(minutes,now):
    at=datetime.fromtimestamp(now/1000,BJ);date=at.date().isoformat()
    rows=[];times=[]
    try:
        for row in minutes or []:
            t=datetime.fromisoformat(row['time']).replace(tzinfo=BJ)
            if t.date().isoformat()==date and t<at:
                if t.strftime('%H:%M') not in CLOCKS:continue
                values=[float(row[k]) for k in ('open','high','low','close','volume')]
                o,h,l,c,v=values
                if not all(isfinite(x) and x>0 for x in values) or not l<=min(o,c)<=max(o,c)<=h:return []
                rows.append(row);times.append(t)
        if not times or any(a>=b for a,b in zip(times,times[1:])) or at-times[-1]>timedelta(minutes=17):return []
        expected=[s for s in CLOCKS if s<=times[-1].strftime('%H:%M')]
        if [t.strftime('%H:%M') for t in times]!=expected:return []
        return rows
    except (ValueError,TypeError,KeyError):return []


CLOCKS=[f'{h:02}:{m:02}' for h in range(9,16) for m in (0,15,30,45)
        if '09:45'<=f'{h:02}:{m:02}'<='11:30' or '13:15'<=f'{h:02}:{m:02}'<='15:00']


def vwaps(rows):
    total=weighted=0.;out=[]
    for r in rows:
        volume=float(r['volume']);total+=volume
        weighted+=(float(r['high'])+float(r['low'])+float(r['close']))/3*volume
        out.append(weighted/total)
    return out


def assess(plan,minutes,now):
    def wait(reason):return dict(ready=False,reason=reason)
    if datetime.fromtimestamp(now/1000,BJ).strftime('%H:%M')>'14:30':return wait('14:30后不新开延续仓')
    rows=completed(minutes,now)
    if len(rows)<3:return wait('等待完整开盘区间及突破后确认')
    atr=plan['atr14'];level=float(rows[0]['high']);vwap=vwaps(rows);last=rows[-1]
    if rows[-1]['time'][:10]<=plan['signal_date']:return wait('候选必须来自前一交易日')
    if float(last['close'])<level or float(last['close'])<vwap[-1]:return wait('突破位或均价未守住')
    for j in range(max(1,len(rows)-4),len(rows)-1):
        bar=rows[j];prior=rows[max(0,j-3):j]
        if datetime.fromisoformat(last['time'])-datetime.fromisoformat(bar['time'])>timedelta(minutes=45):continue
        average=sum(float(r['volume']) for r in prior)/len(prior)
        span=float(bar['high'])-float(bar['low'])
        if not (float(bar['close'])>=level+.01 and float(bar['close'])>vwap[j] and
                float(bar['volume'])>=average*1.5 and span>0 and
                (float(bar['close'])-float(bar['low']))/span>=.7):continue
        if any(float(r['close'])<level-.25*atr for r in rows[j+1:]):continue
        retest=float(last['low'])<=level+.25*atr and float(last['volume'])<=float(bar['volume'])
        follow=float(last['close'])>max(float(r['high']) for r in rows[j:-1]) and float(last['volume'])>=average*1.5
        if not (retest or follow):continue
        lower=cent(max(level,float(last['close'])-.25*atr),True)
        upper=cent(min(float(last['close'])+.1*atr,level+atr,plan['signal_close']*1.08))
        stop=cent(min(float(last['low']),level)-.1*atr)
        if not 0<stop<lower<=upper or upper-lower<.009999 or not .005<=(upper-stop)/upper<=.03:continue
        confirmed=int(datetime.fromisoformat(last['time']).replace(tzinfo=BJ).timestamp()*1000)
        return dict(ready=True,reason='放量突破后回踩守住' if retest else '放量突破后再次确认',
                    lower=lower,upper=upper,stop=stop,target=None,breakout_level=level,
                    confirmed_at=confirmed,signal_id=bar['time'],confirmation=last['time'])
    return wait('等待放量突破后的有效确认，或追价/止损距离超限')


def manage(position,minutes,now):
    rows=completed(minutes,now);result=dict(stop=position['stop'],exit_reason=None)
    if len(rows)<3:return result
    atr=position['plan']['atr14'];level=position['plan']['breakout_level'];vwap=vwaps(rows)
    if all(float(rows[i]['close'])<min(level,vwap[i]) for i in (-2,-1)):
        result['exit_reason']='continuation_structure_exit'
    if float(rows[-1]['close'])>position['entry_price']:
        for i in range(2,len(rows)-2):
            pivot=rows[i];stamp=int(datetime.fromisoformat(pivot['time']).replace(tzinfo=BJ).timestamp()*1000)
            if stamp<=position.get('entry_at',now):continue
            low=float(pivot['low'])
            if not all(low<float(rows[j]['low']) for j in (i-2,i-1,i+1,i+2)):continue
            support=cent(low-.1*atr)
            if result['stop']<support<float(rows[-1]['close']):result['stop']=support
    return result
