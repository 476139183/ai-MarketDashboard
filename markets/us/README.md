# 美股市场包

本目录为 **美股**（及美股永续对照）工具与文档。规则正文：

→ [market-us.md](../../market-analysis/references/market-us.md)  
→ [data-sources-us.md](../../market-analysis/references/data-sources-us.md)

| 路径 | 作用 |
|---|---|
| `dashboard/` | 做多/做空/观望看板（默认 SNDK,MU,NVDA） |
| `backtest/` | S→E→T1 回放 |
| `docs/` | 美股行情源速览 |
| `_sndk_stats.py` | SNDK 波动统计脚本 |

```bash
cd dashboard && python3 app.py
# 或
python3 backtest/replay_engine.py
```

交付时间仍用**北京时间**；交易所当地时间仅作括号对照。  
**A股代码不得走本目录引擎。**
