#!/usr/bin/env bash
# 启动本机美股看板、定时行情与临时远程通道。Ctrl+C 停止。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo '未找到 cloudflared，请先安装或使用本地服务。' >&2
  exit 1
fi
if command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 8768 2>/dev/null; then
  echo '8768 端口已在使用，请先停止已有的看板服务。' >&2
  exit 1
fi
mkdir -p markets/us/.cache/market-monitor
python3 -u markets/us/periodic_analysis.py --watch > markets/us/.cache/market-monitor/collector.log 2>&1 &
collector_pid=$!
python3 -u markets/us/monitor_server.py > markets/us/.cache/market-monitor/server.log 2>&1 &
server_pid=$!
cleanup() { kill "$collector_pid" "$server_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
sleep 1
if ! kill -0 "$server_pid" 2>/dev/null; then
  echo '看板启动失败，请查看 markets/us/.cache/market-monitor/server.log。' >&2
  exit 1
fi
echo '用户名：viewer'
echo '密码文件：markets/us/.cache/market-monitor/access.txt'
echo '远程地址将在下方输出；关闭终端会停止服务。'
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8768
