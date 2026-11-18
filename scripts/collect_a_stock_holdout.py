"""从事前存档池取独立股票后续样本；不按检验期间涨幅筛选。"""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import gzip
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'markets/us/dashboard'),str(ROOT/'markets/a'),str(ROOT)]
import a_share_paper as P
import radar
from replay import fetch


def collect(out,pool=None):
    universe=json.loads((ROOT/'markets/a/large-universe.json').read_text())
    if universe['generated_at'][:10]>='2026-11-04':raise ValueError('股票池不是检验开始前已存档')
    base=json.load(gzip.open(ROOT/'.cache/a-paper-research/sample-latest.json.gz','rt'))
    names={}
    for row in universe['included']+universe['excluded']:
        code=row['code'];name=row.get('name') or code
        try:P.stock_code(code)
        except ValueError:continue
        if code in base['daily'] or 'ST' in name.upper() or '退' in name:continue
        names[code]=name
    if pool is not None:
        frozen=json.loads(pool.read_text())
        if frozen.get('as_of')!='2026-11-03':raise ValueError('额外股票池缺少指定历史日期')
        names={r['code']:r['name'] for r in frozen['stocks']}
        if len(names)!=200 or set(names)&set(base['daily']):raise ValueError('额外股票池数量或隔离条件不符')
    out.mkdir(parents=True,exist_ok=True)
    if pool is None:(out/'pool.json').write_text(json.dumps(dict(source=universe['generated_at'],names=names),ensure_ascii=False,indent=2))
    daily={};minutes={};failures={}
    def one(code):
        path=out/(code+'.json')
        if path.exists():return json.loads(path.read_text())
        cached=ROOT/'.cache/a-opportunity-audit/2026-11-11_2026-11-12'/(code+'.json')
        if cached.exists():
            rows=json.loads(cached.read_text())['daily'];source='已缓存同源未复权日线'
            if not rows or rows[-1]['date']!='2026-11-11':rows,source=radar._current_history(dict(code=code,board='沪深A股'),'2026-11-11')
        else:rows,source=radar._current_history(dict(code=code,board='沪深A股'),'2026-11-11')
        _,_,bars=fetch(code)
        pack=dict(daily=rows,minutes=[r for r in bars if '2026-11-04'<=r['time'][:10]<='2026-11-11'],source=source)
        path.write_text(json.dumps(pack,ensure_ascii=False));return pack
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs={pool.submit(one,c):c for c in names}
        for job in as_completed(jobs):
            code=jobs[job]
            try:
                p=job.result();daily[code]=p['daily'];minutes[code]=p['minutes']
            except Exception as exc:failures[code]=str(exc)[:160]
            print('独立股票采集',len(daily)+len(failures),'/',len(names),flush=True)
    source_note=('名单来自'+universe['generated_at']+'的固定存档，排除原44只开发股票及当时名称显示ST/退市的股票。' if pool is None else
                 '名单来自BaoStock指定2026-11-03历史证券名单，排除原44及原124只，按代码SHA256固定取200只；未按检验期表现替换。')
    sample=dict(daily=daily,minutes=minutes,names=names,legacy_eligible=[r['code'] for r in universe['included']],benchmark=radar._hs300('2026-11-11'),start='2026-11-04',end='2026-11-11',
                limitations=[source_note,
                             '不按检验期收益增删证券；非全市场，上市、历史ST及公司行动仍未完全还原。',
                             '数据失败：'+json.dumps(failures,ensure_ascii=False)])
    with gzip.open(out/'sample.json.gz','wt') as file:json.dump(sample,file,ensure_ascii=False)
    print('完成',len(daily),'失败',failures,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--pool',type=Path)
    args=parser.parse_args();collect(args.output,args.pool)
