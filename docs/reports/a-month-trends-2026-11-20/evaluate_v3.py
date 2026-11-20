import json,gzip,sys,statistics
from pathlib import Path
from collections import defaultdict,Counter
sys.path[:0]=['markets/a','.']
import strength_radar as G
root=Path('.cache/a-month-entry-study-2026-11-20');x=json.load(gzip.open(root/'sample.json.gz','rt'))
old={r['code']:r for r in json.loads((root/'all-stocks.json').read_text())}
cal=[b['date'] for b in x['benchmark'] if b['date']<=x['end']];ci={d:i for i,d in enumerate(cal)};start=ci[x['start']];allowed=set(cal[start-1:-1]);baseline=defaultdict(list)
records=[];confirmed_all=[]
for no,stock in enumerate(x['stocks'],1):
 if stock['code'] not in old:continue
 rows=x['histories'][stock['code']];base=old[stock['code']]['base_price'];final=old[stock['code']]['end_price']
 structures=[];confirmed=[];filled=[];observed=Counter()
 for i,b in enumerate(rows):
  d=b['date']
  if d not in allowed or i<59 or [r['date'] for r in rows[i-59:i+1]]!=cal[ci[d]-59:ci[d]+1]:continue
  out=G.evaluate(stock,rows[:i+1],d);ctx=out.get('trend_context') or {}
  if ctx.get('active'):structures.append(dict(date=d,state=ctx['state']))
  observed[ctx.get('state',out['stage'])]+=1
  if ctx.get('state')=='entry_confirmed':confirmed.append(d)
  if i+1>=len(rows) or rows[i+1]['date']!=cal[ci[d]+1]:continue
  n=rows[i+1];limit=.3 if stock['board']=='北交所' else .2 if stock['board'] in ('创业板','科创板') else .1
  if n['volume']<=0 or n['high']==n['low'] or n['open']>=b['close']*(1+limit*.95):continue
  entry=round(n['open']+.01,2)
  if entry>=b['close']*(1+limit):continue
  fwd=((rows[i+5]['close']-.01)*.9985/(entry*1.0015)-1)*100 if i+5<len(rows) and ci.get(rows[i+5]['date'])==ci[n['date']]+4 else None
  if fwd is not None and out['stage'] not in ('data','security','liquidity','suspended'):baseline[n['date']].append(fwd)
  if ctx.get('state')!='entry_confirmed' or not ctx['lower']<=entry<=ctx['upper']:continue
  event=dict(return20=out['metrics']['return20'],turnover=out['metrics']['avg_turnover20'],code=stock['code'],name=stock['name'],signal_date=d,entry_date=n['date'],entry_price=entry,lower=ctx['lower'],upper=ctx['upper'],stop=ctx['stop'],rule=ctx['rule'],forward5_net_proxy_pct=fwd,remaining_month_pct=(final/entry-1)*100,early70=entry<=base+.3*(final-base))
  filled.append(event);confirmed_all.append(event)
 r=dict(code=stock['code'],name=stock['name'],board=stock['board'],is_trend=old[stock['code']]['is_trend'],month_return_pct=old[stock['code']]['month_return_pct'],first_structure=structures[0] if structures else None,confirmed_days=len(confirmed),first_entry=filled[0] if filled else None,states=dict(observed))
 records.append(r)
 if no%1000==0:print('v3',no,flush=True)
def quality(events):
 es=[e for e in events if e and e['forward5_net_proxy_pct'] is not None];v=[e['forward5_net_proxy_pct'] for e in es]
 paired=[baseline[e['entry_date']] for e in es if baseline[e['entry_date']]]
 return dict(complete=len(v),positive_pct=sum(a>0 for a in v)/len(v)*100 if v else None,mean_pct=statistics.mean(v) if v else None,median_pct=statistics.median(v) if v else None,gain5_pct=sum(a>=5 for a in v)/len(v)*100 if v else None,baseline_positive_pct=statistics.mean(sum(a>0 for a in vs)/len(vs) for vs in paired)*100 if paired else None,baseline_mean_pct=statistics.mean(statistics.mean(vs) for vs in paired) if paired else None)
def cover(group):
 n=len(group)
 return dict(stocks=n,structure_seen=sum(bool(r['first_structure']) for r in group),confirmed=sum(r['confirmed_days']>0 for r in group),next_open_in_zone=sum(bool(r['first_entry']) for r in group),early70=sum(bool(r['first_entry'] and r['first_entry']['early70']) for r in group),remaining5=sum(bool(r['first_entry'] and r['first_entry']['remaining_month_pct']>=5) for r in group))
firsts=[r['first_entry'] for r in records if r['first_entry']];mid='2026-11-03'
summary=dict(events=confirmed_all,version=G.VERSION,trend_version=G.TREND_VERSION,trends=cover([r for r in records if r['is_trend']]),winners15=cover([r for r in records if r['month_return_pct']>=15]),quality_first=quality(firsts),quality_all=quality(confirmed_all),quality_early=quality([e for e in firsts if e['entry_date']<=mid]),quality_late=quality([e for e in firsts if e['entry_date']>mid]),records=records)
(root/'v3-results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
print(json.dumps({k:v for k,v in summary.items() if k not in ('records','events')},ensure_ascii=False,indent=2))
