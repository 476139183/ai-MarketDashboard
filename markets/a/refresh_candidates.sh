#!/bin/sh
# 盘后刷新候选和过往记录；任何数据源失败时停止，不生成伪装成最新的信号。
set -eu
cd "$(dirname "$0")/../.."
python3 markets/a/hot_source.py
python3 markets/a/benchmark.py
python3 markets/a/hot_backtest.py
python3 markets/a/large_universe.py
python3 markets/a/candidate_monitor.py
