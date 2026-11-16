#!/usr/bin/env python3
"""Private, read-only market monitor on localhost."""
import base64
import binascii
import hmac
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from periodic_analysis import build_report, parse_quote_time
from research_context import BJ, SYMBOLS
from engine import fetch_nasdaq_quote

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'.cache'/'market-monitor'/'latest.json'
AUTH=ROOT/'.cache'/'market-monitor'/'access.txt'
HTML=ROOT/'dashboard'/'static'/'market_watch.html'


def authorized(header, password):
    if not header or not header.startswith('Basic '):return False
    try:
        value=base64.b64decode(header[6:],validate=True).decode('utf-8')
    except (ValueError,UnicodeDecodeError,binascii.Error):return False
    user,sep,received=value.partition(':')
    return bool(sep and hmac.compare_digest(user,'viewer') and hmac.compare_digest(received,password))


def redact_stale(data, now):
    try:
        generated=datetime.fromisoformat(data['generated_bj'])
        if generated.tzinfo is None:raise ValueError('missing timezone')
        age=(now.astimezone(timezone.utc)-generated.astimezone(timezone.utc)).total_seconds()
        fresh=-60<=age<=1200
    except (KeyError,TypeError,ValueError):fresh=False
    output=dict(data);output['fresh']=fresh
    if not fresh:
        output['items']=[{**item,'current_price':None,'quote_fresh':False,'signal':'观望','trend_15m_sample':'数据不足','direction_sample':None,'sample_high':None,'sample_low':None,'reason':'定时更新已停止或过期'} for item in data.get('items',[])]
    return output


def merge_quotes(data, quotes, now):
    out=dict(data)
    items=[]
    for item in data.get('items',[]):
        symbol=item.get('symbol')
        quote=quotes.get(symbol)
        live=build_report(now,quote,[],symbol=symbol)
        qt=parse_quote_time((quote or {}).get('ts'))
        net=(quote or {}).get('net_change')
        price=(quote or {}).get('price')
        previous=round(price-net,4) if live['quote_fresh'] and price is not None and net is not None else None
        items.append({**item,'current_price':live['current_price'], 'quote_fresh':live['quote_fresh'],
                      'phase':live['phase'], 'previous_close':previous,
                      'quote_time_bj':qt.astimezone(BJ).isoformat() if qt else None})
    out['items']=items
    return out


def fetch_quotes(symbols):
    results={}
    with ThreadPoolExecutor(max_workers=min(4,max(1,len(symbols)))) as pool:
        futures={symbol:pool.submit(fetch_nasdaq_quote,symbol,force=True) for symbol in symbols}
        for symbol,future in futures.items():
            try:
                quote=future.result()
                if quote is not None:results[symbol]=quote
            except Exception:pass
    return results


def quote_loop(cache, stop):
    while not stop.is_set():
        started=time.monotonic()
        cache.update(fetch_quotes(SYMBOLS))
        stop.wait(max(0,30-(time.monotonic()-started)))


def load_password():
    AUTH.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(str(AUTH),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        return AUTH.read_text(encoding='utf-8').strip()
    password=secrets.token_urlsafe(20)
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(password+'\n')
    return password


def handler(password, quote_cache):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):pass
        def do_GET(self):
            if not authorized(self.headers.get('Authorization'),password):
                self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="Market Watch", charset="UTF-8"');self.send_header('Cache-Control','no-store');self.end_headers();return
            path=urlsplit(self.path).path
            try:
                if path=='/':body=HTML.read_bytes();ctype='text/html; charset=utf-8'
                elif path=='/api/market':
                    try:data=json.loads(DATA.read_text(encoding='utf-8'))
                    except (OSError,ValueError):data={'items':[]}
                    now=datetime.now(timezone.utc)
                    body=json.dumps(merge_quotes(redact_stale(data,now),quote_cache.copy(),now),ensure_ascii=False).encode();ctype='application/json; charset=utf-8'
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except (OSError,BrokenPipeError):return
    return Handler


def main():
    password=load_password()
    if not password:raise RuntimeError('访问密码为空')
    port=int(os.environ.get('MARKET_MONITOR_PORT','8768'))
    quote_cache={}; stop=threading.Event()
    threading.Thread(target=quote_loop,args=(quote_cache,stop),daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',port),handler(password,quote_cache))
    print(f'看板运行于 http://127.0.0.1:{port}；用户名 viewer；密码文件 {AUTH}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:stop.set();server.server_close()

if __name__=='__main__':main()
