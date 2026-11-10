"""Isolated framework jobs; never imports heavy engines or touches paper accounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

REPO=Path(__file__).resolve().parents[3]
CACHE=REPO/'.cache/quant-research'
PYTHON=REPO/'.venv-quant'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
WORKER=Path(__file__).with_name('quant_worker.py')
SYMBOLS=('RB2701','M2701','AU2612','SC2701')


def normalize(params):
    if not isinstance(params,dict) or set(params)-{'engine','market','strategy','symbol','cost_scale','sizing'}:raise ValueError('研究参数包含未知字段')
    market=params.get('market','a_share');engine=params.get('engine','rqalpha')
    if market not in ('a_share','futures') or engine not in ('rqalpha','tqsdk'):raise ValueError('未知市场或引擎')
    strategy=params.get('strategy','setups_v2' if market=='a_share' else 'sma')
    if strategy not in (('legacy','setups_v2','expanded') if market=='a_share' else ('sma','donchian')):raise ValueError('策略不属于所选市场')
    symbol=params.get('symbol','RB2701')
    if symbol not in SYMBOLS:raise ValueError('请选择已归档的月份合约')
    if engine=='tqsdk' and market!='futures':raise ValueError('天勤首版仅接国内期货研究')
    scale=params.get('cost_scale',1)
    if type(scale) is not int or scale not in (1,2):raise ValueError('成本倍率仅支持1或2')
    sizing=params.get('sizing','fixed')
    if sizing not in ('fixed','risk') or market=='a_share' and sizing!='fixed':raise ValueError('仓位对照仅适用于期货')
    if engine=='tqsdk' and (sizing!='fixed' or scale!=1):raise ValueError('天勤入口暂仅支持固定手数与原始成本；风险仓位/压力对照请使用本地引擎')
    return dict(engine=engine,market=market,strategy=strategy,symbol=symbol,cost_scale=scale,sizing=sizing)


def engine_status():
    versions={}
    if PYTHON.is_file():
        try:
            r=subprocess.run([str(PYTHON),'-c',"import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in ['rqalpha','tqsdk']}))"],capture_output=True,text=True,timeout=5)
            if r.returncode==0:versions=json.loads(r.stdout)
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    return dict(rqalpha=dict(installed='rqalpha' in versions,version=versions.get('rqalpha'),local_data=True),
        tqsdk=dict(installed='tqsdk' in versions,version=versions.get('tqsdk'),credentials=bool(os.getenv('TQ_USER') and os.getenv('TQ_PASSWORD'))))


def metadata():
    return dict(engines=engine_status(),symbols=list(SYMBOLS),markets=['a_share','futures'],
        samples=dict(a_share=(REPO/'.cache/a-paper-research/sample-latest.json.gz').is_file(),futures=all((REPO/'docs/reports/futures-backtest-2026-11-08'/(s+'.json')).is_file() for s in SYMBOLS)),
        limitations=['研究使用独立子进程和虚拟资金，不改变前向账户。','RQAlpha仅限本机非商业研究；商业使用需核对授权。',
        '本地样本仍有选择偏差与盘口近似，框架运行成功不代表策略盈利。','天勤需要TQ_USER/TQ_PASSWORD与相应数据权限；无账号时仅完成接入入口。'])


class QuantJobs:
    def __init__(self):self.pool=ThreadPoolExecutor(max_workers=1);self.lock=threading.Lock();self.jobs={}
    def start(self,params):
        cfg=normalize(params);status=engine_status()[cfg['engine']]
        if not status['installed']:raise ValueError('独立研究环境未就绪，请先运行scripts/setup-quant.sh')
        if cfg['engine']=='tqsdk' and not status['credentials']:raise ValueError('天勤账号未配置；先使用RQAlpha本地样本回测')
        with self.lock:
            if any(j['status']=='running' for j in self.jobs.values()):raise ValueError('独立引擎研究正在运行，请稍候')
            if len(self.jobs)>=12:self.jobs.pop(next(iter(self.jobs)))
            key=uuid.uuid4().hex;self.jobs[key]=dict(id=key,status='running',config=cfg,error=None,progress='独立引擎启动中')
        self.pool.submit(self._run,key,cfg);return dict(id=key,status='running')
    def _run(self,key,cfg):
        try:
            folder=CACHE/key;folder.mkdir(parents=True,exist_ok=True)
            env=dict(os.environ,MPLCONFIGDIR=str(CACHE/'matplotlib'),PYTHONUNBUFFERED='1')
            args=[str(PYTHON),str(WORKER),'--output',str(folder/'result.json')]
            for k,v in cfg.items():args.extend(['--'+k.replace('_','-'),str(v)])
            p=subprocess.run(args,cwd=REPO,env=env,capture_output=True,text=True,timeout=600)
            if p.returncode:
                message=(p.stderr or p.stdout or '框架执行失败')[-1800:]
                for k in ('TQ_USER','TQ_PASSWORD'):
                    if os.getenv(k):message=message.replace(os.environ[k],'[已隐藏]')
                raise ValueError(message)
            result=json.loads((folder/'result.json').read_text())
            (CACHE/'latest.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
            with self.lock:self.jobs[key].update(status='done',result=result,progress='研究完成')
        except Exception as exc:
            with self.lock:self.jobs[key].update(status='error',error=str(exc)[:1800],progress='研究失败，未改变账户')
    def snapshot(self,key=None):
        with self.lock:
            if key:
                if not re.fullmatch('[a-f0-9]{32}',key) or key not in self.jobs:raise ValueError('研究任务不存在')
                return dict(self.jobs[key])
        file=CACHE/'latest.json'
        return dict(status='idle',result=json.loads(file.read_text()) if file.is_file() else None)


JOBS=QuantJobs()
