#!/usr/bin/env bash
# 后台扫描美股票池；仅到价/平仓时 macOS 通知
# 用法：
#   ./start-watch.sh           # 后台常驻
#   ./start-watch.sh --once    # 只扫一轮（前台）
#   ./start-watch.sh --stop    # 停止后台
#   ./start-watch.sh --fg      # 前台常驻（看日志）

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DASH="$ROOT/markets/us/dashboard"
PIDFILE="$DASH/cache/signal_watch.pid"
LOGFILE="$DASH/cache/signal_watch.log"
PY=(python3 "$DASH/signal_watch.py")

mkdir -p "$DASH/cache"

stop_watch() {
  if [[ -f "$PIDFILE" ]]; then
    old="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${old}" ]] && kill -0 "$old" 2>/dev/null; then
      echo "停止巡检 PID $old"
      kill "$old" 2>/dev/null || true
      sleep 0.3
    fi
    rm -f "$PIDFILE"
  fi
  # 兜底：同脚本残留
  pkill -f "signal_watch.py" 2>/dev/null || true
}

mode=bg
for arg in "$@"; do
  case "$arg" in
    --once) mode=once ;;
    --fg|--foreground) mode=fg ;;
    --stop) mode=stop ;;
    --help|-h)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: $arg" >&2
      exit 1
      ;;
  esac
done

cd "$DASH"
export PYTHONUNBUFFERED=1
# 巡检用缓存优先，降低限流；需要强制刷新可 SIGNAL_LIVE=1 ./start-watch.sh

if [[ "$mode" == "stop" ]]; then
  stop_watch
  echo "已停止"
  exit 0
fi

if [[ "$mode" == "once" ]]; then
  exec "${PY[@]}" --once
fi

stop_watch
if [[ "$mode" == "fg" ]]; then
  echo "前台巡检 · 日志同时写 $LOGFILE"
  echo "票池: $DASH/watchlist.txt"
  exec "${PY[@]}" 2>&1 | tee -a "$LOGFILE"
fi

nohup "${PY[@]}" >>"$LOGFILE" 2>&1 &
echo $! >"$PIDFILE"
echo "后台巡检已启动 PID $(cat "$PIDFILE")"
echo "票池: $DASH/watchlist.txt"
echo "日志: $LOGFILE"
echo "告警: $DASH/cache/alerts.json"
echo "停止: ./start-watch.sh --stop"
