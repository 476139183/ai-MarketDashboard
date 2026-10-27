# 美股市场规则（覆盖层）

共用框架见 [analysis-rules.md](analysis-rules.md)。与 A股规则互斥。

## 识别与时段

- 字母代码；NYSE/Nasdaq 等。交付 **北京时间**（括号可补 EDT/EST）。
- RTH 北京约 21:30–次日 04:00（夏）/ 22:30–次日 05:00（冬）。盘前盘后须单独核价。

## 盘中（ORB + VWAP）

见 analysis-rules「美股盘中信号」。要点：

1. **15m 短期趋势** 定可否开仓（只顺势；震荡不开）。
2. 主开仓偏 **空头**：ORB 下破 + 强趋势反抽；多头突破默认观察。
3. **止损在 ORB 对侧**；**T1 拉远**；收盘浮盈≥1.5% 可兑现。
4. **ORB 失效**才平空/平多。
5. R1≥0.8；5x 仅参考（非硬闸、非满仓）。

工具：`markets/us/dashboard`、`backtest`。数据：[data-sources-us.md](data-sources-us.md)。

## 高波动

ATR/现价偏大时区间用「带」、确认用分钟收盘；不降低 R1/5x 纪律。
