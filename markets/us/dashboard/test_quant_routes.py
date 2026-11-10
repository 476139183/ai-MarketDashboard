import io,json,unittest
from unittest.mock import patch
from app import Handler
from test_contract_routes import ContractRouteTests


class QuantRoutes(unittest.TestCase):
    get=ContractRouteTests.get
    def post(self,body,origin=None):
        h=object.__new__(Handler);h.path='/api/quant-research';raw=json.dumps(body).encode();h.rfile=io.BytesIO(raw)
        h.headers={'Host':'127.0.0.1:8766','Content-Length':str(len(raw))}
        if origin:h.headers['Origin']=origin
        out=[];h._json=lambda status,data:out.append((status,data));h.do_POST();return out[0]
    def test_page_and_get_are_read_only(self):
        with patch('app.quant_jobs.snapshot',return_value={'status':'idle'}),patch('app.quant_metadata',return_value={'engines':{}}),patch('app.quant_jobs.start') as start,patch('app.a_paper_runner.account.set_enabled') as enable:
            status,body,_=self.get('/api/quant-research');self.assertEqual(status,200);self.assertIn('metadata',json.loads(body));start.assert_not_called();enable.assert_not_called()
            self.assertEqual(self.get('/quant-research')[0],200)
    def test_explicit_post_does_not_control_forward_account(self):
        with patch('app.quant_jobs.start',return_value={'id':'job','status':'running'}) as start,patch('app.futures_paper_runner.account.set_enabled') as enable:
            self.assertEqual(self.post({'market':'futures'})[0],202);start.assert_called_once_with({'market':'futures'});enable.assert_not_called()
            self.assertEqual(self.post({},'https://example.org')[0],403)


if __name__=='__main__':unittest.main()
