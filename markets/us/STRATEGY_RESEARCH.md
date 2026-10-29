# 美股盘中策略研究

本目录的回放工具用于研究信号和持仓，不生成实时交易指令。它们读取本地已有 SQLite 历史数据，不负责下载行情；默认路径是 `markets/us/.cache/session-bars.sqlite3`，可用 `--db` 覆盖。数据库需提供 `bars` 表，含 symbol、timestamp（UTC 秒）、trading_date（美东日期）、session、open、high、low、close、volume 和 source。当前查询只读取 source 为 `Massive historical aggregates` 的记录，以免把无成交量的价格采样当作分钟 K 线。数据库和密钥不纳入版本控制。

## 信号与持仓

- `python3 markets/us/signal_hold.py --mode structure --stop-mode bar-close`：15 分钟信号回放。出现首个方向信号后按下一根开盘模拟入场，方向结构保持则持仓，失效或触及初始止损后在下一根开盘模拟退出。`--confirmations 2` 是延迟入场的研究对照，默认 1。
- `python3 markets/us/intraminute_hold.py`：从开盘 30 分钟区间形成后逐分钟近似首个信号，在下一分钟开盘模拟入场，并按 15 分钟结构检查持仓；初始止损以分钟 K 线近似触发。
- `python3 markets/us/session_replay.py`：盘前、常规、盘后分时段 ORB 回放，仅供探索；可使用 `--trend-filter` 和 `--target-pct` 比较不同假设。

这些工具默认将常规时段收盘视为强制平仓；结构止损、成本、成交和确认方式以各工具为准。15 分钟收盘信号按紧接着的开盘价成交是理想化假设，分钟 K 线也无法还原实时逐笔成交。同一 K 线中高低点的先后不确定。回放不计手续费、价差和额外滑点；缺失时段被排除，可能导致样本选择偏差。当前信号引擎的“观望”包含不适合新开仓，并不必然要求已有持仓退出。回放使用上一交易日的常规时段振幅作为 ATR 代理，并非正式 ATR。

## 方向研究

先运行 `intraminute_hold.py`，再运行 `python3 markets/us/first_signal_quality.py` 和 `python3 markets/us/direction_quality.py`。方向报告比较首次模拟入场之后 15、30、60 分钟的价格与信号方向，不把止盈百分比作为信号门槛；固定时点方向变化忽略中途退出或止损，因此不是实际持仓收益。已有时间段经过多次策略比较，不能视为全新的独立验证集。

```bash
python3 -m unittest discover -s markets/us -p 'test_signal_hold.py'
python3 -m unittest discover -s markets/us -p 'test_intraminute_hold.py'
python3 -m unittest discover -s markets/us -p 'test_session_replay.py'
python3 -m unittest discover -s markets/us -p 'test_first_signal_quality.py'
```
