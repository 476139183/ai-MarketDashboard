import copy
import json
import os
import io
import unittest
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlsplit
from unittest.mock import patch

import engine
from app import Handler


class DSAIntegrationTests(unittest.TestCase):
    url = 'http://dsa.test'

    def transport(self, request, timeout):
        parsed = urlsplit(request.full_url)
        self.__class__.requests.append((request.get_method(), parsed.path + ('?' + parsed.query if parsed.query else ''),
                                       request.get_header('Cookie'), request.data or b''))
        payload = self.payload
        if parsed.path == '/api/health':
            payload = {'status': 'ok'}
        elif parsed.path.endswith('/generation-backends/status'):
            payload = {'primary': {'available': False}}
        raw = json.dumps(payload).encode()
        if self.status >= 400:
            raise HTTPError(request.full_url, self.status, 'upstream error', {}, io.BytesIO(raw))
        return io.BytesIO(raw)

    def setUp(self):
        from dsa_client import _stock_index
        _stock_index.cache_clear()
        self.__class__.requests = []
        self.__class__.status = 200
        self.__class__.payload = dict(stock_code='NVDA', stock_name='英伟达', period='daily', data=[
            dict(date='2026-10-29', open=100, high=104, low=99, close=103, volume=None),
            dict(date='2026-10-30', open=103, high=106, low=102, close=105, volume=1234),
        ])
        self.env = patch.dict(os.environ, {'DSA_BASE_URL': self.url, 'DSA_SESSION_COOKIE': 'test-session'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        transport = patch('urllib.request.urlopen', side_effect=self.transport)
        transport.start()
        self.addCleanup(transport.stop)

    def client(self):
        import dsa_client
        return dsa_client.DSAClient.from_env()

    def test_daily_uses_actual_api_contract_and_preserves_missing_volume(self):
        result = self.client().daily('nvda', days=90)
        self.assertEqual(result['data'][0]['volume'], None)
        self.assertEqual(result['data'][-1]['close'], 105)
        method, path, cookie, _ = self.requests[-1]
        self.assertEqual(method, 'GET')
        self.assertEqual(path, '/api/v1/stocks/NVDA/history?period=daily&days=90')
        self.assertEqual(cookie, 'dsa_session=test-session')

    def test_daily_rejects_empty_wrong_symbol_duplicate_dates_and_bad_ohlc(self):
        original = copy.deepcopy(self.payload)
        cases = [dict(data=[]), dict(stock_code='600519'), dict(period='weekly'),
                 dict(data=[original['data'][0], original['data'][0]]),
                 dict(data=[dict(original['data'][0], close=999)]),
                 dict(data=[dict(original['data'][0], open=float('nan'))]),
                 dict(data=[dict(original['data'][0], volume=-1)]),
                 dict(stock_code=None), dict(data=[None])]
        for changes in cases:
            with self.subTest(changes=changes):
                self.__class__.payload = {**copy.deepcopy(original), **changes}
                with self.assertRaisesRegex(ValueError, 'DSA'):
                    self.client().daily('NVDA')

    def test_name_search_returns_market_choices_and_does_not_treat_korea_as_a_share(self):
        self.__class__.payload = [
            ['000660.KS', '000660.KS', 'SK海力士', 'SKhailishi', 'SKhls', ['海力士'], 'KR', 'stock', True, 100],
            ['600519.SH', '600519', '贵州茅台', 'guizhoumaotai', 'gzmt', ['茅台'], 'CN', 'stock', True, 100],
        ]
        status, result = self.local_api('GET', '/api/dsa/search?q=%E6%B5%B7%E5%8A%9B%E5%A3%AB')
        self.assertEqual(status, 200)
        us = next(row for row in result['items'] if row['symbol'] == 'SKHY')
        kr = next(row for row in result['items'] if row['symbol'] == '000660.KS')
        self.assertTrue(us['supported'])
        self.assertEqual(us['market'], 'US')
        self.assertFalse(kr['supported'])
        self.assertIn('韩股', kr['note'])
        self.assertTrue(all(method == 'GET' for method, *_ in self.requests))

    def test_name_search_handles_pinyin_alias_empty_and_unknown_query(self):
        self.__class__.payload = [
            ['600519.SH', '600519', '贵州茅台', 'guizhoumaotai', 'gzmt', ['茅台'], 'CN', 'stock', True, 100],
        ]
        for query in ('gzmt', '%E8%8C%85%E5%8F%B0'):
            status, result = self.local_api('GET', '/api/dsa/search?q=' + query)
            self.assertEqual(status, 200)
            self.assertEqual(result['items'][0]['symbol'], '600519')
        status, result = self.local_api('GET', '/api/dsa/search?q=zzzzunknown')
        self.assertEqual(result['items'], [])
        status, result = self.local_api('GET', '/api/dsa/search?q=')
        self.assertEqual(status, 400)

    def test_async_analysis_never_sends_notifications(self):
        self.__class__.payload = dict(task_id='task_123', status='pending')
        result = self.client().start_analysis('600519')
        self.assertEqual(result['task_id'], 'task_123')
        method, path, _, raw = self.requests[-1]
        self.assertEqual((method, path), ('POST', '/api/v1/analysis/analyze'))
        body = json.loads(raw)
        self.assertEqual(body['stock_code'], '600519')
        self.assertTrue(body['async_mode'])
        self.assertFalse(body['notify'])

    def test_ai_settings_never_return_saved_api_secret(self):
        self.__class__.payload = {'config_version':'v1','items':[
            {'key':'OPENAI_API_KEY','value':'secret-value','raw_value_exists':True},
            {'key':'GENERATION_BACKEND','value':'codex_cli','raw_value_exists':True}]}
        result=self.client().ai_settings()
        self.assertEqual(result['backend'],'codex_cli')
        self.assertTrue(result['api_key_set'])
        self.assertNotIn('secret-value',json.dumps(result))

    def test_local_ai_choice_uses_native_backend_without_wiping_api_config(self):
        self.__class__.payload={'config_version':'v1','mask_token':'******','items':[],'success':True}
        self.client().save_ai_settings({'backend':'codex_cli'})
        puts=[r for r in self.requests if r[0]=='PUT']
        body=json.loads(puts[0][3])
        items={i['key']:i['value'] for i in body['items']}
        self.assertEqual(items['GENERATION_BACKEND'],'codex_cli')
        self.assertNotIn('OPENAI_API_KEY',items)
        self.assertEqual(items['GENERATION_FALLBACK_BACKEND'],'')

    def test_duplicate_analysis_reuses_existing_task(self):
        self.__class__.status = 409
        self.__class__.payload = dict(error='duplicate_task', existing_task_id='task_existing')
        self.assertEqual(self.client().start_analysis('NVDA')['task_id'], 'task_existing')

    def test_status_retrieves_report_without_submitting_again(self):
        self.__class__.payload = dict(task_id='task_123', status='completed', result=dict(report=dict(summary=dict(analysis_summary='测试结论'))))
        result = self.client().task_status('task_123')
        self.assertEqual(result['result']['report']['summary']['analysis_summary'], '测试结论')
        self.assertEqual(self.requests[-1][:2], ('GET', '/api/v1/analysis/status/task_123'))

    def test_http_error_is_explicit_and_does_not_expose_cookie(self):
        self.__class__.status = 401
        with self.assertRaisesRegex(ValueError, '401') as error:
            self.client().daily('NVDA')
        self.assertNotIn('test-session', str(error.exception))

    def test_invalid_code_never_reaches_upstream(self):
        for symbol in ['../../config', '', 'NVDA?x=y', 'NVDA/MU', None]:
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.client().daily(symbol)
        self.assertEqual(self.requests, [])

    def test_connection_timeout_returns_clear_error(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError()):
            with self.assertRaisesRegex(ValueError, '超时'):
                self.client().daily('NVDA')

    def test_service_status_distinguishes_running_service_from_missing_model(self):
        status = self.client().service_status()
        self.assertTrue(status['connected'])
        self.assertFalse(status['model_ready'])
        self.assertNotIn('test-session', json.dumps(status))

    def test_engine_maps_daily_dates_to_new_york_and_does_not_forge_quote(self):
        rows, meta, source = engine.fetch_chart('NVDA', '3mo', '1d')
        self.assertEqual(source, 'dsa')
        self.assertEqual(rows[-1]['dt_ny'].strftime('%Y-%m-%d %H:%M'), '2026-10-30 09:30')
        self.assertEqual(rows[-1]['c'], 105)
        self.assertNotIn('regularMarketPrice', meta)
        self.assertEqual(meta['daily_as_of'], '2026-10-30')

    def test_failure_falls_back_to_yahoo_with_reason(self):
        self.__class__.payload['data'] = []
        with patch.object(engine, '_http_json', return_value={}), \
             patch.object(engine, '_parse_chart', return_value=([{'c': 100}], {})), \
             patch.object(engine, '_save_cache'):
            rows, meta, source = engine.fetch_chart('NVDA', '3mo', '1d')
        self.assertEqual(source, 'live')
        self.assertEqual(rows[0]['c'], 100)
        self.assertIn('DSA', meta['daily_error'])

    def test_minute_requests_do_not_use_dsa(self):
        with patch.object(engine, '_http_json', return_value={}), \
             patch.object(engine, '_parse_chart', return_value=([{'c': 100}], {})), \
             patch.object(engine, '_save_cache'):
            engine.fetch_chart('NVDA', '5d', '15m')
        self.assertEqual(self.requests, [])

    def test_six_month_request_preserves_requested_history_window(self):
        engine.fetch_chart('NVDA', '6mo', '1d')
        self.assertIn('days=180', self.requests[-1][1])

    def test_force_cache_never_calls_dsa(self):
        with patch.dict(os.environ, {'SIGNAL_CACHE': '1'}), \
             patch.object(engine, '_load_cache', return_value=([{'c': 100}], {})), \
             patch.object(engine, '_http_json', side_effect=OSError('offline')):
            engine.fetch_chart('NVDA', '3mo', '1d')
        self.assertEqual(self.requests, [])

    def local_api(self, method, path, body=None, origin=None):
        handler = object.__new__(Handler)
        handler.path = path
        raw = json.dumps(body).encode() if body is not None else b''
        handler.rfile = io.BytesIO(raw)
        handler.headers = {'Content-Length': str(len(raw)), 'Host': '127.0.0.1:8765'}
        if origin:
            handler.headers['Origin'] = origin
        result = []
        handler._send = lambda status, data, content_type: result.append((status, data, content_type))
        getattr(handler, 'do_' + method)()
        status, data, _ = result[0]
        return status, json.loads(data)

    def test_local_history_api_and_config_never_return_credentials(self):
        status, payload = self.local_api('GET', '/api/dsa/history?symbol=NVDA')
        self.assertEqual(status, 200)
        self.assertEqual(payload['data'][-1]['close'], 105)
        status, config = self.local_api('GET', '/api/dsa/config')
        self.assertEqual(status, 200)
        self.assertTrue(config['configured'])
        self.assertNotIn('test-session', json.dumps(config))

    def test_local_post_submits_async_and_local_status_reads_result(self):
        self.__class__.payload = dict(task_id='task_test', status='pending')
        status, result = self.local_api('POST', '/api/dsa/analysis', {'symbol': 'NVDA'})
        self.assertEqual(status, 202)
        self.assertEqual(result['task_id'], 'task_test')
        self.__class__.payload = dict(task_id='task_test', status='failed', error='模型未配置')
        status, result = self.local_api('GET', '/api/dsa/status?task_id=task_test')
        self.assertEqual(status, 200)
        self.assertEqual(result['error'], '模型未配置')

    def test_cross_origin_post_cannot_trigger_model_cost(self):
        status, _ = self.local_api('POST', '/api/dsa/analysis', {'symbol': 'NVDA'}, origin='https://outside.test')
        self.assertEqual(status, 403)
        self.assertEqual(self.requests, [])

    def test_local_errors_do_not_look_like_success(self):
        self.__class__.status = 401
        status, payload = self.local_api('GET', '/api/dsa/history?symbol=NVDA')
        self.assertEqual(status, 502)
        self.assertIn('401', payload['error'])
        with patch.dict(os.environ, {'DSA_BASE_URL': ''}):
            status, payload = self.local_api('GET', '/api/dsa/history?symbol=NVDA')
        self.assertEqual(status, 503)
        self.assertIn('DSA_BASE_URL', payload['error'])


if __name__ == '__main__':
    unittest.main()
