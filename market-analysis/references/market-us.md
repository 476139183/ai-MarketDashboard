# 美股市场规则（覆盖层）

共用框架见 [analysis-rules.md](analysis-rules.md)。与 A股规则互斥。

## 识别与时段

- 字母代码；NYSE/Nasdaq 等。交付 **北京时间**（括号可补 EDT/EST）。
- RTH 北京约 21:30–次日 04:00（夏）/ 22:30–次日 05:00（冬）。盘前盘后须单独核价。

## 盘中（ORB + VWAP）

见 analysis-rules「美股盘中信号」。要点：

1. 看板默认 **15m**；短线 `|score|≥2` 才开普通多空；震荡不开。
2. **冲高回落日优先低位多·洗盘**（[ORB下+, VWAP]），禁止把洗盘当空、禁止追日高。
3. 普通多：回踩偏 VWAP；普通空：反抽偏 VWAP；止损 ORB 对侧；T1 拉远。
4. **ORB 失效**才平空/平多；R1≥0.8 才 `entry_ready`。

工具：`markets/us/dashboard`、`backtest`。数据：[data-sources-us.md](data-sources-us.md)。

## 高波动

ATR/现价偏大时区间用「带」、确认用分钟收盘；不降低 R1 纪律。
