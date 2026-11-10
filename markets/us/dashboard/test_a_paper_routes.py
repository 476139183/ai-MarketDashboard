import io,json,unittest
from unittest.mock import patch
from app import Handler
import test_contract_routes as routes

class AShareRoutes(unittest.TestCase):
    get=routes.ContractRouteTests.get
    def test_radar_exposes_code_version_without_relabelling_archived_snapshot(self):
        import app
        old=dict(signal_date='2026-11-09',strategy_count=0)
        with patch('app.a_radar.load_cached',return_value=old),patch('app.a_radar.scan') as scan:
            value=app._radar_payload()
        self.assertEqual(value['configured_strategy_version'],'a-setups-v2')
        self.assertNotIn('strategy_version',value)
        self.assertEqual(old,dict(signal_date='2026-11-09',strategy_count=0))
        scan.assert_not_called()
    def post(self,path,body,origin=None):
        h=object.__new__(Handler);h.path=path;raw=json.dumps(body).encode();h.rfile=io.BytesIO(raw)
        h.headers={'Host':'127.0.0.1:8766','Content-Length':str(len(raw))}
        if origin:h.headers['Origin']=origin
        out=[];h._json=lambda status,data:out.append((status,data));h.do_POST();return out[0]
    def test_read_does_not_enable_or_scan(self):
        with patch('app.a_paper_runner.snapshot',return_value={'market':'a_share','enabled':False}),patch('app.a_paper_runner.start') as start:
            status,body,_=self.get('/api/a-paper');self.assertEqual(status,200);self.assertFalse(json.loads(body)['enabled']);start.assert_not_called()
        status,body,_=self.get('/a-paper');self.assertEqual(status,200);self.assertIn(b'a-paper.js',body)
    def test_explicit_control_isolated_and_same_origin(self):
        with patch('app.a_paper_runner.account.set_enabled') as enabled,patch('app.a_paper_runner.start'),patch('app.a_paper_runner.snapshot',return_value={}),patch('app.futures_paper_runner.account.set_enabled') as futures:
            self.assertEqual(self.post('/api/a-paper/control',{'enabled':True})[0],200);enabled.assert_called_once_with(True);futures.assert_not_called()
            self.assertEqual(self.post('/api/a-paper/control',{'enabled':False},'https://other.test')[0],403)
        self.assertEqual(self.post('/api/a-paper/control',{'enabled':'yes'})[0],400)
    def test_config_validation_and_no_automatic_enable(self):
        self.assertEqual(self.post('/api/a-paper/config',{'capital':1})[0],400)
        with patch('app.a_paper_runner.account.configure') as config,patch('app.a_paper_runner.snapshot',return_value={}),patch('app.a_paper_runner.start') as start:
            self.assertEqual(self.post('/api/a-paper/config',{'max_positions':3})[0],200);config.assert_called_once();start.assert_not_called()

    def test_research_is_explicit_and_receives_config_without_enabling(self):
        with patch('app.a_research_jobs.snapshot',return_value={'status':'idle'}) as read,patch('app.a_research_jobs.start',return_value={'id':'job','status':'running'}) as start,patch('app.a_paper_runner.account.snapshot',return_value={'config':{'max_positions':3}}),patch('app.a_paper_runner.account.set_enabled') as enabled:
            self.assertEqual(self.get('/api/a-paper/research')[0],200);start.assert_not_called()
            self.assertEqual(self.post('/api/a-paper/research',{'months':3,'sample_size':20})[0],202)
            start.assert_called_once_with({'months':3,'sample_size':20},{'max_positions':3});enabled.assert_not_called()
