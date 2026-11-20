"""Exploratory daily-bar portfolio; conservative censoring, not live fills."""
import gzip,json,math
from pathlib import Path
from collections import defaultdict,Counter
root=Path('.cache/a-month-entry-study-2026-11-20');sample=json.load(gzip.open(root/'sample.json.gz','rt'))
data=json.loads((root/'v3-results.json').read_text());meta={s['code']:s for s in sample['stocks']}
bars={code:{r['date']:r for r in rs} for code,rs in sample['histories'].items()}
cal=[b['date'] for b in sample['benchmark']];ci={d:i for i,d in enumerate(cal)}

def fee(value,buy,stress=1):return round(max(5*stress,value*.0003*stress)+value*.00001*stress+(0 if buy else value*.0005*stress),2)
def limit(code):return .3 if meta[code]['board']=='北交所' else .2 if meta[code]['board'] in ('创业板','科创板') else .1

def run(stress=1,eligible_codes=None,exit_ma=10,stop_mode="structure",confirm_bars=1):
 orders=defaultdict(list)
 for e in data['events']:
  if eligible_codes is None or e['code'] in eligible_codes:orders[e['entry_date']].append(e)
 cash=100000.;positions={};trades=[];curve=[];rejected=Counter();maxmargin=0
 for d in [d for d in cal if sample['start']<=d<=sample['end']]:
  previous=cal[ci[d]-1]
  for code,p in list(positions.items()):
   b=bars[code].get(d);prev=bars[code].get(previous)
   if not b or not prev or b['volume']<=0:continue
   if d<=p['entry_date']:continue
   if b['low']==b['high'] or b['open']<=prev['close']*(1-limit(code)*.95):
    rejected['无法核验卖出成交']+=1;continue
   reason=None;raw=None
   if b['open']<=p['stop']:raw=b['open'];reason='gap_stop'
   elif p.get('ma_exit'):raw=b['open'];reason='ma'+str(exit_ma)+'_exit_next_open'
   elif b['low']<=p['stop']:raw=p['stop'];reason='daily_stop_proxy'
   if reason:
    price=round(raw-.01*stress,2);cost=fee(price*p['qty'],False,stress);net=round((price-p['entry_price'])*p['qty']-p['entry_fee']-cost,2)
    cash=round(cash+price*p['qty']-cost,2)
    trades.append(dict(**p,exit_date=d,exit_price=price,exit_fee=cost,net_pnl=net,reason=reason));positions.pop(code)
  ranked=sorted(orders[d],key=lambda e:(-e['return20'],-e['turnover'],e['code']))
  for e in ranked:
   code=e['code']
   if code in positions:continue
   if len(positions)>=3:rejected['最多3仓']+=1;continue
   if code not in meta or meta[code]['board']=='北交所':rejected['北交所未接入模拟']+=1;continue
   price=round(e['entry_price']+.01*(stress-1),2)
   planned_stop=e['stop']
   if stop_mode=='atr2':
    prefix=[r for r in sample['histories'][code] if r['date']<=e['signal_date']]
    atr=sum(max(y['high']-y['low'],abs(y['high']-a['close']),abs(y['low']-a['close'])) for a,y in zip(prefix[-15:-1],prefix[-14:]))/14
    planned_stop=round(prefix[-1]['close']-2*atr,2)
   if not 0<planned_stop<e['lower']:rejected['替代止损结构无效']+=1;continue
   if not e['lower']<=price<=e['upper']:rejected['成本后超区间']+=1;continue
   b=bars[code][d]
   equity=cash+sum(p['qty']*p['last_price'] for p in positions.values())
   budget=min(10000.,cash);risk=equity*.0025
   qty=min(int(budget/price/100)*100,int(risk/max(price-(planned_stop-.01*stress),.01)/100)*100)
   minimum=200 if meta[code]['board']=='科创板' else 100
   while qty>=minimum:
    cost=fee(price*qty,True,stress);loss=(price-planned_stop+.01*stress)*qty+cost+fee((planned_stop-.01*stress)*qty,False,stress)
    if price*qty+cost<=budget and loss<=risk:break
    qty-=100
   if qty<minimum:rejected['预算或风险不足最小买入股数']+=1;continue
   cash=round(cash-price*qty-cost,2)
   positions[code]=dict(code=code,name=e['name'],entry_date=d,signal_date=e['signal_date'],entry_price=price,entry_fee=cost,qty=qty,stop=planned_stop,last_price=price,ma_exit=False,rule=e['rule'])
  # Only after all open orders, today's close becomes available for future exits/valuation.
  for code,p in positions.items():
   b=bars[code].get(d)
   if not b:continue
   p['last_price']=b['close'];prior=[r['close'] for r in sample['histories'][code] if r['date']<=d]
   p['ma_exit']=len(prior)>=exit_ma+confirm_bars-1 and all(prior[len(prior)-1-j]<sum(prior[len(prior)-exit_ma-j:len(prior)-j])/exit_ma for j in range(confirm_bars))
  equity=cash+sum(p['qty']*p['last_price'] for p in positions.values());curve.append(dict(date=d,equity=equity,cash=cash,positions=len(positions)))
 peak=100000.;dd=0
 for row in curve:peak=max(peak,row['equity']);dd=max(dd,(peak-row['equity'])/peak*100)
 return dict(exit_ma=exit_ma,stop_mode=stop_mode,confirm_bars=confirm_bars,model='daily_bar_exploratory_v1',stress=stress,return_pct=(curve[-1]['equity']/100000-1)*100,max_close_drawdown_pct=dd,closed_trades=len(trades),winning_trades=sum(t['net_pnl']>0 for t in trades),open_positions=list(positions.values()),trades=trades,curve=curve,rejected=dict(rejected))
result=dict(baseline=run(),double_cost=run(2))
hindsight_codes={r['code'] for r in data['records'] if r['is_trend']}
result['hindsight_only']=run(eligible_codes=hindsight_codes)
result['hindsight_only']['warning']='仅用于诊断：使用月末已知151只趋势股白名单，包含未来信息，禁止作为策略验证或发布依据。'
base=result['baseline'];equity=100000*(1+base['return_pct']/100);extra=0.
for t in base['trades']:
 extra+=.02*t['qty']+fee((t['entry_price']+.01)*t['qty'],True,2)+fee((t['exit_price']-.01)*t['qty'],False,2)-t['entry_fee']-t['exit_fee']
for p in base['open_positions']:
 extra+=.01*p['qty']+fee((p['entry_price']+.01)*p['qty'],True,2)-p['entry_fee']
result['fixed_fill_double_cost']=dict(return_pct=(equity-extra)/100000*100-100,extra_cost=extra)
(root/'portfolio.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
for k,r in [('baseline',result['baseline']),('double_cost',result['double_cost'])]:print(k,json.dumps({f:r[f] for f in ['return_pct','max_close_drawdown_pct','closed_trades','winning_trades','rejected']},ensure_ascii=False))

variants=[('ma10_structure',10,'structure',1),('ma20_structure',20,'structure',1),('ma10_atr2',10,'atr2',1),('ma20_atr2',20,'atr2',1),('ma20_two_closes_atr2',20,'atr2',2)]
audit={'full_market':{},'hindsight_151_only':{}}
for label,ma,stop,n in variants:
 for group,codes in [('full_market',None),('hindsight_151_only',hindsight_codes)]:
  row=run(eligible_codes=codes,exit_ma=ma,stop_mode=stop,confirm_bars=n);audit[group][label]=row
  print(group,label,'return',round(row['return_pct'],4),'drawdown',round(row['max_close_drawdown_pct'],4),'closed',row['closed_trades'],'wins',row['winning_trades'],'open',len(row['open_positions']))
assert abs(audit['full_market']['ma10_structure']['return_pct']-result['baseline']['return_pct'])<1e-9
(root/'exit-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
