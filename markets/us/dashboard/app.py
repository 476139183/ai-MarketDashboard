#!/usr/bin/env python3
"""本地信号看板：http://127.0.0.1:8765"""

from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import analyze_many, analyze_symbol
from engine import BJ
from dsa_client import DSAClient, DSAError
from news_service import REFRESH as news_refresh
from futures_cn import SERVICE as futures_service
from futures_backtest import JOBS as futures_backtest_jobs, SPECS as futures_specs, DEFAULTS as futures_backtest_defaults, STRATEGIES as futures_strategies
from contract_radar import SERVICE as bitget_radar
from okx_contracts import SERVICE as contract_radar
from binance_contracts import SERVICE as binance_radar
from contract_radar import ContractRadar
from radar_periods import period_minutes
from us_radar import SERVICE as us_radar, LIVE_SERVICE as us_chart
from paper_trading import RUNNER as paper_runner, BINANCE_RUNNER as binance_paper_runner, STOCK_RUNNER as stock_paper_runner

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.append(str(ROOT.parent))
sys.path.append(str(REPO / 'markets' / 'a'))
from public_history import audit_history, DEFAULT_DB as PUBLIC_HISTORY_DB
import radar as a_radar
import market_review as a_review
STATIC = ROOT / "static"
WATCHLIST = ROOT / "watchlist.txt"
ALERTS = ROOT / "cache" / "alerts.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("BOARD_PORT", "8765"))
MONITOR_PATH = ROOT.parent / ".cache" / "sndk-monitor" / "latest.json"
_RADAR_LOCK = threading.Lock()
_RADAR_STATE = {'status': 'idle', 'error': None, 'started_at': None}
_REVIEW_LOCK = threading.Lock()
_REVIEW_STATE = {'status': 'idle', 'error': None}
_CONTRACT_PERIODS = {}
_CONTRACT_PERIOD_LOCK = threading.Lock()


def select_paper_runner(exchange='okx', market='crypto'):
    if exchange not in ('okx','binance') or market not in ('crypto','stock'):
        raise ValueError('模拟交易仅支持 okx / binance 与 crypto / stock')
    if market == 'stock':
        if exchange != 'okx':
            raise ValueError('美股合约模拟目前仅支持欧易 OKX')
        return stock_paper_runner
    return paper_runner if exchange == 'okx' else binance_paper_runner


def select_contract_radar(exchange='okx', minutes=240):
    sources = {'okx':contract_radar, 'binance':binance_radar, 'bitget':bitget_radar}
    if exchange not in sources:
        raise ValueError('行情来源仅支持 okx / bitget / binance')
    minutes = period_minutes(minutes)
    base = sources[exchange]
    if minutes == 240:
        return base
    key=(exchange,minutes)
    with _CONTRACT_PERIOD_LOCK:
        if key not in _CONTRACT_PERIODS:
            _CONTRACT_PERIODS[key]=ContractRadar(base.fetch,base.watchlist,source=base.source,
                                                candle_workers=base.candle_workers,minutes=minutes)
        return _CONTRACT_PERIODS[key]


def load_monitor_report(path: Path, now: datetime) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(report["generated_bj"])
        age = (now.astimezone(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds()
        fresh = -60 <= age <= 1200
        report.update(available=True, report_fresh=fresh)
        if not fresh:
            report.update(quote_fresh=False, current_price=None, signal="观望", reason="定时分析已超过 20 分钟未更新，请检查采集脚本。")
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False, "report_fresh": False, "signal": "观望", "reason": "等待定时脚本生成分析。"}


def _load_watchlist() -> list:
    if not WATCHLIST.is_file():
        return ["SNDK", "SKHY", "MU", "NVDA"]
    out = []
    for line in WATCHLIST.read_text(encoding="utf-8").splitlines():
        s = line.strip().upper()
        if not s or s.startswith("#"):
            continue
        out.append(s.split()[0].strip(","))
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq or ["SNDK", "SKHY", "MU", "NVDA"]


def _radar_payload():
    cached = a_radar.load_cached(max_age_hours=18)
    if cached is None:
        cached = {'signal_date': None, 'strategy_candidates': [], 'watch_pool': [],
                  'strategy_count': 0, 'watch_count': 0, 'stale': True,
                  'market_label': '尚未扫描', 'observe_session': '下一交易日',
                  'limitations': ['点击「刷新扫描」生成最近交易日雷达']}
    with _RADAR_LOCK:
        state = dict(_RADAR_STATE)
    return {**cached, 'status': state['status'], 'error': state['error']}


def _start_radar_refresh():
    with _RADAR_LOCK:
        if _RADAR_STATE['status'] == 'running':
            return False
        _RADAR_STATE.update(status='running', error=None, started_at=datetime.now(timezone.utc).isoformat())

    def job():
        try:
            a_radar.scan(force_hot=True)
            try:
                a_review.build_review()
            except Exception:  # noqa: BLE001 - 复盘失败不阻断雷达
                pass
            with _RADAR_LOCK:
                _RADAR_STATE.update(status='idle', error=None)
        except Exception as exc:  # noqa: BLE001
            with _RADAR_LOCK:
                _RADAR_STATE.update(status='idle', error=str(exc))

    threading.Thread(target=job, daemon=True).start()
    return True


def _review_payload():
    cached = a_review.load_cached(max_age_hours=18)
    if cached is None:
        cached = {'as_of': None, 'market_tone': '尚未复盘', 'summary_bullets': [],
                  'indices': [], 'top_sectors': [], 'bottom_sectors': [], 'stale': True,
                  'next_day_hint': '点击刷新生成盘后复盘', 'limitations': []}
    with _REVIEW_LOCK:
        state = dict(_REVIEW_STATE)
    return {**cached, 'status': state['status'], 'error': state['error']}


def _start_review_refresh():
    with _REVIEW_LOCK:
        if _REVIEW_STATE['status'] == 'running':
            return False
        _REVIEW_STATE.update(status='running', error=None)

    def job():
        try:
            a_review.build_review()
            with _REVIEW_LOCK:
                _REVIEW_STATE.update(status='idle', error=None)
        except Exception as exc:  # noqa: BLE001
            with _REVIEW_LOCK:
                _REVIEW_STATE.update(status='idle', error=str(exc))

    threading.Thread(target=job, daemon=True).start()
    return True


def load_alerts(now: datetime) -> dict:
    try:
        data = json.loads(ALERTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"updated_bj": None, "active": [], "history": [], "new": [], "errors": []}
    active = []
    for row in data.get("active", []):
        try:
            expiry=row.get('valid_until_bj')
            if expiry and datetime.fromisoformat(expiry)<=now:
                continue
            fired = datetime.strptime(row["fired_bj"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
            if -60 <= (now - fired).total_seconds() <= 1200:
                active.append(row)
        except (ValueError, TypeError, KeyError):
            continue
    return {**data, "active": active}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[board] {self.address_string()} - {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def do_POST(self) -> None:
        path=urlparse(self.path).path
        if path not in ('/api/dsa/analysis','/api/dsa/ai-settings', '/api/paper/control','/api/news/refresh','/api/news/sources','/api/futures/backtest'):
            self._json(404, {'error': 'not found'})
            return
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            self._json(403, {'error': '不允许跨站发起分析'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('无效的请求长度')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('请求必须是 JSON 对象')
            if path == '/api/futures/backtest':
                self._json(202, futures_backtest_jobs.start(body))
                return
            if path == '/api/paper/control':
                runner = select_paper_runner(body.get('exchange','okx'),body.get('market','crypto'))
                runner.account.set_enabled(body.get('enabled'))
                runner.start()
                self._json(200, runner.snapshot())
                return
            if path=='/api/news/refresh':
                news_refresh.start()
                self._json(202,news_refresh.snapshot())
                return
            client=DSAClient.from_env()
            if path=='/api/news/sources':
                self._json(200,client.news_add_source(body.get('preset')))
                return
            if path=='/api/dsa/ai-settings':
                self._json(200,client.save_ai_settings(body))
            else:
                self._json(202,client.start_analysis(body.get('symbol', '')))
        except DSAError as exc:
            self._json(503 if exc.status == 503 else 502, {'error': str(exc)})
        except (ValueError, UnicodeError) as exc:
            self._json(400, {'error': str(exc)})
        except (OSError, sqlite3.Error) as exc:
            self._json(503, {'error': str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ('/futures-radar', '/futures-radar/'):
            self._send(200, (STATIC / 'futures-radar.html').read_bytes(), 'text/html; charset=utf-8')
            return
        if path == '/api/futures/radar':
            try:
                period = parse_qs(parsed.query).get('period', ['day'])[0]
                from futures_cn import validate_period
                self._json(200, futures_service.radar(validate_period(period)))
            except ValueError as exc:
                self._json(400, {'error': str(exc)})
            return
        if path in ('/futures', '/futures/'):
            self._send(200, (STATIC / 'futures.html').read_bytes(), 'text/html; charset=utf-8')
            return
        if path == '/api/futures/backtest':
            qs = parse_qs(parsed.query)
            job_id = (qs.get('id') or [''])[0]
            try:
                payload = futures_backtest_jobs.snapshot(job_id) if job_id else dict(
                    specs={k:dict(multiplier=v[0],tick_size=v[1]) for k,v in futures_specs.items()},
                    defaults=futures_backtest_defaults, strategies=futures_strategies)
                self._json(200, payload)
            except KeyError as exc:
                self._json(404, {'error': str(exc.args[0])})
            return
        if path in ('/api/futures', '/api/futures/chart'):
            try:
                qs = parse_qs(parsed.query)
                payload = futures_service.snapshot() if path == '/api/futures' else futures_service.chart(
                    (qs.get('symbol') or ['RB0'])[0], (qs.get('period') or ['day'])[0])
                self._json(200, payload)
            except ValueError as exc:
                self._json(400, {'error': str(exc)})
            return

        if path in ('/news','/news/'):
            self._send(200,(STATIC/'news.html').read_bytes(),'text/html; charset=utf-8')
            return
        if path in ('/api/news','/api/news/sources','/api/news/refresh'):
            try:
                if path=='/api/news/refresh':self._json(200,news_refresh.snapshot());return
                client=DSAClient.from_env()
                if path=='/api/news/sources':self._json(200,client.news_sources());return
                qs=parse_qs(parsed.query)
                self._json(200,client.news_items(market=(qs.get('market')or['all'])[0],query=(qs.get('q')or[''])[0],symbol=(qs.get('symbol')or[''])[0],days=int((qs.get('days')or['7'])[0]),page=int((qs.get('page')or['1'])[0]),page_size=int((qs.get('page_size')or['20'])[0])))
            except DSAError as exc:self._json(503 if exc.status==503 else 502,{'error':str(exc)})
            except (ValueError,TypeError) as exc:self._json(400,{'error':str(exc)})
            return

        if path in ('/us-radar', '/us-radar/'):
            self._send(200, (STATIC / 'us-radar.html').read_bytes(), 'text/html; charset=utf-8')
            return

        if path in ('/api/us-radar','/api/us-chart'):
            qs=parse_qs(parsed.query)
            try:
                symbols=(qs.get('symbols') or [','.join(_load_watchlist()[:8])])[0].split(',')
                if path=='/api/us-chart' and len(symbols)!=1:raise ValueError('实时图表每次仅支持一只美股')
                service=us_chart if path=='/api/us-chart' else us_radar
                self._json(200,service.payload(symbols,(qs.get('period') or ['15'])[0],
                                              (qs.get('session') or ['all'])[0],(qs.get('provider') or ['auto'])[0]))
            except ValueError as exc:
                self._json(400,{'error':str(exc)})
            return

        if path in ('/paper', '/paper/'):
            self._send(200, (STATIC / 'paper.html').read_bytes(), 'text/html; charset=utf-8')
            return

        if path == '/api/paper':
            try:
                qs=parse_qs(parsed.query)
                runner = select_paper_runner((qs.get('exchange') or ['okx'])[0],(qs.get('market') or ['crypto'])[0])
                self._json(200, runner.snapshot())
            except ValueError as exc:
                self._json(400, {'error': str(exc)})
            except (OSError, sqlite3.Error) as exc:
                self._json(503, {'error': str(exc)})
            return

        if path in ('/contracts', '/contracts/'):
            self._send(200, (STATIC / 'contracts.html').read_bytes(), 'text/html; charset=utf-8')
            return

        if path == '/api/contracts':
            qs=parse_qs(parsed.query)
            try:
                service=select_contract_radar((qs.get('exchange') or ['okx'])[0],(qs.get('period') or ['240'])[0])
                self._json(200,service.payload())
            except ValueError as exc:
                self._json(400,{'error':str(exc)})
            return

        if path == '/api/history-samples':
            try:
                self._json(200, audit_history(PUBLIC_HISTORY_DB))
            except (OSError, ValueError, sqlite3.Error):
                # 本地历史审计失败不影响实时行情或 AI 任务。
                self._json(503, {'error': '历史样本暂不可读，请运行样本审计检查本地数据库'})
            return

        if path == '/api/market-state':
            qs = parse_qs(parsed.query)
            try:
                code = (qs.get('symbol') or [''])[0].strip().upper()
                overnight = (qs.get('accept_overnight') or ['0'])[0] == '1'
                self._json(200, analyze_symbol(code, accept_overnight=overnight))
            except (ValueError, OSError) as exc:
                self._json(400, {'error': str(exc)})
            return

        if path in ('/research', '/research/'):
            self._send(200, (STATIC / 'research.html').read_bytes(), 'text/html; charset=utf-8')
            return

        if path in ('/a-radar', '/a-radar/'):
            self._send(200, (STATIC / 'a-radar.html').read_bytes(), 'text/html; charset=utf-8')
            return

        if path in ('/a-review', '/a-review/'):
            self._send(200, (STATIC / 'a-review.html').read_bytes(), 'text/html; charset=utf-8')
            return

        if path == '/api/a-radar':
            qs = parse_qs(parsed.query)
            refresh = (qs.get('refresh') or ['0'])[0] in ('1', 'true', 'yes')
            if refresh:
                _start_radar_refresh()
            payload = _radar_payload()
            if refresh and payload.get('status') == 'running' and not payload.get('signal_date'):
                payload = {**payload, 'generated_at': None, 'observe_session': '下一交易日',
                           'strategy_candidates': [], 'watch_pool': []}
            self._json(200, payload)
            return

        if path == '/api/a-review':
            qs = parse_qs(parsed.query)
            refresh = (qs.get('refresh') or ['0'])[0] in ('1', 'true', 'yes')
            if refresh:
                _start_review_refresh()
            self._json(200, _review_payload())
            return

        if path.startswith('/api/dsa/'):
            try:
                if path == '/api/dsa/config':
                    if not os.environ.get('DSA_BASE_URL', '').strip():
                        result = {'configured': False, 'connected': False, 'model_ready': False}
                    else:
                        result = DSAClient.from_env().service_status()
                    self._json(200, result)
                    return
                client = DSAClient.from_env()
                qs = parse_qs(parsed.query)
                if path == '/api/dsa/search':
                    result = client.search((qs.get('q') or [''])[0])
                elif path == '/api/dsa/history':
                    client.timeout = 60
                    result = client.daily((qs.get('symbol') or [''])[0],
                                          days=int((qs.get('days') or ['90'])[0]))
                elif path == '/api/dsa/status':
                    result = client.task_status((qs.get('task_id') or [''])[0])
                elif path == '/api/dsa/ai-settings':
                    result = client.ai_settings()
                else:
                    self._json(404, {'error': 'not found'})
                    return
                self._json(200, result)
            except DSAError as exc:
                self._json(503 if exc.status == 503 else 502, {'error': str(exc)})
            except ValueError as exc:
                self._json(400, {'error': str(exc)})
            return

        if path in ("/", "/index.html"):
            data = (STATIC / "index.html").read_bytes()
            self._send(200, data, "text/html; charset=utf-8")
            return

        if path in ("/premarket", "/premarket/"):
            self._send(200, (STATIC / "premarket.html").read_bytes(), "text/html; charset=utf-8")
            return

        if path in ("/backtest", "/backtest/"):
            report = ROOT / "cache" / "trend-trade-report.html"
            if report.is_file():
                self._send(200, report.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "尚未生成历史回放报告".encode("utf-8"), "text/plain; charset=utf-8")
            return

        if path in ("/diagnostics", "/diagnostics/"):
            report = ROOT / "cache" / "trade-diagnostics-report.html"
            if report.is_file():
                self._send(200, report.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "尚未生成逐笔诊断报告".encode("utf-8"), "text/plain; charset=utf-8")
            return

        if path == "/api/premarket":
            result = load_monitor_report(MONITOR_PATH, datetime.now(timezone.utc))
            self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            fp = (STATIC / rel).resolve()
            if not str(fp).startswith(str(STATIC.resolve())) or not fp.is_file():
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
            # JS/CSS 强制不缓存，避免结构/信号 UI 改了还看到旧版
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/watchlist":
            wl = _load_watchlist()
            body = json.dumps(
                {"symbols": wl, "joined": ",".join(wl)}, ensure_ascii=False
            ).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/alerts":
            raw = json.dumps(load_alerts(datetime.now(timezone.utc)), ensure_ascii=False).encode("utf-8")
            self._send(200, raw, "application/json; charset=utf-8")
            return

        if path == "/api/signals":
            qs = parse_qs(parsed.query)
            raw = (qs.get("symbols") or [",".join(_load_watchlist())])[0]
            symbols = [x.strip().upper() for x in raw.split(",") if x.strip()]
            only = (qs.get("only") or [""])[0].strip().lower() in ("1", "signal", "active")
            interval = "15m"
            try:
                payload = analyze_many(symbols or ["SNDK"], interval=interval, multi=False)
                if only:
                    items = [
                        it
                        for it in (payload.get("items") or [])
                        if (it.get("advice") in ("做多", "做空") and it.get("at_entry"))
                        or it.get("advice") in ("平多", "平空")
                    ]
                    payload = {**payload, "items": items, "only_signals": True}
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as e:
                err = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
                self._send(500, err, "application/json; charset=utf-8")
            return

        if path == "/api/health":
            self._send(200, b'{"ok":true,"app":"market_studio"}', "application/json; charset=utf-8")
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    paper_runner.start()
    binance_paper_runner.start()
    stock_paper_runner.start()
    print(f"信号看板: http://{HOST}:{PORT}")
    print("Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()
    finally:
        paper_runner.stop_event.set()
        binance_paper_runner.stop_event.set()
        stock_paper_runner.stop_event.set()


if __name__ == "__main__":
    main()
