# 观市 · Market Studio

面向 A 股与美股的本地网页研究工作台：日 K 与量价趋势、盘中条件信号、手动 AI 分析报告。各市场的规则、时段、数据源和可执行动作独立处理；仓库另保留历史研究脚本。

## 怎么选市场

| 识别线索 | 走哪条线 |
|---|---|
| 6 位数字代码、`SH`/`SZ`/`BJ`、沪深京、涨跌停、T+1 | **A股** → `markets/a` + `market-analysis/references/market-a-share.md` |
| 字母代码（如 `SNDK`/`AAPL`）、Nasdaq/NYSE、盘前盘后、EDT | **美股** → `markets/us` + `market-analysis/references/market-us.md` |
| `BTCUSDT` 等永续/现货币 | 加密 → 技能内加密章节（与 A/美股现货分列） |

技能入口：`market-analysis/SKILL.md`（**先识别市场，再读对应规则**）。

## 网页研究工作台

```bash
git clone https://github.com/476139183/ai-MarketDashboard.git
cd ai-MarketDashboard
bash start-web.sh
```

首次运行会自动准备独立行情服务，随后打开 `http://127.0.0.1:8766/research`。搜索股票名称或代码，在候选列表中选择后确认。支持日 K、自选、MA5/MA20、成交量和桌面/手机布局。AI 方式由用户手动选择 API、Codex CLI 或 Claude Code CLI；只有点击生成报告才调用，任务与报告按个股隔离。详见 [接入与启动说明](docs/dsa-integration.md)。

历史研究已接入 FinMind 日线与公开 OHLCV 月度样本，按少量股票和月份导入，默认下载预算256MiB。网页展开「历史回测样本」可查看实际覆盖和完整交易日；详见 [公开样本接入](docs/public-history.md)。

## 目录

```
market-analysis/          # 统一技能：分流 + 共用契约
markets/
  a/                      # A股：独立研究与历史实验
  us/                     # 网页工作台、双市场规则适配、美股研究与数据工具
```

## 单独启动信号看板

```bash
./start-dashboard.sh          # 启动并打开浏览器
./start-dashboard.sh --live   # 强制刷新行情
./start-dashboard.sh --no-open
```

信号以日线量价判断方向、已收盘 **15 分钟** K 线确认盘中状态，报价和交易限制满足条件后才提供进场计划。
或手动：`cd markets/us/dashboard && python3 app.py` → http://127.0.0.1:8765  
沪深普通 A 股使用独立适配，处理午休、T+1 与涨跌停，默认不做空。历史回放和策略仍处研究阶段，不代表实盘收益。
