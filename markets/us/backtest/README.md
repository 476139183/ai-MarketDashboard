# 美股策略回放

## ORB + 短线趋势 · 多周期回放

对 `dashboard/engine.py` 的 `compute_intraday_signal` 按 **1m / 5m / 15m** 分开回放（RTH）。

```bash
cd markets/us/backtest
python3 orb_vwap_replay.py                         # 默认 SNDK NVDA MU × 1m,5m,15m
python3 orb_vwap_replay.py SNDK NVDA MU --live     # 强制拉 Yahoo
python3 orb_vwap_replay.py --days 40 --intervals 5m,15m
```

样本长度受 Yahoo 限制大致为：`1m≈7日`，`5m/15m≈60日`（脚本默认 5m/15m 用最近 40 个交易日）。

结果：

- `results/{sym}_{1m|5m|15m}_orb_vwap_replay.json`
- `results/summary_{1m|5m|15m}.json`
- `results/multi_interval_summary.json`

规则：短线趋势顺势 + ORB（开盘≈30 分钟）/ 回踩反抽；结构止损 ORB 对侧；R1≥0.8；收盘浮盈≥1.5% 可兑现。

## 旧版近似回放

```bash
cd markets/us
python3 backtest/replay_engine.py
```
