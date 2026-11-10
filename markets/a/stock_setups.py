"""A股分型信号与回踩计划；只使用调用方传入的已完成日线。"""
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
from math import isfinite
from daily_strategy import entry_signal
from trade_plan import build_trade_plan

VERSION='a-setups-v2'
LABELS={'ma20_reclaim':'重返MA20确认','trend_pullback':'持续趋势回踩','volume_breakout':'放量突破确认'}
PRIORITY=('trend_pullback','ma20_reclaim','volume_breakout')


def ma(values,n):return sum(values[-n:])/n


def cent(value,up=False):
    return float(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_CEILING if up else ROUND_FLOOR))


def valid_bars(rows):
    if len(rows)<25:return False
    try:
        if any(a['date']>=b['date'] for a,b in zip(rows,rows[1:])):return False
        for r in rows:
            o,h,l,c,v=(float(r[k]) for k in ('open','high','low','close','volume'))
            if not all(isfinite(x) for x in (o,h,l,c,v)) or v<=0 or l<=0 or not l<=min(o,c)<=max(o,c)<=h:return False
    except (KeyError,TypeError,ValueError):return False
    return True


def build_setup_plan(rows,rule):
    if rule not in LABELS:raise ValueError('未知A股分型')
    if not valid_bars(rows):return None
    base=build_trade_plan(rows);closes=[float(r['close']) for r in rows]
    pivot=ma(closes,20) if rule=='ma20_reclaim' else ma(closes,10) if rule=='trend_pullback' else max(float(r['high']) for r in rows[-21:-1])
    atr=sum(max(float(b['high'])-float(b['low']),abs(float(b['high'])-float(a['close'])),abs(float(b['low'])-float(a['close']))) for a,b in zip(rows[-15:-1],rows[-14:]))/14
    lower,upper=cent(pivot*.995,True),cent(pivot*1.005)
    stop=cent(pivot-1.5*atr);target=pivot+3*atr
    resistance=max(float(r['high']) for r in rows[-61:-1])
    if resistance>upper:target=min(target,resistance)
    target=cent(target);risk=upper-stop
    rr=(target-upper)/risk if risk>0 else 0
    issues=[]
    if atr<=0 or not 0<stop<lower<=upper<target:issues.append('止损、买入区间与目标结构无效')
    if upper-lower<.009999:issues.append('整分区间不足一档滑点空间')
    if closes[-1]>pivot*1.05:issues.append('距离回踩中心超过5%，不追高')
    if rr<1.5:issues.append('区间上沿至目标的毛盈亏比不足1.5')
    base.update(strategy_version=VERSION,strategy_rule=rule,strategy_label=LABELS[rule],plan_ready=not issues,
                pivot=round(pivot,4),atr14=round(atr,4),risk_reward=round(rr,4),rejection_reasons=issues,
                chase_risk=closes[-1]>pivot*1.05,
                summary='；'.join(issues) if issues else LABELS[rule]+'：次日仅在回踩区间内观察买入')
    base['sniper']=dict(ideal_buy=upper,secondary_buy=lower,stop_loss=stop,take_profit=target,
        note=LABELS[rule]+'独立回踩区间；整分报价；1.5ATR保护与上方空间检查')
    base['checklist']=[dict(item='分型确认',detail=LABELS[rule]+'量价条件已满足',status='满足'),
        dict(item='买入区间',detail=f'{lower:.2f}～{upper:.2f}；仅下一交易日有效',status='满足' if upper-lower>=.009999 else '不满足'),
        dict(item='不追高',detail=f'距回踩中心 {(closes[-1]/pivot-1)*100:+.2f}%',status='不满足' if base['chase_risk'] else '满足'),
        dict(item='结构空间',detail=f'止损{stop:.2f}；目标{target:.2f}；毛盈亏比{rr:.2f}',status='满足' if rr>=1.5 and stop>0 else '不满足')]
    return base


def evaluate_setups(rows):
    if not valid_bars(rows):return []
    closes=[float(r['close']) for r in rows];last=rows[-1]
    ma5,ma10,ma20=(ma(closes,n) for n in (5,10,20))
    rising=ma20>ma(closes[:-5],20)
    mean_volume=sum(float(r['volume']) for r in rows[-21:-1])/20
    strength=float(last['high'])>float(last['low']) and (float(last['close'])-float(last['low']))/(float(last['high'])-float(last['low']))>=.6
    volume=float(last['volume'])
    touch=False
    for i in range(len(rows)-6,len(rows)-1):
        if float(rows[i]['low'])<=ma(closes[:i+1],10)*1.01:touch=True
    matched={
        'ma20_reclaim':entry_signal(rows,'trend') and rising and strength and volume>=mean_volume*1.2,
        'trend_pullback':ma5>ma10>ma20 and rising and touch and closes[-1]>ma5 and closes[-1]>closes[-2] and closes[-1]>=float(last['open']) and volume>=mean_volume*.8,
        'volume_breakout':entry_signal(rows,'breakout') and rising and strength and volume>=mean_volume*1.5,
    }
    return [dict(rule=rule,plan=build_setup_plan(rows,rule)) for rule in PRIORITY if matched[rule]]


def selected_setup(setups):
    return next((s for s in setups if s['plan']['plan_ready']),setups[0] if setups else None)
