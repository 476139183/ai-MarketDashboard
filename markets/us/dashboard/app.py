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

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.append(str(ROOT.parent))
sys.path.append(str(REPO / 'markets' / 'a'))
from public_history import audit_history, DEFAULT_DB as PUBLIC_HISTORY_DB
import radar as a_radar
STATIC = ROOT / "static"
WATCHLIST = ROOT / "watchlist.txt"
ALERTS = ROOT / "cache" / "alerts.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("BOARD_PORT", "8765"))
MONITOR_PATH = ROOT.parent / ".cache" / "sndk-monitor" / "latest.json"
_RADAR_LOCK = threading.Lock()
_RADAR_STATE = {'status': 'idle', 'error': None, 'started_at': None}


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
            with _RADAR_LOCK:
                _RADAR_STATE.update(status='idle', error=None)
        except Exception as exc:  # noqa: BLE001
            with _RADAR_LOCK:
                _RADAR_STATE.update(status='idle', error=str(exc))

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
        if path not in ('/api/dsa/analysis','/api/dsa/ai-settings'):
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
            client=DSAClient.from_env()
            if path=='/api/dsa/ai-settings':
                self._json(200,client.save_ai_settings(body))
            else:
                self._json(202,client.start_analysis(body.get('symbol', '')))
        except DSAError as exc:
            self._json(503 if exc.status == 503 else 502, {'error': str(exc)})
        except (ValueError, UnicodeError) as exc:
            self._json(400, {'error': str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

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
    print(f"信号看板: http://{HOST}:{PORT}")
    print("Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()
