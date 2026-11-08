"""Explicit, non-blocking news-pool refresh. No model or notification calls."""
from datetime import datetime,timezone
import threading
from dsa_client import DSAClient,DSAError

class NewsRefresh:
    def __init__(self):
        self.lock=threading.Lock();self.state={'status':'idle','started_at':None,'finished_at':None,'results':[],'error':None}
    def snapshot(self):
        with self.lock:return {**self.state,'results':[dict(r) for r in self.state['results']]}
    def start(self):
        with self.lock:
            if self.state['status']=='running':return False
            self.state.update(status='running',started_at=datetime.now(timezone.utc).isoformat(),finished_at=None,results=[],error=None)
        def job():
            try:
                client=DSAClient.from_env();client.timeout=45
                sources=client.news_sources()['items']
                for source in sources:
                    if not source.get('enabled'):continue
                    try:
                        result=client.request('/api/v1/intelligence/sources/'+str(int(source['id']))+'/fetch',{})
                        item={'name':source['name'],'ok':result.get('ok') is True,'fetched_count':result.get('fetched_count',0),'saved_count':result.get('saved_count',0)}
                        if not item['ok']:item['error']='来源采集失败，稍后重试或检查DSA源状态'
                    except (DSAError,ValueError,TypeError):item={'name':source.get('name','资讯源'),'ok':False,'error':'来源采集失败，稍后重试或检查DSA源状态'}
                    with self.lock:self.state['results'].append(item)
            except DSAError as exc:
                with self.lock:self.state['error']=str(exc)
            except Exception:
                with self.lock:self.state['error']='资讯采集任务失败，请检查本地DSA服务'
            finally:
                with self.lock:self.state.update(status='idle',finished_at=datetime.now(timezone.utc).isoformat())
        threading.Thread(target=job,daemon=True).start();return True

REFRESH=NewsRefresh()
