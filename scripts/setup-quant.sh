#!/usr/bin/env bash
set -euo pipefail
QUANT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QUANT_PYTHON="${QUANT_PYTHON:-python3.12}"
if [[ ! -x "$QUANT_ROOT/.venv-quant/bin/python" ]]; then
  "$QUANT_PYTHON" -m venv "$QUANT_ROOT/.venv-quant"
fi
"$QUANT_ROOT/.venv-quant/bin/python" -m pip install -r "$QUANT_ROOT/requirements-quant.txt" -c "$QUANT_ROOT/requirements-quant.lock.txt"
"$QUANT_ROOT/.venv-quant/bin/python" -c 'import rqalpha,tqsdk; print("RQAlpha / TqSdk 导入通过；未连接任何账户")'
