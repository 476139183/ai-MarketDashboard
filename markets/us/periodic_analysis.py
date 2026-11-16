#!/usr/bin/env python3
"""Periodic local SNDK market observation; no trade execution."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from research_context import BJ, NY, SYMBOLS
sys.path.insert(0,str(Path(__file__).resolve().parent/'dashboard'))
from engine import fetch_nasdaq_quote, fetch_nasdaq_intraday_points

OUT = Path(__file__).resolve().parent / '.cache' / 'market-monitor'


def parse_quote_time(value):
    if not value: return None
    try:
        return datetime.strptime(value.strip().removesuffix(' ET'), '%b %d, %Y %I:%M %p').replace(tzinfo=NY)
    except ValueError:
        return None


def sampled_direction(points, now):
    """Exploratory 15m sample direction; strength is not a probability."""
    now=now.astimezone(NY)
    buckets={}
    for point in sorted(points,key=lambda p:p['dt_ny']):
        dt=point['dt_ny'].astimezone(NY)
        key=dt.replace(minute=(dt.minute//15)*15,second=0,microsecond=0)
        if key+timedelta(minutes=15)<=now:
            buckets.setdefault(key,[]).append(float(point['c']))
    ordered=sorted((k,v) for k,v in buckets.items() if len(v)>=5)
    first=None;latest=None
    for idx in range(2,len(ordered)):
        group=ordered[idx-2:idx+1]
        if any((group[j+1][0]-group[j][0])!=timedelta(minutes=15) for j in (0,1)):
            first=None;latest=None;continue
        values=[v[-1] for _,v in group]
        direction='偏多' if values[0]<values[1]<values[2] else '偏空' if values[0]>values[1]>values[2] else None
        if direction is None or values[0]<=0:
            first=None;latest=None;continue
        end=group[-1][0]+timedelta(minutes=15)
        strength=min(100,round(50+20*abs(values[-1]/values[0]-1)*100))
        if first is None or first['direction']!=direction:
            first={'direction':direction,'first_price':values[-1], 'first_time_bj':end.astimezone(BJ).isoformat()}
        latest={**first,'strength':strength,'price':values[-1], 'observed_bj':end.astimezone(BJ).isoformat()}
    # The last signal must include the most recently completed quarter-hour.
    end_key=now.replace(minute=(now.minute//15)*15,second=0,microsecond=0)
    return latest if latest and datetime.fromisoformat(latest['observed_bj']).astimezone(NY)==end_key else None


def build_report(now, quote, points, symbol="SNDK"):
    now=now.astimezone(NY)
    minutes=now.hour*60+now.minute
    phase='盘前' if 240<=minutes<570 else '盘中' if 570<=minutes<960 else '盘后' if 960<=minutes<1200 else '休市'
    if now.weekday()>=5:phase='休市'
    quote=quote or {}; qt=parse_quote_time(quote.get('ts'))
    age=(now-qt).total_seconds() if qt else None
    status=str(quote.get('market_status') or '').lower()
    expected={'盘前':'pre', '盘中':'open', '盘后':'after'}.get(phase)
    fresh=bool(qt and age is not None and -60<=age<=300 and qt.date()==now.date() and expected and expected in status and quote.get('price') is not None)
    start={'盘前':240,'盘中':570,'盘后':960}.get(phase)
    end={'盘前':570,'盘中':960,'盘后':1200}.get(phase)
    filtered=[]
    if start is not None:
        filtered=[p for p in points if p['dt_ny'].date()==now.date() and start <= p['dt_ny'].hour*60+p['dt_ny'].minute < end and p['dt_ny']<=now]
    filtered.sort(key=lambda p:p['dt_ny'])
    values=[float(p['c']) for p in filtered]
    result=sampled_direction(filtered,now) if fresh else None
    trend=('上行' if result['direction']=='偏多' else '下行') if result else '数据不足或震荡'
    return {'generated_bj':now.astimezone(BJ).isoformat(), 'symbol':symbol,'phase':phase,
        'quote_source':'Nasdaq quote','quote_time_et':quote.get('ts'), 'quote_fresh':fresh,
        'current_price':quote.get('price') if fresh else None,
        'previous_close_reference':quote.get('prev_close'),
        'sample_source':'Nasdaq chart price samples', 'sample_count':len(values),
        'sample_low':min(values) if values else None,'sample_high':max(values) if values else None,
        'sample_last_bj':filtered[-1]['dt_ny'].astimezone(BJ).isoformat() if filtered else None,
        'trend_15m_sample':trend,'direction_sample':result,'signal':'观望',
        'reason':'仅有价格采样，缺少可信分钟成交量与经过验证的盘前策略；暂不给交易信号和止损。' if fresh else '报价过期、时段不匹配或不可用，暂不给交易信号。',
        'limitations':'采样高低点可能遗漏实际极值；不代表完整市场或真实分钟OHLC，无法计算可靠VWAP。'}


def collect(symbol):
    errors=[]
    try:q=fetch_nasdaq_quote(symbol,force=True)
    except Exception as e:q=None;errors.append('报价获取失败: '+str(e))
    try:points,_=fetch_nasdaq_intraday_points(symbol)
    except Exception as e:points=[];errors.append('分时获取失败: '+str(e))
    report=build_report(datetime.now(NY),q,points,symbol=symbol)
    report['errors']=errors
    return report


def collect_many(symbols):
    with ThreadPoolExecutor(max_workers=min(4, max(1,len(symbols)))) as pool:
        futures=[pool.submit(collect,symbol) for symbol in symbols]
        reports=[]
        for symbol,future in zip(symbols,futures):
            try:
                reports.append(future.result())
            except Exception as exc:
                report=build_report(datetime.now(NY),None,[],symbol=symbol)
                report['errors']=['采集失败: '+str(exc)]
                reports.append(report)
        return reports


def retain_previous_directions(report,out):
    """Keep the last observed directional sample within the same market session."""
    try:
        current_day=datetime.fromisoformat(report['generated_bj']).astimezone(NY).date()
        history=out/'history.jsonl'
        with history.open('rb') as f:
            f.seek(0,2); size=f.tell(); f.seek(max(0,size-1024*1024))
            lines=f.read().splitlines()
    except (OSError,KeyError,ValueError):
        lines=[];current_day=None
    previous={}
    current={(item.get('symbol'),item.get('phase')):item.get('direction_sample') for item in report.get('items',[])}
    for line in reversed(lines):
        try:
            old=json.loads(line)
            if datetime.fromisoformat(old['generated_bj']).astimezone(NY).date()!=current_day:continue
            for item in old.get('items',[]):
                key=(item.get('symbol'),item.get('phase'))
                old_signal=item.get('direction_sample')
                active=current.get(key)
                same=active and old_signal and active.get('direction')==old_signal.get('direction') and active.get('first_time_bj')==old_signal.get('first_time_bj')
                if key not in previous and old_signal and not same:
                    previous[key]=old_signal
        except (ValueError,KeyError,TypeError):continue
    report['items']=[{**item,'previous_direction_sample':previous.get((item.get('symbol'),item.get('phase')))} for item in report.get('items',[])]
    return report


def persist(report,out):
    out.mkdir(parents=True,exist_ok=True)
    tmp=out/'latest.tmp';tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,out/'latest.json')
    with (out/'history.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(report,ensure_ascii=False)+'\n')


def seconds_to_next_quarter(timestamp):
    return 900 - (timestamp % 900)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--watch',action='store_true',help='每15分钟运行一次，Ctrl+C停止')
    p.add_argument('--output-dir',type=Path,default=OUT)
    args=p.parse_args()
    while True:
        reports=collect_many(SYMBOLS)
        report={'generated_bj':datetime.now(BJ).isoformat(),'items':reports}
        report=retain_previous_directions(report,args.output_dir)
        persist(report,args.output_dir);print(json.dumps(report,ensure_ascii=False),flush=True)
        if not args.watch:break
        time.sleep(seconds_to_next_quarter(time.time()))

if __name__=='__main__':main()
