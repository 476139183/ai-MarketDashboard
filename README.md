# A股票 — 双市场分析仓库

本仓库同时支持 **A股** 与 **美股**（及加密），但**规则、时段、数据源、可执行动作不可混用**。

## 怎么选市场

| 识别线索 | 走哪条线 |
|---|---|
| 6 位数字代码、`SH`/`SZ`/`BJ`、沪深京、涨跌停、T+1 | **A股** → `markets/a` + `market-analysis/references/market-a-share.md` |
| 字母代码（如 `SNDK`/`AAPL`）、Nasdaq/NYSE、盘前盘后、EDT | **美股** → `markets/us` + `market-analysis/references/market-us.md` |
| `BTCUSDT` 等永续/现货币 | 加密 → 技能内加密章节（与 A/美股现货分列） |

技能入口：`market-analysis/SKILL.md`（**先识别市场，再读对应规则**）。

## 目录

```
market-analysis/          # 统一技能：分流 + 共用契约
markets/
  a/                      # A股：规则说明（看板/回测待建）
  us/                     # 美股：看板、回测、SNDK 工具、美股数据源文档
```

## 快速启动（仅美股看板 · 1m / 5m / 15m）

```bash
./start-dashboard.sh          # 启动并打开浏览器
./start-dashboard.sh --live   # 强制刷新行情
./start-dashboard.sh --no-open
```

看板可切换 **1分钟 / 5分钟 / 15分钟**（ORB≈开盘 30 分钟）。  
或手动：`cd markets/us/dashboard && python3 app.py` → http://127.0.0.1:8765  
**不要**用该引擎分析 A股代码。
