"""Fixed, auditable local-engine experiments; no parameter search or account migration."""
import json
import hashlib
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/reports/quant-engines-2026-11-10'


def main():
    OUT.mkdir(parents=True,exist_ok=True);cases=[];results=[]
    for strategy in ('legacy','setups_v2','expanded'):
        for scale in (1,2):cases.append(dict(engine='rqalpha',market='a_share',strategy=strategy,symbol='RB2701',cost_scale=scale,sizing='fixed'))
    for symbol in ('RB2701','M2701','AU2612','SC2701'):
        for strategy in ('sma','donchian'):
            for sizing,scale in [('fixed',1),('risk',1),('risk',2)]:cases.append(dict(engine='rqalpha',market='futures',strategy=strategy,symbol=symbol,cost_scale=scale,sizing=sizing))
    for index,cfg in enumerate(cases,1):
        name=f"{cfg['market']}-{cfg['symbol'] if cfg['market']=='futures' else '44stocks'}-{cfg['strategy']}-{cfg['sizing']}-c{cfg['cost_scale']}.json"
        args=[str(ROOT/'.venv-quant/bin/python'),str(ROOT/'markets/us/dashboard/quant_worker.py'),'--output',str(OUT/name)]
        for k,v in cfg.items():args.extend(['--'+k.replace('_','-'),str(v)])
        result=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=180)
        if result.returncode:raise RuntimeError(name+' '+result.stderr[-1800:])
        row=json.loads((OUT/name).read_text())
        results.append(dict(file=name,config=cfg,summary=row['summary'],data=row['data'],diagnostics=row['diagnostics'],open_positions=row['open_positions']))
        print(f"{index}/{len(cases)} {name} 收益{row['summary']['return_pct']:.4f}% 平仓{row['summary']['closed_trades']}",flush=True)
    files=['quant_worker.py','rqalpha_local.py','quant_research.py']
    code={f:hashlib.sha256((ROOT/'markets/us/dashboard'/f).read_bytes()).hexdigest() for f in files}
    (OUT/'summary.json').write_text(json.dumps(dict(engine='rqalpha',version='6.5.0',results=results,code_sha256=code,
        tqsdk_status='已安装3.10.4；用户确认暂不配置账号，尚未运行服务端回测'),ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
