#!/usr/bin/env bash
# 启动美股 15m 信号工作台（ORB + 短线趋势）
# 用法：
#   ./start-dashboard.sh              # 默认缓存优先
#   ./start-dashboard.sh --live       # SIGNAL_LIVE=1 强制拉行情
#   ./start-dashboard.sh --no-open    # 不自动打开浏览器
#   PORT=8766 ./start-dashboard.sh    # 换端口（需改 app 时另说；当前读环境仅作提示）

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DASH="$ROOT/markets/us/dashboard"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
URL="http://${HOST}:${PORT}"
OPEN_BROWSER=1
LIVE=0

for arg in "$@"; do
  case "$arg" in
    --live|-l) LIVE=1 ;;
    --no-open) OPEN_BROWSER=0 ;;
    --help|-h)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: $arg（支持 --live / --no-open）" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$DASH/app.py" ]]; then
  echo "找不到看板: $DASH/app.py" >&2
  exit 1
fi

# 端口占用则提示并结束旧进程（仅本机 8765 看板）
if command -v lsof >/dev/null 2>&1; then
  OLD_PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${OLD_PIDS}" ]]; then
    echo "端口 ${PORT} 已被占用，结束旧进程: ${OLD_PIDS}"
    # shellcheck disable=SC2086
    kill ${OLD_PIDS} 2>/dev/null || true
    sleep 0.4
  fi
fi

cd "$DASH"
export PYTHONUNBUFFERED=1
if [[ "$LIVE" -eq 1 ]]; then
  export SIGNAL_LIVE=1
  echo "模式: LIVE（强制刷新 15m/行情）"
else
  unset SIGNAL_LIVE 2>/dev/null || true
  echo "模式: 缓存优先（加 --live 可强制刷新）"
fi

echo "工作台目录: $DASH"
echo "打开: $URL"
echo "默认标的: SNDK, MU, NVDA · 策略仅 15m（现价/日高低温实时）"
echo "Ctrl+C 停止"
echo

open_later() {
  sleep 0.8
  if [[ "$OPEN_BROWSER" -eq 1 ]]; then
    if command -v open >/dev/null 2>&1; then
      open "$URL" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$URL" >/dev/null 2>&1 || true
    fi
  fi
}
open_later &

exec python3 app.py
