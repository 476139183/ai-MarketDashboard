import io,json,unittest
from unittest.mock import patch
from dsa_client import DSAClient,DSAError
from app import Handler

class NewsTests(unittest.TestCase):
    def client(self):return DSAClient('http://dsa.test',cookie='private-session')
    def call(self,method,path,body=None):
        handler=object.__new__(Handler);handler.path=path;raw=json.dumps(body).encode() if body is not None else b''
        handler.rfile=io.BytesIO(raw);handler.headers={'Host':'127.0.0.1:8766','Content-Length':str(len(raw))};out=[]
        handler._send=lambda status,data,mime:out.append((status,json.loads(data)));getattr(handler,'do_'+method)();return out[0]
    def test_news_queries_use_upstream_pool_not_model(self):
        with patch.object(DSAClient,'request',return_value={'items':[],'total':0,'page':1,'page_size':20}) as transport:
            self.client().news_items(market='crypto',query='BTC',days=7,page=2)
            path=transport.call_args.args[0]
            self.assertIn('/api/v1/intelligence/items?',path);self.assertIn('scope_value=crypto',path);self.assertIn('market=global',path)
            self.assertNotIn('analysis',path)
    def test_news_output_removes_unsafe_links_and_keeps_time_unknown(self):
        raw={'items':[{'id':1,'title':'<script>x</script>','url':'javascript:alert(1)','summary':'hello','scope_type':'market','scope_value':'crypto','market':'global','published_at':None,'fetched_at':'2026-11-08T10:00:00+08:00','api_key':'secret'}],'total':1,'page':1,'page_size':20}
        with patch.object(DSAClient,'request',return_value=raw):result=self.client().news_items()
        self.assertIsNone(result['items'][0]['url']);self.assertIsNone(result['items'][0]['published_at']);self.assertEqual(result['items'][0]['market'],'crypto');self.assertNotIn('secret',json.dumps(result))
    def test_bad_filters_never_reach_upstream(self):
        with patch.object(DSAClient,'request') as upstream:
            for kw in ({'market':'unknown'},{'days':0},{'page':0},{'query':'x'*101},{'symbol':'../../config'}):
                with self.subTest(kw=kw),self.assertRaises(ValueError):self.client().news_items(**kw)
            upstream.assert_not_called()
    def test_sources_expose_only_selected_safe_status_fields(self):
        raw={'items':[{'id':1,'name':'RSS','market':'us','enabled':True,'url':'https://feed.example/rss?api_key=secret','last_error':'secret-error','source_type':'rss'}],'total':1}
        with patch.object(DSAClient,'request',return_value=raw):out=self.client().news_sources()
        self.assertNotIn('secret',json.dumps(out));self.assertNotIn('url',out['items'][0])
    def test_refresh_status_is_nonblocking_and_duplicate_refresh_is_single_job(self):
        from news_service import NewsRefresh
        service=NewsRefresh()
        with patch('news_service.threading.Thread') as worker:
            self.assertTrue(service.start());self.assertFalse(service.start());worker.assert_called_once()
        self.assertEqual(service.snapshot()['status'],'running')
    def test_item_api_and_source_api_do_not_post(self):
        with patch('app.DSAClient.from_env',return_value=self.client()),patch.object(DSAClient,'request',return_value={'items':[],'total':0}) as request:
            self.assertEqual(self.call('GET','/api/news?market=cn')[0],200)
            self.assertEqual(self.call('GET','/api/news/sources')[0],200)
            self.assertTrue(all(not c.kwargs.get('method') and len(c.args)==1 for c in request.call_args_list))
    def test_repeated_get_never_bootstraps_sources(self):
        with patch('app.DSAClient.from_env',return_value=self.client()),patch.object(DSAClient,'request',return_value={'items':[],'total':0}) as request:
            self.call('GET','/api/news');self.call('GET','/api/news')
            self.assertTrue(all('/items?' in c.args[0] for c in request.call_args_list))
    def test_source_presets_are_idempotent_and_unknown_preset_is_rejected(self):
        with patch.object(DSAClient,'news_sources',return_value={'items':[{'name':'SEC官方新闻','id':5}]}),patch.object(DSAClient,'request') as transport:
            self.assertFalse(self.client().news_add_source('us')['created'])
            with self.assertRaises(ValueError):self.client().news_add_source('https://unsafe.test')
            transport.assert_not_called()
    def test_fake_ip_source_validation_has_clear_safe_error(self):
        with patch.object(DSAClient,'news_sources',return_value={'items':[]}),patch.object(DSAClient,'request',side_effect=DSAError('400',400,{'message':'source url must not target private or local network addresses','token':'secret'})):
            with self.assertRaisesRegex(DSAError,'Fake-IP') as caught:self.client().news_add_source('cn')
            self.assertNotIn('secret',str(caught.exception))
    def test_refresh_continues_after_failed_source_and_skips_disabled(self):
        from news_service import NewsRefresh
        service=NewsRefresh();client=self.client()
        def immediate(**kwargs):
            class Worker:
                def start(self):kwargs['target']()
            return Worker()
        with patch('news_service.threading.Thread',side_effect=immediate),patch('news_service.DSAClient.from_env',return_value=client),patch.object(client,'news_sources',return_value={'items':[{'id':1,'name':'bad','enabled':True},{'id':2,'name':'good','enabled':True},{'id':3,'name':'off','enabled':False}]}),patch.object(client,'request',side_effect=[DSAError('failed'),{'ok':True,'saved_count':3}]):
            service.start()
        result=service.snapshot();self.assertEqual(result['status'],'idle');self.assertEqual([r['ok'] for r in result['results']],[False,True]);self.assertEqual(result['results'][1]['saved_count'],3)
    def test_empty_sources_are_explained_instead_of_silent_success(self):
        from news_service import NewsRefresh
        service=NewsRefresh()
        def immediate(**kwargs):
            class Worker:
                def start(self):kwargs['target']()
            return Worker()
        with patch('news_service.threading.Thread',side_effect=immediate),patch('news_service.DSAClient.from_env',return_value=self.client()),patch.object(DSAClient,'news_sources',return_value={'items':[]}):
            service.start()
        self.assertIn('来源',service.snapshot()['error'])
    def test_cn_rss_preset_preserves_market_instead_of_crypto_scope(self):
        with patch.object(DSAClient,'news_sources',return_value={'items':[]}),patch.object(DSAClient,'request',return_value={'id':1,'name':'中新网财经','market':'cn'}) as transport:
            self.client().news_add_source('cn-rss')
            body=transport.call_args.args[1];self.assertEqual(body['market'],'cn');self.assertNotEqual(body.get('scope_value'),'crypto')
    def test_foreign_origin_cannot_start_news_refresh(self):
        handler=object.__new__(Handler);handler.path='/api/news/refresh';handler.headers={'Origin':'https://other.test','Host':'127.0.0.1:8766'};out=[];handler._json=lambda status,data:out.append(status)
        handler.do_POST();self.assertEqual(out,[403])

if __name__=='__main__':unittest.main()
