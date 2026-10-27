# 行情源目录

完整说明见：

- 共用索引：[market-analysis/references/data-sources.md](../market-analysis/references/data-sources.md)
- 美股专用：[markets/us/docs/market-data-sources.md](../markets/us/docs/market-data-sources.md)
- A股专用：[market-analysis/references/data-sources-a.md](../market-analysis/references/data-sources-a.md)

看板现价优先 Nasdaq；1m/5m/15m 结构优先 Nasdaq 分时聚合，Yahoo/缓存回退。日高/日低用 keyStats 校正分时漏极值。
