"""昨日存档池→今天已完成分钟的独立反事实回放；不写前向账本。"""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta
import csv
import gzip
import json
from pathlib import Path
import tempfile
import a_paper_research as R
import a_share_paper as P
from stock_setups import evaluate_setups,selected_setup


def replay_day(sample,as_of):
    cutoff=datetime.fromisoformat(as_of);signal=sample['signal_date'];today=sample['trade_date']
    if cutoff.tzinfo is None or cutoff.astimezone(P.BJ).date().isoformat()!=today:raise ValueError('回放截止时间必须属于目标日并包含时区')
    radar=R.setups_radar(sample,signal)
    radar.update(counterfactual=True,generated_at_kind='为研究假设昨日16:00生成；不是当时真实名单')
    calendar=sample.get('calendar') or [b['date'] for b in sample['benchmark']]
    if calendar[-2:]!=[signal,today]:raise ValueError('信号日和回放日不是已验证的相邻交易日')
    history={c:[r for r in rows if r['date']<=signal] for c,rows in sample['daily'].items()}
    minutes={c:[b for b in rows if b['time'][:10]==today and datetime.fromisoformat(b['time']).replace(tzinfo=P.BJ)<=cutoff and b['time'][11:16] in R.END_TIMES] for c,rows in sample['minutes'].items()}
    for rows in minutes.values():
        if any(a['time']>=b['time'] for a,b in zip(rows,rows[1:])):raise ValueError('分钟数据重复或乱序')
    times=sorted({b['time'] for rows in minutes.values() for b in rows})
    observations=[]
    for code,rows in sorted(history.items()):
        setup=selected_setup(evaluate_setups(rows));bars=minutes.get(code,[]);plan=setup['plan'] if setup else None
        z=plan['sniper'] if plan else {}
        in_band=[b for b in bars if z and z['secondary_buy']<=P.ASharePaperAccount.fill(b['open'],True,P.DEFAULTS)<=z['ideal_buy']]
        observations.append(dict(code=code,name=sample['names'].get(code,code),strategy_rule=setup['rule'] if setup else None,
            plan=plan,market_pass=radar['market_pass'],open_samples_in_plan=len(in_band),first_in_plan=in_band[0]['time'] if in_band else None,
            minute_bars=len(bars),first_open=bars[0]['open'] if bars else None,last_close=bars[-1]['close'] if bars else None,
            signal_close=rows[-1]['close'] if rows else None,
            from_open_pct=(bars[-1]['close']/bars[0]['open']-1)*100 if bars and bars[0]['open']>0 else None,
            high=max((b['high'] for b in bars),default=None),low=min((b['low'] for b in bars),default=None),
            coverage_end=bars[-1]['time'] if bars else None))
    with tempfile.TemporaryDirectory(prefix='a-day-replay-') as tmp:
        account=P.ASharePaperAccount(Path(tmp)/'research.sqlite3',source=R.HIST_SOURCE)
        account.configure(P.DEFAULTS)
        account.set_enabled(True,now=int(datetime.fromisoformat(today+'T09:29:00+08:00').timestamp()*1000))
        for time in times:
            opened=datetime.fromisoformat(time).replace(tzinfo=P.BJ)-timedelta(minutes=15);stamp=int(opened.timestamp()*1000)
            packs={}
            for code,bars in minutes.items():
                bar=next((b for b in bars if b['time']==time),None);rows=history.get(code,[])
                if not bar or not rows or rows[-1]['date']!=signal or bar['volume']<=0:continue
                prior=rows[-1]['close'];px=bar['open'];ratio=.2 if code.startswith(('300','301','688')) else .1
                lo,hi=P.money(prior*(1-ratio)),P.money(prior*(1+ratio))
                if not lo*1.005<px<hi*.995:continue
                packs[code]=dict(daily=rows,quote=dict(symbol=code,name=sample['names'].get(code,code),source=R.HIST_SOURCE,
                    price=px,bid=px,ask=px,volume=bar['volume'],previous_close=prior,quote_at=stamp,lower_limit=lo,upper_limit=hi))
            account.step(radar,calendar,packs,now=stamp)
        # Only mark positions after the corresponding bar has closed; never fill at that close.
        with account.transaction() as state:
            for p in state['positions']:
                bars=minutes.get(p['code'],[])
                if bars:p.update(last_price=bars[-1]['close'],quote_at=int(datetime.fromisoformat(bars[-1]['time']).replace(tzinfo=P.BJ).timestamp()*1000))
        account_state=account.snapshot(now=int(cutoff.timestamp()*1000),include_all_trades=True)
    return dict(counterfactual=True,as_of=as_of,signal_date=signal,trade_date=today,scope='昨日已存档且已通过证券/市值/流动性筛选的候选与观察池；不是重新扫描全市场',
        coverage_end=max(times) if times else None,radar=radar,observations=observations,account=account_state,
        failures=sample.get('failures',{}),limitations=R.LIMITATIONS+['名单为今天按昨日完成日线重建，非昨日真实存档；绝不进入前向账户。',
        '日内只用已完成15分钟K线的开盘采样，至少等下一条新采样报价；不声称真实盘口成交。',
        '本回放独立10万元空仓起步；今日买入受T+1限制，不能在今天平仓计算胜率。'])


def collect(snapshot,signal,today):
    from feeds.cn import from_sina
    from replay import fetch
    from radar import _hs300
    if snapshot['signal_date']!=signal:raise ValueError('存档信号日不匹配')
    stocks={r['code']:r for r in snapshot['strategy_candidates']+snapshot['watch_pool']}
    benchmark=_hs300(signal);calendar=P.market_calendar(today)
    if calendar[-2:]!=[signal,today]:raise ValueError('行情未验证目标日历')
    daily={};minutes={};failures={}
    def one(code):
        rows,source=from_sina(code,days=180);rows=[r for r in rows if r['date']<=signal]
        if not rows or rows[-1]['date']!=signal:raise ValueError('日线未更新到信号日')
        _,_,bars=fetch(code)
        return rows,[r for r in bars if r['time'][:10]==today],source
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(one,c):c for c in stocks}
        for f in as_completed(jobs):
            code=jobs[f]
            try:rows,bars,source=f.result();daily[code]=rows;minutes[code]=bars
            except Exception as exc:failures[code]=str(exc)[:200]
            print('行情采集',len(daily)+len(failures),'/',len(stocks),flush=True)
    return dict(signal_date=signal,trade_date=today,daily=daily,minutes=minutes,benchmark=benchmark,calendar=calendar,
        names={c:r['name'] for c,r in stocks.items()},failures=failures,archived_snapshot=snapshot)


def write_report(out,result):
    state=result['account'];r=result['radar'];lines=[f"# {result['trade_date']} A股日内回放",'',
        f"实际生成时间：{result['as_of']}。信号数据截至{result['signal_date']}收盘；今天行情覆盖至{result['coverage_end']}。",
        '', '**这是今天重建昨日计划的独立反事实回放，没有改动前向账户、昨日存档或补写真实模拟成交。**', '', result['scope'], '',
        f"新版大盘过滤：{'通过' if r['market_pass'] else '未通过，所有新买入禁止'}。可执行候选{len(r['strategy_candidates'])}个。",
        f"独立本金100,000元；持仓{len(state['positions'])}笔，已平仓{len(state['trades'])}笔，权益{state['equity']:.2f}元；无已平仓时胜率不适用。",'',
        '| 股票 | 新版分型 | 计划检查 | 回踩区间 | 今日开盘→末价 | 今日采样到区间次数 | 行情末端 |', '|---|---|---|---|---|---:|---|']
    for o in result['observations']:
        p=o['plan'];z=p['sniper'] if p else {};zone=f"{z['secondary_buy']:.2f}～{z['ideal_buy']:.2f}" if z else '—'
        ready='通过' if p and p['plan_ready'] else '；'.join(p['rejection_reasons']) if p else '未满足分型量价确认'
        prices=f"{o['first_open']:.2f}→{o['last_close']:.2f}" if o['first_open'] is not None else '缺失'
        lines.append(f"| {o['code']} {o['name']} | {p['strategy_label'] if p else '—'} | {ready} | {zone} | {prices} | {o['open_samples_in_plan']} | {o['coverage_end'] or '缺失'} |")
    lines+=['', '采样到区间次数已考虑不利一档滑点，但不是成交：仍须计划检查、大盘、现金仓位、新报价及涨跌停等条件全部通过。大盘未通过时即使到价也只观察。', '',
        '数据失败：'+json.dumps(result['failures'],ensure_ascii=False),'', '## 限制','']+['- '+x for x in result['limitations']]
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    fields=['code','name','strategy_rule','signal_close','first_open','last_close','from_open_pct','minute_bars','open_samples_in_plan','coverage_end','market_pass']
    with (out/'observations.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        writer.writerows({k:o[k] for k in fields} for o in result['observations'])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--signal-date',required=True);parser.add_argument('--trade-date',required=True);parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--sample',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.sample:
        with gzip.open(args.sample,'rt') as f:sample=json.load(f)
        if sample['signal_date']!=args.signal_date or sample['trade_date']!=args.trade_date:raise ValueError('离线样本日期不匹配')
    else:
        sample=collect(json.loads(args.snapshot.read_text()),args.signal_date,args.trade_date)
        with gzip.open(args.output/'input.json.gz','wt') as f:json.dump(sample,f,ensure_ascii=False,allow_nan=False)
    result=replay_day(sample,datetime.now(P.BJ).isoformat())
    write_report(args.output,result)
    print('回放完成',result['coverage_end'],'持仓',len(result['account']['positions']),'已平仓',len(result['account']['trades']),flush=True)
