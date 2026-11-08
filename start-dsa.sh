#!/usr/bin/env bash
# 本机独立 DSA 服务。首次：bash start-dsa.sh --setup；启动：bash start-dsa.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CHECKOUT="${DSA_CHECKOUT_DIR:-$ROOT/.cache/daily-stock-analysis}"
REVISION="db0570edd008a319d7eff14eda3bc0ef9da620eb"
SERVICE_PORT="${DSA_PORT:-8000}"

case "${1:-}" in
  --setup)
    if [[ ! -d "$CHECKOUT/.git" ]]; then
      git clone --depth 1 https://github.com/ZhuLinsen/daily_stock_analysis.git "$CHECKOUT"
      if [[ "$(git -C "$CHECKOUT" rev-parse HEAD)" != "$REVISION" ]]; then
        git -C "$CHECKOUT" fetch --depth 1 origin "$REVISION"
        git -C "$CHECKOUT" checkout --detach "$REVISION"
      fi
    elif [[ "$(git -C "$CHECKOUT" rev-parse HEAD)" != "$REVISION" ]]; then
      echo "已有 DSA checkout 与已核验版本不同；保留现有文件。请设置 DSA_CHECKOUT_DIR 使用独立目录。" >&2
      exit 1
    fi
    if [[ ! -x "$CHECKOUT/.venv/bin/python" ]]; then
      python3 -m venv "$CHECKOUT/.venv"
    fi
    if [[ ! -f "$CHECKOUT/.env" ]]; then
      cp "$CHECKOUT/.env.example" "$CHECKOUT/.env"
    fi
    PIP_CACHE_DIR="$ROOT/.cache/pip" "$CHECKOUT/.venv/bin/python" -m pip install -r "$CHECKOUT/requirements.txt"
    echo "DSA 环境已准备。AI 模型和新闻服务请在 $CHECKOUT/.env 中配置。"
    echo "随后执行 bash start-dsa.sh 启动服务。"
    exit 0
    ;;
  --help|-h)
    echo "用法：bash start-dsa.sh [--setup]"
    echo "DSA_CHECKOUT_DIR 指定独立上游目录；DSA_PORT 指定端口（默认 8000）。"
    exit 0
    ;;
  '') ;;
  *) echo "未知参数：$1" >&2; exit 1 ;;
esac

if [[ ! -x "$CHECKOUT/.venv/bin/python" ]]; then
  echo "尚未安装 DSA，请先运行 bash start-dsa.sh --setup" >&2
  exit 1
fi
cd "$CHECKOUT"
exec .venv/bin/python "$ROOT/scripts/serve_dsa.py" --port "$SERVICE_PORT"
