import io
import json
import unittest
from unittest.mock import patch
import test_contract_routes as routes
from app import Handler


class FuturesPaperRoutes(unittest.TestCase):
    get=routes.ContractRouteTests.get

    def post(self,path,body,origin=None):
        h=object.__new__(Handler);h.path=path
        raw=json.dumps(body).encode();h.rfile=io.BytesIO(raw)
        h.headers={'Host':'127.0.0.1:8766','Content-Length':str(len(raw))}
        if origin:h.headers['Origin']=origin
        out=[];h._json=lambda status,data:out.append((status,data));h.do_POST()
        return out[0]

    def test_read_does_not_enable_or_fetch_and_page_exists(self):
        with patch('app.futures_paper_runner.snapshot',return_value={'currency':'CNY','enabled':False}) as snapshot,patch('app.futures_paper_runner.start') as start,patch('app.paper_runner.start') as crypto:
            status,body,_=self.get('/api/futures/paper')
            self.assertEqual(status,200);self.assertFalse(json.loads(body)['enabled'])
            snapshot.assert_called_once();start.assert_not_called();crypto.assert_not_called()
        for path in ['/futures-paper','/futures-paper/']:
            status,body,_=self.get(path);self.assertEqual(status,200);self.assertIn(b'futures-paper.js',body)

    def test_control_is_explicit_same_origin_and_separate(self):
        with patch('app.futures_paper_runner.account.set_enabled') as control,patch('app.futures_paper_runner.start') as start,patch('app.futures_paper_runner.snapshot',return_value={'enabled':True}),patch('app.paper_runner.account.set_enabled') as crypto:
            status,_=self.post('/api/futures/paper/control',{'enabled':True})
            self.assertEqual(status,200);control.assert_called_once_with(True);start.assert_called_once();crypto.assert_not_called()
            status,_=self.post('/api/futures/paper/control',{'enabled':False},'https://other.test')
            self.assertEqual(status,403);self.assertEqual(control.call_count,1)
        self.assertEqual(self.post('/api/futures/paper/control',{'enabled':'yes'})[0],400)
        self.assertEqual(self.post('/api/futures/paper/control',{'enabled':True,'reset':True})[0],400)

    def test_config_validates_and_does_not_start_runner(self):
        with patch('app.futures_paper_runner.account.configure') as save,patch('app.futures_paper_runner.snapshot',return_value={'enabled':False}),patch('app.futures_paper_runner.start') as start:
            status,_=self.post('/api/futures/paper/config',{'symbol':'RB2701'})
            self.assertEqual(status,200);save.assert_called_once_with({'symbol':'RB2701'});start.assert_not_called()
        self.assertEqual(self.post('/api/futures/paper/config',{'symbol':'RB0'})[0],400)


if __name__=='__main__':unittest.main()
