"""Predeclared crypto strategy hypotheses and causally available market factors."""
from bisect import bisect_left,bisect_right
import math
from statistics import median,pstdev
from crypto_ema_replay import STEP,SMALL,ema


def wilder(values,n):
    result=[None]*len(values)
    if len(values)<n:return result
    avg=sum(values[:n])/n;result[n-1]=avg
    for i in range(n,len(values)):
        avg=(avg*(n-1)+values[i])/n;result[i]=avg
    return result


def indicators(rows):
    closes=[r['c'] for r in rows];tr=[];plus=[];minus=[];gains=[];losses=[]
    for i,r in enumerate(rows):
        prev=rows[i-1] if i else r
        tr.append(max(r['h']-r['l'],abs(r['h']-prev['c']),abs(r['l']-prev['c'])))
        up=r['h']-prev['h'];down=prev['l']-r['l']
        plus.append(up if up>down and up>0 else 0);minus.append(down if down>up and down>0 else 0)
        change=r['c']-prev['c'];gains.append(max(change,0));losses.append(max(-change,0))
    atr=wilder(tr,20);atr14=wilder(tr,14);p=wilder(plus,14);m=wilder(minus,14);dx=[]
    for i in range(13,len(rows)):
        di_p=100*p[i]/atr14[i] if atr14[i] else 0;di_m=100*m[i]/atr14[i] if atr14[i] else 0
        dx.append(100*abs(di_p-di_m)/(di_p+di_m) if di_p+di_m else 0)
    adx=[None]*13+wilder(dx,14)
    ag=wilder(gains,14);al=wilder(losses,14);rsi=[]
    for gain,loss in zip(ag,al):
        rsi.append(None if gain is None else 50 if gain==loss==0 else 100 if loss==0 else 100-100/(1+gain/loss))
    return dict(atr=atr,adx=adx,rsi=rsi,ema20=ema(closes,20),ema50=ema(closes,50))


def daily_sma_bias(rows):
    groups={}
    for r in rows:groups.setdefault(r['t']//86400000*86400000,[]).append(r)
    completed=[]
    for t,children in sorted(groups.items()):
        if [r['t'] for r in children]==list(range(t,t+86400000,STEP)):completed.append((t+86400000,children[-1]['c']))
    output={}
    for r in rows:
        t=r['t']+STEP;past=[c for closed,c in completed if closed<=t]
        output[t]=1 if len(past)>=40 and sum(past[-10:])/10>sum(past[-40:])/40 else -1 if len(past)>=40 and sum(past[-10:])/10<sum(past[-40:])/40 else 0
    return output


def strategy_catalog(rows):
    ind=indicators(rows);closes=[r['c'] for r in rows];catalog={}
    risk={r['t']+STEP:dict(stop_pct=min(.04,max(.005,2*ind['atr'][i]/r['c'])) if ind['atr'][i] is not None else .02,target_pct=None) for i,r in enumerate(rows)}
    for n in (12,42):
        state={r['t']+STEP:(1 if r['c']>rows[i-n]['c'] else -1 if r['c']<rows[i-n]['c'] else 0) if i>=n else 0 for i,r in enumerate(rows)}
        catalog[f'momentum_{n}']=dict(state=state,entry=dict(state),risk=risk)
    for n,m in ((20,10),(55,20)):
        state={};entry={};held=0
        for i,r in enumerate(rows):
            t=r['t']+STEP;trigger=0
            if i>=n:
                old=rows[i-n:i];short=rows[i-m:i]
                if held==1 and r['c']<min(x['l'] for x in short):held=0
                elif held==-1 and r['c']>max(x['h'] for x in short):held=0
                if r['c']>max(x['h'] for x in old):held=1;trigger=1
                elif r['c']<min(x['l'] for x in old):held=-1;trigger=-1
            state[t]=held;entry[t]=trigger
        catalog[f'donchian_{n}_{m}']=dict(state=state,entry=entry,risk=risk)
    for threshold in (20,25):
        state={};entry={};held=0
        for i,r in enumerate(rows):
            t=r['t']+STEP;trigger=0;fast=ind['ema20'][i];slow=ind['ema50'][i]
            if held==1 and (r['c']<slow or fast<slow):held=0
            elif held==-1 and (r['c']>slow or fast>slow):held=0
            if i>=60 and ind['adx'][i] is not None and ind['adx'][i]>=threshold:
                if fast>slow and fast>ind['ema20'][i-6] and closes[i-1]<=ind['ema20'][i-1] and r['c']>fast:held=1;trigger=1
                elif fast<slow and fast<ind['ema20'][i-6] and closes[i-1]>=ind['ema20'][i-1] and r['c']<fast:held=-1;trigger=-1
            state[t]=held;entry[t]=trigger
        catalog[f'pullback_adx{threshold}']=dict(state=state,entry=entry,risk=risk)
    for n in (20,30):
        state={};entry={};held=0
        for i,r in enumerate(rows):
            t=r['t']+STEP;trigger=0
            if i>=n and ind['adx'][i] is not None:
                current=closes[i-n+1:i+1];previous=closes[i-n:i]
                middle=sum(current)/n;old_mid=sum(previous)/n
                lower=middle-2*pstdev(current);upper=middle+2*pstdev(current)
                old_lower=old_mid-2*pstdev(previous);old_upper=old_mid+2*pstdev(previous)
                if held==1 and (r['c']>=middle or ind['adx'][i]>=25):held=0
                elif held==-1 and (r['c']<=middle or ind['adx'][i]>=25):held=0
                if ind['adx'][i]<20:
                    if closes[i-1]<old_lower and r['c']>=lower:held=1;trigger=1
                    elif closes[i-1]>old_upper and r['c']<=upper:held=-1;trigger=-1
            state[t]=held;entry[t]=trigger
        catalog[f'bollinger_{n}']=dict(state=state,entry=entry,risk=risk)
    state=daily_sma_bias(rows)
    catalog['daily_sma10_40']=dict(state=state,entry={t:v if t%86400000==0 else 0 for t,v in state.items()},risk=risk)
    return catalog


def quantile(values,q):
    values=sorted(values);x=(len(values)-1)*q;i=int(x)
    return values[i]+(values[min(i+1,len(values)-1)]-values[i])*(x-i)


def factor_features(data,start,end):
    slow=data['slow'];fast=data['fast'];stats=data['stats'];slow_stamps=[r['t'] for r in slow];fast_stamps=[r['t'] for r in fast]
    stat_stamps=[r[0] for r in stats['ratio']]
    funding={int(t):v for t,v in data['funding']['rates'].items()};funding_stamps=sorted(funding)
    discrepancies={r['t']:r['relative_error'] for r in data.get('audit',{}).get('taker_mismatches',[])}
    result={}
    for boundary in range(start,end,STEP):
        # 4h factor timestamp is bucket start. Add full interval plus5m publication buffer.
        j=bisect_right(stat_stamps,boundary-STEP-SMALL)-1
        i=bisect_left(slow_stamps,boundary)-1
        if j<42 or i<20:raise ValueError('Insufficient factor prehistory')
        stamp=stat_stamps[j]
        ratio=stats['ratio'][j][1];ratio_history=[r[1] for r in stats['ratio'][j-42:j]]
        _,sell,buy=stats['taker'][j];total=sell+buy
        oi=stats['oi'][j][2];old_oi=stats['oi'][j-1][2]
        turnover=total/((oi+old_oi)/2) if oi+old_oi else 0
        previous_turnover=[]
        for k in range(j-20,j):
            amount=stats['taker'][k][1]+stats['taker'][k][2];avg_oi=(stats['oi'][k][2]+stats['oi'][k-1][2])/2
            previous_turnover.append(amount/avg_oi if avg_oi else 0)
        reference_turnover=median(previous_turnover)
        reference_volume=median(r['v'] for r in slow[i-20:i])
        lo=bisect_left(fast_stamps,boundary-7*86400000);hi=bisect_left(fast_stamps,boundary)
        profile=fast[lo:hi]
        if len(profile)!=7*288:raise ValueError('Incomplete volume-profile prehistory')
        bottom=min(r['l'] for r in profile);top=max(r['h'] for r in profile);width=(top-bottom)/50
        weights=[0.0]*50
        if width:
            for r in profile:
                typical=(r['h']+r['l']+r['c'])/3;bucket=min(49,max(0,int((typical-bottom)/width)))
                weights[bucket]+=r['v']
            poc=bottom+(max(range(50),key=lambda k:weights[k])+.5)*width
        else:poc=bottom
        f=bisect_right(funding_stamps,boundary-SMALL)-1
        funding_rate=funding[funding_stamps[f]] if f>=0 else None
        feature=dict(stat_at=stamp,stat_available_at=stamp+STEP+SMALL,price_bar_at=slow[i]['t'],
            flow_volume_error=discrepancies.get(stamp,0),flow_quality=discrepancies.get(stamp,0)<=.01,account_ratio=ratio,flow=(buy-sell)/total if total else 0,oi_change=oi/old_oi-1 if old_oi else 0,
            rvol=slow[i]['v']/reference_volume if reference_volume else 0,turnover=turnover,
            turnover_relative=turnover/reference_turnover if reference_turnover else 0,
            poc=poc,poc_delta=(slow[i]['c']-poc)/poc if poc else 0,funding_rate=funding_rate,
            crowded_long=funding_rate is not None and ratio>=quantile(ratio_history,.9) and funding_rate>.0001,
            crowded_short=funding_rate is not None and ratio<=quantile(ratio_history,.1) and funding_rate<-.0001)
        if feature['stat_available_at']>boundary:raise AssertionError('Factor publication lookahead')
        result[boundary]=feature
    return result


def apply_factor(seed,features,gate):
    entry={}
    for t,side in seed['entry'].items():
        f=features.get(t)
        allowed=False
        if side and f:
            flow=side*f['flow']>=.05;oi=f['oi_change']>0;volume=f['rvol']>=1.2;turnover=f['turnover_relative']>=1
            poc=side*f['poc_delta']>=0;crowded=f['crowded_long'] if side==1 else f['crowded_short']
            known_funding=f.get('funding_rate') is not None
            if gate=='flow':allowed=f.get('flow_quality',True) and flow
            elif gate=='oi':allowed=oi
            elif gate=='volume':allowed=volume
            elif gate=='turnover':allowed=turnover
            elif gate=='poc':allowed=poc
            elif gate=='crowding':allowed=known_funding and not crowded
            elif gate=='combined':allowed=f.get('flow_quality',True) and known_funding and not crowded and sum((flow,oi,volume and turnover,poc))>=3
            else:raise ValueError('Unknown factor gate')
        entry[t]=side if allowed else 0
    return dict(seed,state=dict(seed['state']),entry=entry)
