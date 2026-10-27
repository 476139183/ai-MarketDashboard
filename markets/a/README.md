# A股市场包

本目录承载 **A股** 专属逻辑与后续工具。规则正文在技能侧：

→ [market-a-share.md](../../market-analysis/references/market-a-share.md)  
→ [data-sources-a.md](../../market-analysis/references/data-sources-a.md)

## 与美股的硬差异（摘要）

| 项 | A股 | 勿套用美股 |
|---|---|---|
| 交易时段 | 北京 9:30–11:30、13:00–15:00（集合竞价另计） | 无美东盘前/盘后等价物 |
| 涨跌幅 | 主板约 ±10%；创业板/科创板约 ±20%；ST 更窄；注册制首日等例外 | 无涨跌停封板逻辑 |
| 交收 | 现货 **T+1**（当日买次日才能卖） | 非 T+0 美股现货 |
| 做空 | 普通账户现货默认**不可做空**；融券/股指期货另标产品 | 不得默认给「做空」现货信号 |
| 数据源 | 交易所/券商/国内行情源（见 data-sources-a） | 禁止把 Nasdaq/Alpaca 当 A股主源 |

## 状态

- 技能规则：已拆分
- 盘中看板 / 回测引擎：尚未实现（勿调用 `markets/us/dashboard`）
