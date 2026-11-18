import unittest,io,json
from unittest.mock import patch
from app import Handler
import strength_radar as S

class StrengthRoutes(unittest.TestCase):
 def call(self,method,body=None,origin=None):
  h=object.__new__(Handler);h.path='/api/a-strength';h.headers={'Host':'127.0.0.1:8766'}
  if origin:h.headers['Origin']=origin
  raw=json.dumps(body).encode() if body is not None else b'';h.headers['Content-Length']=str(len(raw));h.rfile=io.BytesIO(raw);out=[];h._send=lambda code,data,mime:out.append((code,json.loads(data)))
  getattr(h,'do_'+method)();return out[0]
 def test_get_never_starts_collection_or_changes_account(self):
  with patch.object(S.SERVICE,'snapshot',return_value={'items':[]}),patch.object(S.SERVICE,'start') as start:
   self.assertEqual(self.call('GET')[0],200);start.assert_not_called()
 def test_post_is_explicit_and_cross_origin_is_blocked(self):
  with patch.object(S.SERVICE,'start') as start,patch.object(S.SERVICE,'snapshot',return_value={'status':'running'}):
   self.assertEqual(self.call('POST',{})[0],202);start.assert_called_once()
   self.assertEqual(self.call('POST',{},'https://foreign.test')[0],403);self.assertEqual(start.call_count,1)
 def test_scan_rejects_account_fields(self):
  with patch.object(S.SERVICE,'start') as start:
   self.assertEqual(self.call('POST',{'enabled':True})[0],400);start.assert_not_called()
 def test_running_job_is_deduplicated(self):
  service=S.StrengthService()
  with patch('strength_radar.threading.Thread') as worker:
   self.assertTrue(service.start());self.assertFalse(service.start());self.assertEqual(worker.call_count,1)
if __name__=='__main__':unittest.main()
