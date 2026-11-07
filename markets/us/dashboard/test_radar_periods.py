import unittest
from unittest.mock import patch

import contract_radar as radar
from radar_periods import period_minutes, aggregate_rows


class RadarPeriodTests(unittest.TestCase):
    def test_supported_periods_and_reject_invalid(self):
        for value in (1, 5, 10, 15, 30, 60, 240):
            self.assertEqual(period_minutes(str(value)), value)
        for value in ('0', '-1', '2', 'abc', '15.5', True):
            with self.assertRaises(ValueError):
                period_minutes(value)

    def test_ten_minute_aggregation_requires_both_closed_children(self):
        rows = [[0,'10','12','9','11','3','33'],
                [300000,'11','14','10','13','4','52'],
                [600000,'13','15','12','14','5','70']]
        result = aggregate_rows(rows, source_minutes=5, target_minutes=10, now=900001)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], [0,10,14,9,13,7,85])
        self.assertEqual(aggregate_rows(rows[1:], source_minutes=5,target_minutes=10,now=1200001), [])

    def test_one_minute_signal_uses_selected_period_not_four_hours(self):
        now=1800000000000//60000*60000+1000
        boundary=now//60000*60000
        rows=[[boundary-(100-i)*60000,100+i,102+i,99+i,101+i,10,1000] for i in range(100)]
        quote={'lastPr':'201','ts':now}
        item=radar.analyze_contract('BTCUSDT','比特币','crypto',quote,rows,now,minutes=1)
        self.assertEqual(item['direction'],'bullish')
        self.assertEqual(item['period_minutes'],1)
        self.assertEqual(item['candle_close_at'],boundary)
        self.assertIn('1 分钟',item['reasons'][0])

    def test_services_do_not_share_candle_cache_between_periods(self):
        requests=[]
        def fetch(path, params):
            requests.append(params)
            return []
        one=radar.ContractRadar(fetch,minutes=1)
        ten=radar.ContractRadar(fetch,minutes=10)
        one._candles('BTCUSDT',1800000000000)
        ten._candles('BTCUSDT',1800000000000)
        self.assertEqual(requests[0]['granularity'],'1m')
        self.assertEqual(requests[1]['granularity'],'5m')
        self.assertEqual(requests[1]['limit'],'240')


if __name__=='__main__':
    unittest.main()
