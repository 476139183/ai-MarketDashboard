"""Daily Stock Analysis HTTP 客户端；凭证仅由看板服务端读取。"""

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date
from functools import lru_cache
from urllib.parse import quote, urlencode, urlsplit


class DSAError(ValueError):
    def __init__(self, message, status=502, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


@lru_cache(maxsize=8)
def _stock_index(base_url, cookie, window):
    # 复用 DSA 的名称/拼音索引，十分钟刷新一次，避免每次输入都下载全表。
    return DSAClient(base_url, timeout=15, cookie=cookie).request('/stocks.index.json', allow_list=True)


def stock_code(value):
    code = value.strip().upper() if isinstance(value, str) else ''
    if not re.fullmatch(r'(?:\d{6}|[A-Z]{1,5}(?:[.-][A-Z])?)', code):
        raise ValueError('请输入 A 股六位代码或美股代码，如 600519、NVDA、BRK.B')
    return code


class DSAClient:
    def __init__(self, base_url, timeout=20, cookie=''):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise DSAError('DSA_BASE_URL 必须是无凭证的 HTTP/HTTPS 服务地址', 503)
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.cookie = cookie

    @classmethod
    def from_env(cls):
        url = os.environ.get('DSA_BASE_URL', '').strip()
        if not url:
            raise DSAError('尚未配置 DSA_BASE_URL，请先启动 DSA 服务并配置地址', 503)
        return cls(url, cookie=os.environ.get('DSA_SESSION_COOKIE', '').strip())

    def request(self, path, body=None, method=None, allow_list=False):
        headers = {'Accept': 'application/json'}
        if self.cookie:
            headers['Cookie'] = 'dsa_session=' + self.cookie
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode('utf-8')
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            payload = {}
            try:
                payload = json.loads(exc.read(65536))
            except (ValueError, OSError):
                pass
            finally:
                exc.close()
            message = f'DSA 服务返回 HTTP {exc.code}'
            if exc.code == 401:
                message += '，请检查 DSA_SESSION_COOKIE 是否有效'
            raise DSAError(message, exc.code, payload if isinstance(payload, dict) else {}) from None
        except (OSError, ValueError):
            raise DSAError('DSA 服务连接失败或超时，请检查服务地址和运行状态') from None
        try:
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('response too large')
            payload = json.loads(raw)
            if not isinstance(payload, dict) and not (allow_list and isinstance(payload, list)):
                raise ValueError('expected object')
        except (ValueError, UnicodeError):
            raise DSAError('DSA 返回了无效 JSON 或过大的响应') from None
        return payload

    def search(self, query):
        query = query.strip() if isinstance(query, str) else ''
        if not query or len(query) > 80:
            raise ValueError('请输入股票名称或代码（最多 80 字）')
        rows = _stock_index(self.base_url, self.cookie, int(time.monotonic() // 600))
        if not isinstance(rows, list):
            raise DSAError('股票名称索引暂不可用，请直接输入股票代码')
        # 上游索引更新有延迟；美股 ADR 上市信息已核对公司和 Nasdaq 公告。
        rows = [*rows, ['SKHY', 'SKHY', 'SK海力士 ADR', 'skhailishi', 'skhls',
                         ['海力士', 'SK Hynix', 'SK海力士'], 'US', 'stock', True, 100]]
        aliases = {'NVDA': ['英伟达'], 'MU': ['美光', '美光科技'],
                   'SNDK': ['闪迪'], 'AAPL': ['苹果'], 'GOOG': ['谷歌', 'Google'],
                   'GOOGL': ['谷歌', 'Google'], 'TSLA': ['特斯拉'], 'MSFT': ['微软'],
                   'AMZN': ['亚马逊'], 'META': ['脸书'], 'TSM': ['台积电'],
                   'AVGO': ['博通'], 'AMD': ['超威半导体'], 'ASML': ['阿斯麦']}
        markets = {'CN': 'A 股', 'US': '美股', 'KR': '韩股', 'HK': '港股',
                   'JP': '日股', 'BSE': '北交所', 'ETF': 'ETF', 'INDEX': '指数'}
        def normalize(value):
            return ''.join(str(value).casefold().split())
        needle = normalize(query)
        matches = {}
        for row in rows:
            if isinstance(row, dict):
                row = [row.get(key) for key in ('canonicalCode', 'displayCode', 'nameZh',
                       'pinyinFull', 'pinyinAbbr', 'aliases', 'market', 'assetType', 'active')]
            if not isinstance(row, (list, tuple)) or len(row) < 9 or row[8] is not True:
                continue
            code, display, name, full, abbr, alternate, market, asset = row[:8]
            if not all(isinstance(value, str) for value in (code, display, name, market)):
                continue
            fields = [code, display, name, full or '', abbr or '',
                      *(alternate if isinstance(alternate, list) else []), *aliases.get(code, [])]
            scores = [0 if normalize(v) == needle else 1 if normalize(v).startswith(needle)
                      else 2 for v in fields if needle in normalize(v)]
            if not scores:
                continue
            supported = asset == 'stock' and (market == 'US' and bool(re.fullmatch(r'[A-Z]{1,5}(?:[.-][A-Z])?', code))
                         or market == 'CN' and bool(re.fullmatch(r'(?:00[0-3]|30[01]|60[0135]|688)\d{3}', display)))
            symbol = display if market == 'CN' else code
            item = {'symbol': symbol, 'name': name, 'market': market,
                    'market_label': markets.get(market, market), 'supported': supported,
                    'note': '' if supported else f'暂未接入{markets.get(market, market)}行情与盘中规则'}
            matches[(market, symbol)] = (min(scores), not supported, symbol, item)
        return {'query': query, 'items': [row[-1] for row in sorted(matches.values(), key=lambda r: r[:3])[:8]]}

    def daily(self, symbol, days=90):
        code = stock_code(symbol)
        if not 1 <= days <= 365:
            raise ValueError('days 必须在 1–365 之间')
        query = urlencode({'period': 'daily', 'days': days})
        payload = self.request(f'/api/v1/stocks/{quote(code, safe="")}/history?{query}')
        if str(payload.get('stock_code') or '').upper() != code or payload.get('period') != 'daily':
            raise DSAError('DSA 返回的标的或周期与请求不一致')
        rows = payload.get('data')
        if not isinstance(rows, list) or not rows:
            raise DSAError('DSA 未返回日 K 线，上游行情可能不可用')
        cleaned, seen = [], set()
        try:
            for row in rows:
                day = date.fromisoformat(row['date']).isoformat()
                if day in seen:
                    raise ValueError('duplicate date')
                seen.add(day)
                values = {key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
                if any(not math.isfinite(value) or value <= 0 for value in values.values()):
                    raise ValueError('invalid price')
                if not (values['low'] <= min(values['open'], values['close'])
                        <= max(values['open'], values['close']) <= values['high']):
                    raise ValueError('invalid OHLC')
                volume = None if row.get('volume') is None else float(row['volume'])
                if volume is not None and (not math.isfinite(volume) or volume < 0):
                    raise ValueError('invalid volume')
                cleaned.append({**values, 'date': day, 'volume': volume})
        except (KeyError, ValueError, TypeError, OverflowError):
            raise DSAError('DSA 日线包含无效日期、重复日期或异常 OHLCV') from None
        return {**payload, 'data': sorted(cleaned, key=lambda row: row['date']), 'source': 'DSA'}

    def service_status(self):
        try:
            health = self.request('/api/health')
            if health.get('status') != 'ok':
                raise DSAError('分析服务健康检查未通过')
        except DSAError as exc:
            return {'configured': True, 'connected': False, 'model_ready': False, 'error': str(exc)}
        try:
            backend = self.request('/api/v1/system/config/generation-backends/status')
            ready = backend.get('primary', {}).get('available') is True
            return {'configured': True, 'connected': True, 'model_ready': ready,
                    'backend':backend.get('primary',{}).get('backend_id')}
        except DSAError as exc:
            return {'configured': True, 'connected': True, 'model_ready': False, 'model_error': str(exc)}

    def ai_settings(self):
        config=self.request('/api/v1/system/config?include_schema=false')
        values={item['key']:item for item in config.get('items',[])}
        def value(key):
            return values.get(key,{}).get('value','')
        local={}
        for backend in ('codex_cli','claude_code_cli'):
            try:
                preview=self.request('/api/v1/system/config/generation-backends/status/preview',
                                     {'items':[{'key':'GENERATION_BACKEND','value':backend}]})
                local[backend]=preview.get('primary',{}).get('available') is True
            except DSAError:
                local[backend]=False
        return {'backend':value('GENERATION_BACKEND') or 'litellm',
                'api_base_url':value('OPENAI_BASE_URL'), 'model':value('LITELLM_MODEL'),
                'api_key_set':values.get('OPENAI_API_KEY',{}).get('raw_value_exists',False),
                'local_available':local}

    def save_ai_settings(self, settings):
        backend=settings.get('backend')
        if backend not in ('litellm','codex_cli','claude_code_cli'):
            raise ValueError('仅支持 API、Codex CLI 或 Claude Code CLI')
        items=[{'key':'GENERATION_BACKEND','value':backend},
               {'key':'GENERATION_FALLBACK_BACKEND','value':''}]
        if backend=='litellm':
            model=str(settings.get('model','')).strip()
            base=str(settings.get('api_base_url','')).strip()
            key=str(settings.get('api_key','')).strip()
            if not model or not base:
                raise ValueError('API 方式需要填写接口地址和模型名称')
            parsed=urlsplit(base)
            if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('接口地址必须是无凭证的 HTTP/HTTPS 地址')
            if any('\n' in v or '\r' in v for v in (model,base,key)):
                raise ValueError('API 配置不能包含换行')
            model=model if model.startswith('openai/') else 'openai/'+model
            items.extend([{'key':'LITELLM_MODEL','value':model},{'key':'OPENAI_BASE_URL','value':base}])
            if key:
                items.append({'key':'OPENAI_API_KEY','value':key})
        config=self.request('/api/v1/system/config?include_schema=false')
        self.request('/api/v1/system/config',{'config_version':config['config_version'],
                     'mask_token':config.get('mask_token','******'),'reload_now':True,'items':items},method='PUT')
        return self.ai_settings()

    def start_analysis(self, symbol):
        code = stock_code(symbol)
        try:
            result = self.request('/api/v1/analysis/analyze', {
                'stock_code': code, 'report_type': 'detailed', 'async_mode': True,
                'notify': False, 'report_language': 'zh',
            })
        except DSAError as exc:
            if exc.status != 409 or not exc.payload.get('existing_task_id'):
                raise
            result = {'task_id': exc.payload['existing_task_id'], 'status': 'pending', 'reused': True}
        if not result.get('task_id'):
            raise DSAError('DSA 未返回分析任务编号')
        return result

    def task_status(self, task_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(task_id)):
            raise ValueError('无效的任务编号')
        return self.request('/api/v1/analysis/status/' + task_id)
