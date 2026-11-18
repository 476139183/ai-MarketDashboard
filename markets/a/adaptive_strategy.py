"""预先定义的温和突破/趋势回踩实验；量价指标不冒充资金流。"""
import math

from candidate_monitor import history_issue
from market_scan import qualification_changes

PROFILES = ('moderate_breakout','moderate_breakout_market','trend_pullback','trend_pullback_market')
PHASE2_PROFILES = tuple(kind+'_breadth_'+ranking for kind in ('moderate_breakout','trend_pullback')
                        for ranking in ('liquidity','quality'))


def profile_kind(profile):
    return 'moderate_breakout' if profile.startswith('moderate_breakout') else 'trend_pullback'


def candidate_features(bars):
    if len(bars)<26:
        return None
    sample = bars[-26:]
    if any(not all(math.isfinite(float(b[k])) for k in ('open','high','low','close','volume'))
           or b['close']<=0 or b['volume']<=0 for b in sample):
        return None
    closes = [b['close'] for b in sample]
    close,previous = closes[-1],closes[-2]
    ma5,ma20 = sum(closes[-5:])/5,sum(closes[-20:])/20
    ma20_before = sum(closes[-25:-5])/20
    if not (ma5>ma20 and close>ma20 and ma20>ma20_before):
        return None
    day_gain = close/previous-1
    extension = close/ma20-1
    turnover = sum(b['close']*b['volume'] for b in sample[-20:])/20
    ratio = sample[-1]['volume']/(sum(b['volume'] for b in sample[-21:-1])/20)
    ranges = [max(b['high']-b['low'],abs(b['high']-p['close']),abs(b['low']-p['close']))
              for p,b in zip(sample[-15:-1],sample[-14:])]
    atr_ratio = sum(ranges)/14/close
    if not (0<day_gain<=.06 and extension<=.12 and turnover>=100_000_000 and ratio<=3 and atr_ratio<=.06):
        return None
    high = max(b['high'] for b in sample[-21:-1])
    previous_ma5 = sum(closes[-6:-1])/5
    return {'close':close,'ma5':ma5,'ma20':ma20,'extension_pct':extension*100,
            'avg20_turnover_proxy_yuan':turnover,'volume_ratio':ratio,'atr_pct':atr_ratio*100,
            'trend_quality':(ma20/ma20_before-1)/atr_ratio if atr_ratio>0 else 0,
            'breakout_distance_pct':(close/high-1)*100,'prior20_high':high,
            'moderate_breakout':0<close/high-1<=.025 and ratio>=1.2,
            'trend_pullback':previous<=previous_ma5 and close>ma5 and close>=sample[-1]['open'] and ratio>=1}


def profile_accepts(row, profile):
    if profile not in PROFILES+PHASE2_PROFILES:
        raise ValueError('未知入场实验')
    kind = profile_kind(profile)
    return bool(row and row[kind])


def market_allowed(index):
    allowed = set()
    for i in range(20,len(index)):
        ma = sum(x['close'] for x in index[i-19:i+1])/20
        before = sum(x['close'] for x in index[i-20:i])/20
        if index[i]['close']>ma and ma>=before:
            allowed.add(index[i]['date'])
    return allowed


def ranking_key(row):
    return (-row['avg20_turnover_proxy_yuan'],row['extension_pct'],row['code'])


def market_breadth(histories,calendar):
    indices={d:i for i,d in enumerate(calendar)}
    totals={}
    for bars in histories.values():
        for i in range(20,len(bars)):
            day=bars[i]['date']
            if day not in indices or bars[i]['volume']<=0:
                continue
            j=indices[day]
            if [b['date'] for b in bars[i-20:i+1]]!=calendar[max(0,j-20):j+1]:
                continue
            closes=[b['close'] for b in bars[i-19:i+1]]
            if not all(math.isfinite(x) and x>0 for x in closes):
                continue
            u,n=totals.get(day,(0,0))
            totals[day]=(u+(closes[-1]>sum(closes)/20),n+1)
    return {d:u/n for d,(u,n) in totals.items() if n}


def breadth_allowed(breadth,calendar):
    allowed=set()
    for i in range(5,len(calendar)):
        sample=[breadth.get(d) for d in calendar[i-5:i+1]]
        if all(x is not None for x in sample) and sample[-1]>.55 and sum(sample[-3:])>=sum(sample[:3]):
            allowed.add(calendar[i])
    return allowed


def profile_ranking_key(row,profile):
    if profile.endswith('_quality'):
        return (-row['trend_quality'],-row['avg20_turnover_proxy_yuan'],row['code'])
    return ranking_key(row)


def build_profiles(histories, names, calendar, index, start='2026-01-01',profile_names=PROFILES,breadth=None):
    indices = {d:i for i,d in enumerate(calendar)}
    allowed = market_allowed(index)
    broad_allowed=breadth_allowed(breadth or {},calendar)
    by_kind = {'moderate_breakout':{},'trend_pullback':{}}
    for code,bars in histories.items():
        for i in range(25,len(bars)):
            day = bars[i]['date']
            if day<start or day not in indices:
                continue
            row = candidate_features(bars[i-25:i+1])
            if not row or not (row['moderate_breakout'] or row['trend_pullback']):
                continue
            j = indices[day]
            # 额外5日用于MA20斜率，不能跨缺失交易日拼接。
            if history_issue(bars[i-25:i+1],calendar[max(0,j-25):j+1],day):
                continue
            if [b['date'] for b in bars[i-25:i+1]] != calendar[max(0,j-25):j+1]:
                continue
            row.update(code=code,name=names[code],signal_date=day,status='合格信号',unmet=[])
            for kind in by_kind:
                if row[kind]:
                    by_kind[kind].setdefault(day,[]).append(row)
    profiles,counts = {},{}
    for profile in profile_names:
        signals,coverage,previous = [],{},[]
        for day in calendar:
            if day<start:
                continue
            rows = by_kind[profile_kind(profile)].get(day,[])
            if profile.endswith('_market') and day not in allowed:
                rows=[]
            if '_breadth_' in profile and day not in broad_allowed:
                rows=[]
            rows=sorted(rows,key=lambda r:profile_ranking_key(r,profile))
            changes=qualification_changes(rows,previous)
            continued=set(changes['continuing_codes'])
            chosen=[dict(r,qualification='持续符合' if r['code'] in continued else '新入选') for r in rows[:5]]
            signals.append({'signal_date':day,'stocks':chosen,'new_count':len(changes['new_codes']),
                            'continuing_count':len(continued)})
            coverage[day]=len(rows)
            previous=rows
        profiles[profile]=signals
        counts[profile]=coverage
    return profiles,counts


def rank_adjusted(histories,names,calendar,index,profile,breadth=None):
    if profile not in PROFILES+PHASE2_PROFILES:
        raise ValueError('未知入场实验')
    if profile.endswith('_market') and calendar[-1] not in market_allowed(index):
        return []
    if '_breadth_' in profile and calendar[-1] not in breadth_allowed(breadth or {},calendar):
        return []
    rows=[]
    for code,bars in histories.items():
        if len(bars)<26 or [b['date'] for b in bars[-26:]]!=calendar[-26:]:
            continue
        if history_issue(bars[-26:],calendar,calendar[-1]):
            continue
        row=candidate_features(bars)
        if profile_accepts(row,profile):
            rows.append(dict(row,code=code,name=names[code],signal_date=calendar[-1],status='合格信号',unmet=[]))
    return sorted(rows,key=lambda r:profile_ranking_key(r,profile))
