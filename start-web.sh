#!/usr/bin/env bash
# 一键启动观市网页版及所需本机行情服务。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/scripts/serve_web.py" "$@"
