import sys,json,time,gzip
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
sys.path[:0]=['markets/a','.']
from market_scan import fetch_universe,fetch_recent
from radar import _hs300
root=Path('.cache/a-month-entry-study-2026-11-20');cache=root/'daily';cache.mkdir(exist_ok=True)
benchmark=_hs300('2026-11-19')
assert benchmark[-1]['date']=='2026-11-19',benchmark[-1]
count,stocks,excluded=fetch_universe()
(root/'universe.json').write_text(json.dumps(dict(total=count,stocks=stocks,excluded=excluded),ensure_ascii=False))
(root/'benchmark.json').write_text(json.dumps(benchmark,ensure_ascii=False))
failures={};histories={}
def one(stock):
 file=cache/(stock['code']+'.json')
 if file.exists():return json.loads(file.read_text())
 rows=fetch_recent(stock,'2026-11-19',count=120)
 file.write_text(json.dumps(rows));return rows
for attempt in range(2):
 pending=[s for s in stocks if s['code'] not in histories]
 if not pending:break
 with ThreadPoolExecutor(max_workers=8 if attempt==0 else 3) as pool:
  jobs={pool.submit(one,s):s for s in pending}
  for i,future in enumerate(as_completed(jobs),1):
   stock=jobs[future]
   try:histories[stock['code']]=future.result();failures.pop(stock['code'],None)
   except Exception as exc:failures[stock['code']]=str(exc)[:160]
   if i%500==0 or i==len(jobs):print('collect',attempt,i,'/',len(jobs),'ready',len(histories),'failures',len(failures),flush=True)
(root/'failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2))
with gzip.open(root/'sample.json.gz','wt') as f:json.dump(dict(stocks=stocks,histories=histories,benchmark=benchmark,start='2026-10-20',end='2026-11-19',failures=failures,total=count,excluded=excluded),f,ensure_ascii=False)
print('done',len(histories),'of',len(stocks),flush=True)
