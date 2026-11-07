"""Original US equities radar. Provider gaps and sampled prices stay explicit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
import re
import threading
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import engine
from sina_intraday import fetch_rows as sina_rows
from contract_radar import ema
from radar_periods import MINUTE_PERIODS, aggregate_rows, period_minutes

NY=ZoneInfo('America/New_York')
SESSIONS={'pre':(240,570,'盘前'),'regular':(570,960,'盘中'),'post':(960,1200,'盘后')}

def session_of(stamp):
    dt=datetime.fromtimestamp(stamp/1000,NY)
    if dt.weekday()<5:
        for key,(start,end,_) in SESSIONS.items():
            if start<=dt.hour*60+dt.minute<end:return key
    return 'closed'


def stock_bars(rows, source_minutes, minutes, now, include_open=False):
    groups={}
    for r in rows:
        stamp=int(r['t'])*1000
        phase=session_of(stamp)
        if phase=='closed':continue
        dt=datetime.fromtimestamp(stamp/1000,NY)
        start,end,_=SESSIONS[phase]
        anchor=int(dt.replace(hour=start//60,minute=start%60,second=0,microsecond=0).timestamp()*1000)
        end_at=int(dt.replace(hour=end//60,minute=end%60,second=0,microsecond=0).timestamp()*1000)
        if stamp+source_minutes*60000>end_at:continue
        groups.setdefault((anchor,phase),[]).append([stamp,r['o'],r['h'],r['l'],r['c'],r['v'],0])
    bars=[]
    for (anchor,phase),children in groups.items():
        for r in aggregate_rows(children,source_minutes=source_minutes,target_minutes=minutes,now=now,anchor_ms=anchor,include_open=include_open):
            bars.append(dict(time=r[0],open=r[1],high=r[2],low=r[3],close=r[4],volume=r[5],session=phase,complete=r[0]+minutes*60000<=now))
    return sorted(bars,key=lambda b:b['time'])


def quote_time(value):
    # Date-only closes have no usable intraday quote timestamp.
    for fmt in ('%b %d, %Y %I:%M %p ET','%b %d, %Y %I:%M:%S %p ET'):
        try:return int(datetime.strptime(str(value),fmt).replace(tzinfo=NY).timestamp()*1000)
        except ValueError:pass
    return None


def analyze_stock(symbol,bars,quote,source,minutes,session,now,sampled=False):
    coverage={key:dict(label=label,count=0,last_at=None) for key,(_,_,label) in SESSIONS.items()}
    latest_date=datetime.fromtimestamp(bars[-1]['time']/1000,NY).date() if bars else None
    for bar in bars:
        if datetime.fromtimestamp(bar['time']/1000,NY).date()==latest_date:
            c=coverage[bar['session']];c['count']+=1;c['last_at']=bar['time']+minutes*60000
    selected=[b for b in bars if session=='all' or b['session']==session]
    current=session_of(now)
    last=selected[-1] if selected else None
    stamp=quote_time(quote.get('ts'))
    fresh=stamp is not None and -5000<=now-stamp<=120000 and session_of(stamp)==current and current!='closed'
    complete=len(selected)>=60 and not sampled
    structure_fresh=bool(last and current!='closed' and last['session']==current
                         and 0<=now-(last['time']+minutes*60000)<minutes*60000+120000)
    valid=fresh and complete and structure_fresh
    valid_until=None
    if valid:
        dt=datetime.fromtimestamp(now/1000,NY)
        end=SESSIONS[current][1]
        session_end=int(dt.replace(hour=end//60,minute=end%60,second=0,microsecond=0).timestamp()*1000)
        valid_until=min(stamp+120000,last['time']+2*minutes*60000+120000,session_end)
    closes=[b['close'] for b in selected]
    e20=ema(closes,20) if complete else []
    e50=ema(closes,50) if complete else []
    direction='observe'
    if valid and closes[-1]>e20[-1]>e50[-1] and e20[-1]>e20[-7]:direction='bullish'
    elif valid and closes[-1]<e20[-1]<e50[-1] and e20[-1]<e20[-7]:direction='bearish'
    price=quote.get('price')
    if price is None and bars:price=bars[-1]['close']
    return dict(symbol=symbol,name=quote.get('name') or symbol,source=source,currency='USD',
                price=price,quote_source='Nasdaq',quote_time_text=quote.get('ts'),quote_at=stamp,quote_fresh=fresh,bar_close_at=last['time']+minutes*60000 if last else None,
                direction=direction,label={'bullish':'看涨倾向','bearish':'看跌倾向','observe':'观望 / 待确认'}[direction],
                valid=valid,valid_until=valid_until,structure_fresh=structure_fresh,period_minutes=minutes,session=current,sampled=sampled,
                coverage=coverage,coverage_date=str(latest_date) if latest_date else None,
                ema20=e20[-1] if e20 else None,ema50=e50[-1] if e50 else None,
                support=min(b['low'] for b in selected[-20:]) if selected else None,
                resistance=max(b['high'] for b in selected[-20:]) if selected else None,
                candles=selected[-120:],line=[],warning='',error=None,
                reason='分时采样仅供观察，不生成 K 线或趋势信号' if sampled else
                '需要新鲜报价、当前时段结构和至少 60 根完整 K 线' if not valid else '收盘价、EMA20 / EMA50 与 EMA20 近 6 根斜率综合判断')


def massive_rows(symbol,source_minutes,now):
    key=os.environ.get('MASSIVE_API_KEY','').strip()
    if not key:raise ValueError('未配置 MASSIVE_API_KEY')
    end=datetime.fromtimestamp(now/1000,NY).date()
    start=end-timedelta(days=30 if source_minutes>=15 else 7)
    url=f'https://api.massive.com/v2/aggs/ticker/{symbol}/range/{source_minutes}/minute/{start}/{end}?'+urlencode(dict(adjusted='true',sort='desc',limit=50000))
    # Key in header; never returned in URLs, errors or the browser.
    payload=engine._http_json(url,timeout=10,extra_headers={'Authorization':'Bearer '+key})
    if payload.get('status') in ('ERROR','NOT_AUTHORIZED') or payload.get('next_url'):
        raise ValueError('Massive 权限不足或数据分页未完整返回')
    rows=[dict(t=int(r['t'])//1000,**{k:r[k] for k in ('o','h','l','c','v')}) for r in payload.get('results',[])]
    if not rows:raise ValueError('Massive 当前权限或日期范围无分钟线')
    return rows


def load_stock(symbol,minutes,session,provider,now):
    quote=engine.fetch_nasdaq_quote(symbol) or {}
    source_minutes=5 if minutes==10 else 30 if minutes==60 else minutes
    failures=[];bars=[];live_bars=[];source='';sampled=False;line=[]
    choices=([('massive' if os.environ.get('MASSIVE_API_KEY') else 'nasdaq' if session in ('pre','post') else 'sina')] if provider=='auto' else [provider])
    if choices[0] != 'nasdaq':choices.append('nasdaq')
    for choice in choices:
        try:
            if choice=='massive':
                raw=massive_rows(symbol,source_minutes,now);source='Massive · 权限与延迟取决于订阅'
            elif choice=='sina':
                raw=sina_rows(symbol,source_minutes);source='新浪美股 · 常规盘 OHLCV'
            elif choice=='yahoo':
                raw,meta,origin=engine.fetch_chart(symbol,'1mo' if source_minutes>=15 else '5d',str(source_minutes)+'m',prefer_cache=False)
                if not raw:raise ValueError('无分钟线')
                source=str(origin)
            else:
                points,chart_meta=engine.fetch_nasdaq_intraday_points(symbol)
                if quote.get('day_volume') is None:
                    quote['day_volume']=chart_meta.get('day_volume');quote['day_volume_date']=chart_meta.get('day_volume_date')
                line=[dict(time=int(p['t'])*1000,value=p['c'],session=session_of(int(p['t'])*1000)) for p in points
                      if session_of(int(p['t'])*1000)!='closed']
                source='Nasdaq · 分时采样';sampled=True;break
            bars=stock_bars(raw,source_minutes,minutes,now)
            live_bars=stock_bars(raw,source_minutes,minutes,now,include_open=True)
            if not bars:raise ValueError('无完整 K 线')
            break
        except Exception:
            failures.append({'sina':'新浪美股分钟行情不可用','yahoo':'Yahoo / yfinance 不可用','massive':'Massive 不可用或权限不足','nasdaq':'Nasdaq 分时不可用'}[choice])
    result=analyze_stock(symbol,bars,quote,source or '暂无分钟源',minutes,session,now,sampled)
    result['chart_candles']=[b for b in live_bars if session=='all' or b['session']==session][-120:]
    if quote.get('price') is None and live_bars:
        last=live_bars[-1]
        result.update(price=last['close'],quote_source=source+' K线参考',quote_at=None,quote_time_text=datetime.fromtimestamp(last['time']/1000,NY).isoformat()+'（K线起点，非逐笔报价）')
    result['volume_unit']='股'
    result['day_volume']=quote.get('day_volume')
    result['day_volume_source']='Nasdaq'
    result['day_volume_date']=quote.get('day_volume_date')
    result['line']=[p for p in line if session=='all' or p['session']==session]
    if line:
        if result['price'] is None:
            result.update(price=line[-1]['value'],quote_at=line[-1]['time'],quote_source='Nasdaq 分时采样')
        latest_date=datetime.fromtimestamp(line[-1]['time']/1000,NY).date()
        result['coverage_date']=str(latest_date)
        for point in line:
            if datetime.fromtimestamp(point['time']/1000,NY).date()==latest_date:
                coverage=result['coverage'][point['session']]
                coverage['count']+=1;coverage['last_at']=point['time']

    result['warning']='；'.join(failures+(['Nasdaq 报价与分时分别标注；采样不含真实 OHLCV'] if sampled else []))
    if source.startswith('新浪'):result['warning']+='；常规盘分钟线，实际延迟以 K 线源时间为准；盘前盘后请切换对应时段查看 Nasdaq 分时'
    if not bars and not line:result['error']='分钟行情缺失；可配置 Massive 获得独立数据源'
    return result


class USRadar:
    def __init__(self,refresh_seconds=60):
        self.refresh_seconds=refresh_seconds
        self.lock=threading.Lock();self.states={}
    def payload(self,symbols,minutes=15,session='all',provider='auto'):
        minutes=period_minutes(minutes)
        if minutes not in MINUTE_PERIODS or session not in ('all',*SESSIONS) or provider not in ('auto','nasdaq','massive','yahoo','sina'):
            raise ValueError('不支持的美股周期、时段或行情源')
        symbols=tuple(dict.fromkeys(str(s).strip().upper() for s in symbols))
        if not 1<=len(symbols)<=8 or any(not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}',s) for s in symbols):
            raise ValueError('每次支持 1–8 个美股代码')
        key=(symbols,minutes,session,provider);now=int(time.time()*1000)
        with self.lock:
            # Bound memory even when users enter arbitrary symbol combinations.
            if key not in self.states:
                if len(self.states)>=64:
                    idle=next((k for k,v in self.states.items() if v['status']!='running'),None)
                    if idle is None:raise ValueError('扫描任务较多，请稍后重试')
                    del self.states[idle]
                self.states[key]=dict(items=[],generated_at=None,status='idle')
            state=self.states[key]
            if state['status']!='running' and (not state['generated_at'] or now-state['generated_at']>=self.refresh_seconds*1000):
                state['status']='running'
                threading.Thread(target=self.refresh,args=(key,),daemon=True).start()
            result=dict(state)
            result['items']=[]
            for saved in state['items']:
                row=dict(saved)
                row['quote_fresh']=row['quote_at'] is not None and -5000<=now-row['quote_at']<=120000
                if row['valid'] and (row.get('valid_until') is None or now>=row['valid_until']):
                    row.update(valid=False,direction='observe',label='观望 / 待确认',reason='报价或时段结构已过期，等待后续行情')
                result['items'].append(row)
            return dict(**result,period_minutes=minutes,source='US equities',session=session,provider=provider,cache_seconds=self.refresh_seconds,
                        note='公开源可能限流或延迟；时段依据美东常规时间，节假日与提前收市以源数据为准')
    def refresh(self,key):
        symbols,minutes,session,provider=key
        def load(symbol):
            try:return load_stock(symbol,minutes,session,provider,int(time.time()*1000))
            except Exception:
                result=analyze_stock(symbol,[],{},'暂无行情',minutes,session,int(time.time()*1000))
                result['error']='行情暂不可用';return result
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:items=list(pool.map(load,symbols))
            with self.lock:self.states[key]=dict(items=items,generated_at=int(time.time()*1000),status='idle')
        except Exception:
            with self.lock:self.states[key].update(status='idle',generated_at=int(time.time()*1000))

SERVICE=USRadar()
LIVE_SERVICE=USRadar(refresh_seconds=15)
