"""本机网页版启动入口，只管理本次创建的子进程。"""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def health(url):
    try:
        with urlopen(url, timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def wait_ready(url, child, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if health(url):
            return
        if child.poll() is not None:
            raise RuntimeError('服务启动失败，请查看 .cache 目录下的服务日志')
        time.sleep(.5)
    raise RuntimeError('服务启动超时，请查看 .cache 目录下的服务日志')


def main():
    parser = argparse.ArgumentParser(description='启动观市网页研究工作台')
    parser.add_argument('--port', type=int, default=8766, help='网页版端口（默认 8766）')
    parser.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    if health(url + '/api/health').get('app') == 'market_studio':
        print('网页版已运行：' + url + '/research', flush=True)
        if not args.no_open:
            webbrowser.open(url + '/research')
        return
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            raise RuntimeError(f'端口 {args.port} 已被其他程序占用，请使用 --port 指定空闲端口') from None

    env = dict(os.environ)
    config = ROOT / '.env'
    if config.is_file():
        for line in config.read_text().splitlines():
            key, separator, value = line.removeprefix('export ').partition('=')
            feed_keys = (
                'DSA_BASE_URL', 'DSA_SESSION_COOKIE', 'DSA_NEWS_DNS_MODE',
                'CN_DAILY_SOURCES', 'US_DAILY_SOURCES',
                'FEEDS_CROSSCHECK', 'TUSHARE_TOKEN',
                'ALPHAVANTAGE_API_KEY', 'FINNHUB_API_KEY', 'MASSIVE_API_KEY',
            )
            if separator and key.strip() in feed_keys:
                parts = shlex.split(value, comments=True)
                env.setdefault(key.strip(), parts[0] if parts else '')
    env.setdefault('DSA_BASE_URL', 'http://127.0.0.1:8000')
    env['BOARD_PORT'] = str(args.port)
    env['PYTHONUNBUFFERED'] = '1'
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    children, logs = [], []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if env['DSA_BASE_URL'].rstrip('/') == 'http://127.0.0.1:8000' and health(env['DSA_BASE_URL'] + '/api/health').get('status') != 'ok':
            checkout = Path(env.get('DSA_CHECKOUT_DIR', str(cache / 'daily-stock-analysis')))
            python = checkout / '.venv/bin/python'
            installed = python.is_file() and subprocess.run([str(python), '-c', 'import uvicorn'], capture_output=True).returncode == 0
            if not installed:
                print('首次使用，正在安装独立行情服务…', flush=True)
                subprocess.run(['bash', str(ROOT / 'start-dsa.sh'), '--setup'], env=env, check=True)
            log = (cache / 'dsa-service.log').open('a')
            logs.append(log)
            child = subprocess.Popen(['bash', str(ROOT / 'start-dsa.sh')], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            children.append(child)
            print('正在启动行情服务…', flush=True)
            wait_ready(env['DSA_BASE_URL'] + '/api/health', child)

        log = (cache / 'web-dashboard.log').open('a')
        logs.append(log)
        child = subprocess.Popen([sys.executable, str(ROOT / 'markets/us/dashboard/app.py')], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        children.append(child)
        wait_ready(url + '/api/health', child)
        print('观市网页版已启动：' + url + '/research', flush=True)
        print('按 Ctrl+C 停止本次启动的服务。日志：.cache/web-dashboard.log', flush=True)
        if not args.no_open:
            webbrowser.open(url + '/research')
        while True:
            if any(process.poll() is not None for process in children):
                raise RuntimeError('服务意外退出，请查看 .cache 目录下的日志')
            time.sleep(1)
    except KeyboardInterrupt:
        print('正在停止本次启动的服务…', flush=True)
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
