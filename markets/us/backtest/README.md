# 美股策略回放

## ORB+VWAP 多标的回放（推荐）

对 `dashboard/engine.py` 的 `compute_intraday_signal` 做 15m RTH 回放：

```bash
cd markets/us/backtest
python3 orb_vwap_replay.py                  # 默认 SNDK NVDA MU SKHY WDC AMD AVGO TSM
python3 orb_vwap_replay.py NVDA MU SNDK     # 指定标的
```

结果：`results/*_orb_vwap_replay.json`、`results/multi_orb_vwap_summary.json`。  
规则对齐引擎：短线趋势顺势 + ORB 突破站稳、结构止损（ORB 对侧）、R1≥0.8；回放每日一笔，收盘浮盈兑现、浮亏可跨日到 S。  
海力士美股 ADR 代码：**SKHY**（Nasdaq）。需本地有 `dashboard/cache/{SYM}_15m.json` 或 Yahoo 可用。

## 旧版近似回放

```bash
cd markets/us
python3 backtest/replay_engine.py
```
