import gzip,json,sys,hashlib,statistics
from pathlib import Path
from collections import Counter,defaultdict
sys.path[:0]=['markets/a','.']
import importlib.util
spec=importlib.util.spec_from_file_location('frozen_strength_v2',Path(__file__).with_name('baseline_strength_v2.py'))
G=importlib.util.module_from_spec(spec);spec.loader.exec_module(G)
import stock_setups as S
root=Path('.cache/a-month-entry-study-2026-11-20')
x=json.load(gzip.open(root/'sample.json.gz','rt'))
start,end=x['start'],x['end'];calendar=[b['date'] for b in x['benchmark'] if b['date']<=end];ci={d:i for i,d in enumerate(calendar)}
benchmark={b['date']:b for b in x['benchmark']}
first=ci[start];days=calendar[first:];signal_days=set(calendar[first-1:-1]);market={d:benchmark[d]['close']>sum(benchmark[c]['close'] for c in calendar[ci[d]-19:ci[d]+1])/20 for d in signal_days}
records=[];all_events=[];stages=Counter();data_issues=Counter();baseline_by_date=defaultdict(list)

def valid_price(b):
 try:
  import math
  o,h,l,c,v=[float(b[k]) for k in ('open','high','low','close','volume')]
  return all(math.isfinite(v) for v in (o,h,l,c,v)) and 0<l<=min(o,c)<=max(o,c)<=h and v>=0
 except (KeyError,ValueError,TypeError):return False

def entry_at(stock,rows,i):
 if i+1>=len(rows):return None
 b,n=rows[i],rows[i+1]
 if ci.get(b['date']) is None or ci[b['date']]+1>=len(calendar) or n['date']!=calendar[ci[b['date']]+1]:return None
 limit=.3 if stock['board']=='北交所' else .2 if stock['board'] in ('创业板','科创板') else .1
 if n['volume']<=0 or n['high']==n['low'] or n['open']>=b['close']*(1+limit*.95):return None
 price=round(n['open']+.01,2)
 if price>=b['close']*(1+limit):return None
 j=i+5
 forward=None
 if j<len(rows) and ci.get(rows[j]['date'])==ci[n['date']]+4:
  forward=((rows[j]['close']-.01)*.9985/(price*1.0015)-1)*100
 return dict(signal_date=b['date'],entry_date=n['date'],entry_price=price,forward5_net_proxy_pct=forward)

for no,stock in enumerate(x['stocks'],1):
 rows=x['histories'].get(stock['code'],[])
 if not rows or any(not valid_price(r) for r in rows):data_issues['缺失或非法OHLCV']+=1;continue
 if any(a['date']>=b['date'] for a,b in zip(rows,rows[1:])):data_issues['日线时序异常']+=1;continue
 bydate={r['date']:i for i,r in enumerate(rows)}
 if end not in bydate or calendar[first-1] not in bydate:data_issues['月初基准或期末收盘缺失']+=1;continue
 base=rows[bydate[calendar[first-1]]]['close'];final=rows[bydate[end]]['close']
 month=(final/base-1)*100;events=[];plans=[];plan_market=[];plan_fills=[];reasons=Counter();plan_reasons=Counter()
 pattern_first={}
 for i,b in enumerate(rows):
  d=b['date']
  if d not in signal_days:continue
  if i<59 or [r['date'] for r in rows[i-59:i+1]]!=calendar[ci[d]-59:ci[d]+1]:
   reasons['不足连续60根市场日线']+=1;continue
  if any(abs(rows[j]['close']/rows[j-1]['close']-1)>.35 for j in range(max(1,i-24),i+1)):
   reasons['近期价格跳变需核验']+=1;continue
  result=G.evaluate(stock,rows[:i+1],d);stages[result['stage']]+=1;reasons[result['reason'] if result['stage']!='matched' else 'matched']+=1
  entry=entry_at(stock,rows,i)
  if entry and entry['forward5_net_proxy_pct'] is not None and result['stage'] not in ('data','security','liquidity','suspended'):baseline_by_date[entry['entry_date']].append(entry['forward5_net_proxy_pct'])
  if result['patterns']:
   if entry:
    event=dict(**entry,patterns=result['patterns'],metrics=result['metrics'],remaining_month_pct=(final/entry['entry_price']-1)*100,early70=entry['entry_price']<=base+.3*(final-base))
    events.append(event)
    for pattern in result['patterns']:pattern_first.setdefault(pattern,event)
   else:reasons['研究信号次日开盘不可执行代理']+=1
  if stock['board']=='北交所':continue
  setups=S.evaluate_setups(rows[:i+1]);chosen=S.selected_setup(setups)
  if not chosen:continue
  plan=chosen['plan'];plan_reasons.update(plan['rejection_reasons'])
  if not plan['plan_ready']:continue
  plans.append(d)
  if not market[d]:continue
  plan_market.append(d)
  if entry and plan['sniper']['secondary_buy']<=entry['entry_price']<=plan['sniper']['ideal_buy']:
   plan_fills.append(dict(**entry,rule=chosen['rule'],remaining_month_pct=(final/entry['entry_price']-1)*100))
 window=[(i,b) for i,b in enumerate(rows) if start<=b['date']<=end]
 above=sum(b['close']>=sum(r['close'] for r in rows[i-19:i+1])/20 for i,b in window if i>=19)
 e=bydate[end];ma20=sum(r['close'] for r in rows[e-19:e+1])/20;prevma=sum(r['close'] for r in rows[e-24:e-4])/20
 occupancy=above/len(window) if window else 0
 trend=month>=10 and occupancy>=.6 and ma20>prevma
 record=dict(**stock,is_trend=trend,above_ma20_ratio=occupancy,month_return_pct=month,base_price=base,end_price=final,first_signal=events[0] if events else None,pattern_first=pattern_first,signal_count=len(events),plan_ready_days=len(plans),market_allowed_plan_days=len(plan_market),plan_fill=plan_fills[0] if plan_fills else None,reasons=dict(reasons),plan_reasons=dict(plan_reasons))
 records.append(record)
 all_events.extend(dict(code=stock['code'],**e) for e in events)
 if no%750==0:print('analyze',no,'/',len(x['stocks']),flush=True)

def ratio(n,total):return round(n/total*100,3) if total else None

def cover(group):
 n=len(group);signals=[r for r in group if r['first_signal']]
 return dict(stocks=n,any_signal=len(signals),coverage_pct=ratio(len(signals),n),early70=sum(bool(r['first_signal'] and r['first_signal']['early70']) for r in group),early70_pct=ratio(sum(bool(r['first_signal'] and r['first_signal']['early70']) for r in group),n),remaining5=sum(bool(r['first_signal'] and r['first_signal']['remaining_month_pct']>=5) for r in group),plan_ready=sum(r['plan_ready_days']>0 for r in group),market_allowed_plan=sum(r['market_allowed_plan_days']>0 for r in group),plan_next_open=sum(bool(r['plan_fill']) for r in group))

def quality(events):
 complete=[e for e in events if e['forward5_net_proxy_pct'] is not None];values=[e['forward5_net_proxy_pct'] for e in complete]
 b=[]
 for e in complete:
  vals=baseline_by_date[e['entry_date']]
  if vals:b.append(dict(avg=statistics.mean(vals),positive=sum(v>0 for v in vals)/len(vals)))
 return dict(complete=len(values),positive_pct=ratio(sum(v>0 for v in values),len(values)),gain5_pct=ratio(sum(v>=5 for v in values),len(values)),mean_pct=round(statistics.mean(values),4) if values else None,median_pct=round(statistics.median(values),4) if values else None,non_positive_pct=ratio(sum(v<=0 for v in values),len(values)),same_date_universe_mean_pct=round(statistics.mean(e['avg'] for e in b),4) if b else None,same_date_universe_positive_pct=round(statistics.mean(e['positive'] for e in b)*100,3) if b else None)

trends=[r for r in records if r['is_trend']]
winners=[r for r in records if r['month_return_pct']>=15]
strong=[r for r in records if r['month_return_pct']>=30]
missed=[r for r in winners if not r['first_signal']];missed_reasons=Counter()
for r in missed:
 for reason in r['reasons']:missed_reasons[reason]+=1
patterns={}
for pattern in G.LABELS:
 pe=[r['pattern_first'][pattern] for r in records if pattern in r['pattern_first']]
 caught=[r for r in winners if pattern in r['pattern_first']]
 patterns[pattern]=dict(label=G.LABELS[pattern],winner_coverage=len(caught),winner_coverage_pct=ratio(len(caught),len(winners)),early70_winners=sum(r['pattern_first'][pattern]['early70'] for r in caught),quality=quality(pe))
first_events=[r['first_signal'] for r in records if r['first_signal']]
mid=days[len(days)//2-1]
period_quality={'first_half':quality([e for e in first_events if e['entry_date']<=mid]),'second_half':quality([e for e in first_events if e['entry_date']>mid])}
summary=dict(period_split=mid,period_quality=period_quality,start=start,end=end,sessions=len(days),source_universe=x['total'],risk_excluded=len(x['excluded']),eligible=len(x['stocks']),evaluated=len(records),network_failures=len(x['failures']),data_issues=dict(data_issues),sample_sha256=hashlib.sha256((root/'sample.json.gz').read_bytes()).hexdigest(),rule_version=G.VERSION,plan_revision=S.PLAN_REVISION,trends=cover(trends),trend_stocks=sorted(trends,key=lambda r:-r['month_return_pct']),winners15=cover(winners),winners30=cover(strong),by_board={board:cover([r for r in winners if r['board']==board]) for board in sorted({r['board'] for r in winners})},quality_first_signal=quality([r['first_signal'] for r in records if r['first_signal']]),quality_all_signals=quality(all_events),patterns=patterns,missed_reason_stock_counts=dict(missed_reasons.most_common()),stages=dict(stages),winners=sorted(winners,key=lambda r:-r['month_return_pct']),top_other_examples=sorted(records,key=lambda r:-r['month_return_pct'])[:30])
(root/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
(root/'all-stocks.json').write_text(json.dumps(records,ensure_ascii=False,allow_nan=False))
print(json.dumps({k:v for k,v in summary.items() if k not in ('winners','top_other_examples','trend_stocks')},ensure_ascii=False,indent=2),flush=True)
