import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import quant_research as Q


class QuantResearchTests(unittest.TestCase):
    def test_requests_do_not_accept_arbitrary_paths_or_code(self):
        for args in [dict(market='crypto'),dict(market='a_share',strategy='sma'),dict(market='futures',symbol='RB0'),dict(source='/etc/passwd'),dict(code='print(1)'),dict(cost_scale=100)]:
            with self.assertRaises(ValueError):Q.normalize(args)
        self.assertEqual(Q.normalize({'market':'futures','strategy':'sma','symbol':'RB2701'})['engine'],'rqalpha')

    def test_missing_tq_credentials_are_explicit_and_do_not_start_a_job(self):
        jobs=Q.QuantJobs()
        with patch.object(Q,'engine_status',return_value={'tqsdk':{'installed':True,'credentials':False}}),patch.object(jobs.pool,'submit') as submit:
            with self.assertRaisesRegex(ValueError,'账号'):jobs.start(dict(engine='tqsdk',market='futures',strategy='sma'))
            submit.assert_not_called()

    def test_metadata_and_result_reads_do_not_run_backtests(self):
        with TemporaryDirectory() as tmp,patch.object(Q,'CACHE',Path(tmp)),patch.object(Q.subprocess,'run') as execute:
            jobs=Q.QuantJobs();self.assertEqual(jobs.snapshot()['status'],'idle')
            execute.assert_not_called()
            (Path(tmp)/'latest.json').write_text(json.dumps({'engine':'rqalpha','market':'a_share'}))
            self.assertEqual(jobs.snapshot()['result']['engine'],'rqalpha')


if __name__=='__main__':unittest.main()
