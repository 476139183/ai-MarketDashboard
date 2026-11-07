# 美股策略回放

## ORB + 短线趋势 · 多周期回放

对 `dashboard/engine.py` 的 `compute_intraday_signal` 按 **1m / 5m / 15m** 分开回放（RTH）。  
分周期只为统计评估；规则同一套，看板侧重多周期是否同向。

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

## 加密永续：布林回归与成本空间过滤（编号6）

独立研究策略 `crypto-bollinger-cost-v1`：已闭合4h布林20震荡回归，入场需到中轨空间≥0.66%，2ATR止损，10x、单笔20%净值保证金、新开仓组合60%上限。使用完整5m成交/标记价格、历史资金费与合约规格回放，不发送真实委托。

```bash
python3 markets/us/backtest/crypto_bollinger_cost.py --bundle /path/to/normalized-okx-bundle.json --output /tmp/strategy06.json
```

以上命令从仓库根目录执行。输入格式、测试和冻结回放边界见[策略说明](../../../docs/strategies/crypto-bollinger-cost-v1.md)。这是事后研究候选，未替换线上模拟规则；原始行情缓存不随代码提交。
