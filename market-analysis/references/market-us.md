# 美股市场规则（覆盖层）

共用框架见 [analysis-rules.md](analysis-rules.md)。与 A股规则互斥。

## 识别与时段

- 字母代码；NYSE/Nasdaq 等。交付 **北京时间**（括号可补 EDT/EST）。
- RTH 北京约 21:30–次日 04:00（夏）/ 22:30–次日 05:00（冬）。盘前盘后须单独核价。

## 盘中（ORB + VWAP）

见 analysis-rules「美股盘中信号」。要点：

1. **VWAP 定偏向**，逆势突破不做。
2. **ORB 同向突破**为主；回踩/反抽为续；假突破才反手。
3. **止损在结构失效侧**（ORB/VWAP），过远则风控闸禁止开仓。
4. VWAP 翻面 → **平空/平多**。
5. R1≥0.8、5x\<35% 硬闸。

工具：`markets/us/dashboard`、`backtest`。数据：[data-sources-us.md](data-sources-us.md)。

## 高波动

ATR/现价偏大时区间用「带」、确认用分钟收盘；不降低 R1/5x 纪律。
