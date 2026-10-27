# 美股策略回放

按美股盘中规则近似回放：**S → E → T1**、5x 压测、北京时间。A股回测未实现。

```bash
cd markets/us
python3 backtest/replay_engine.py
```

- `results/summary.json` / `trades.csv`
- 日线：SNDK 自 2025-02 等
- 15m：近约 60 交易日，破 ORB 后等回抽进 E

`sum_equity_5x` 为各笔加总（非复利），仅供调参。
