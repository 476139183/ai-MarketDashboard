"""冻结规则的逐分钟观察/成交检查；只读本地历史，不自动调参。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from backtest_trend_policy import replay_minutes, summarize
from market_states import VERSION, ZONES, build_market_context, entry_signal

ROOT=Path(__file__).resolve().parent


def aggregate(minutes, feed='massive_regular'):
    groups=defaultdict(dict)
    for row in minutes:
        t=row['timestamp']
        groups[t//900*900][t]=row
    result=[]
    for start,group in sorted(groups.items()):
        if set(group)!={start+i*60 for i in range(15)}:
            continue
        bars=[group[t] for t in sorted(group)]
        result.append(dict(t=start,o=bars[0]['open'],h=max(b['high'] for b in bars),
                           l=min(b['low'] for b in bars),c=bars[-1]['close'],
                           v=sum(b['volume'] for b in bars),feed=feed))
    return result


def replay_stock(conn,symbol,start,*,source='Massive historical aggregates',end='9999-12-31'):
    raw=[dict(row) for row in conn.execute("SELECT * FROM bars WHERE symbol=? AND source=? AND session='regular' AND trading_date<=? ORDER BY timestamp",(symbol,source,end))]
    days=defaultdict(list)
    for row in raw:
        days[row['trading_date']].append(row)
    daily,history,trades=[],[],[]
    counts=Counter();blocked=Counter();tested_days=0;warmup_ready=0;resets=0;previous_day=None
    for day,minutes in sorted(days.items()):
        # 抽样月份可能相隔多年，不能把不同片段拼成连续均线/历史量能。
        if previous_day and (datetime.fromisoformat(day)-datetime.fromisoformat(previous_day)).days>7:
            daily,history=[],[];resets+=1
        previous_day=day
        bars=aggregate(minutes,feed=source)
        if day>=start:
            tested_days+=1;signals={};context=None;latest=None
            warmup_ready+=int(len(daily)>=60)
            for minute in minutes:
                known=minute['timestamp']+60
                closed=[bar for bar in bars if bar['t']+900<=known]
                key=closed[-1]['t'] if closed else None
                now=datetime.fromtimestamp(known,ZONES['us'])
                if context is None or key!=latest:
                    context=build_market_context(daily,history+closed,now)
                    latest=key
                    counts[context['intraday_state']['code']]+=1
                signal=entry_signal(context,px=minute['close'],quote_fresh=True,
                                    session_open=now.hour*60+now.minute<960,now=now)
                signals[known]=signal
                if not signal['actionable']:
                    blocked[signal['reason']]+=1
            completed=replay_minutes(minutes,signals,fee_bps=1,slip_bps=2)
            for trade in completed:
                trade['symbol']=symbol
            trades.extend(completed)
        # 只有全时段真实分钟数据齐全才合成完整日线，且仅供后一天使用。
        first=datetime.fromisoformat(day).replace(hour=9,minute=30,tzinfo=ZONES['us'])
        expected={int(first.timestamp())+i*60 for i in range(390)}
        if {row['timestamp'] for row in minutes}==expected:
            daily.append(dict(t=int(first.timestamp()),o=minutes[0]['open'],h=max(b['high'] for b in minutes),
                              l=min(b['low'] for b in minutes),c=minutes[-1]['close'],v=sum(b['volume'] for b in minutes)))
        history=(history+bars)[-26*10:]
    observed=[day for day in sorted(days) if start<=day<=end]
    warnings=[]
    if not observed:
        warnings.append('所选股票、来源或日期范围内没有分钟样本')
    elif not warmup_ready:
        warnings.append('连续完整日线不足60日，当前样本只能验数据链路，不能评价进场策略收益')
    return dict(symbol=symbol,source=source,days=tested_days,daily_warmup_ready_days=warmup_ready,history_gap_resets=resets,
                first=observed[0] if observed else None,last=observed[-1] if observed else None,state_counts=dict(counts),
                blocked_reasons=dict(blocked),warnings=warnings,summary=summarize(trades),trades=trades)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=ROOT.parent/'.cache/session-bars.sqlite3')
    parser.add_argument('--start',default='2026-09-01')
    parser.add_argument('--end',default='9999-12-31')
    parser.add_argument('--source',choices=['massive','hf'],default='massive')
    parser.add_argument('--symbols',default='NVDA,MU,SNDK')
    parser.add_argument('--output',type=Path,default=ROOT/'cache/daily-volume-replay.json')
    args=parser.parse_args()
    source={'massive':'Massive historical aggregates','hf':'HF public OHLCV-1m'}[args.source]
    if args.start>args.end:
        parser.error('start 不能晚于 end')
    conn=sqlite3.connect(f'file:{args.db}?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    results=[]
    try:
        for symbol in args.symbols.split(','):
            result=replay_stock(conn,symbol,args.start,source=source,end=args.end);results.append(result)
            print(json.dumps({k:result[k] for k in ('symbol','days','state_counts','summary')},ensure_ascii=False),flush=True)
    finally:
        conn.close()
    report=dict(version=VERSION,source=source,results=results,method='历史分钟收盘模拟可见报价；15m收盘更新状态，下一分钟延迟后检查入场带/盈亏比，双边每边1bp费用+2bp不利滑点；同分钟止损优先；收盘强平',
                limitations=['仅美股所选来源的历史样本；规则为候选，不是独立收益证明','不完整交易日不能合成日线；半日市会保守排除；至少60个完整历史交易日供日线热身','历史报价由分钟收盘代理，不代表真实逐笔/盘口执行','公开HF来源的复权与交易所量能覆盖尚未独立核验，不与其他来源拼接','尚未验证A股T+1成交回测，A股只做状态与执行约束检查'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
