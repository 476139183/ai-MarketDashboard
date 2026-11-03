# 观市 · Market Studio

面向 **A 股与美股** 的本地网页研究工作台：日 K 与量价趋势、盘中条件信号、盘后「明日盯盘 / 大盘复盘」、手动 AI 分析报告。各市场的规则、时段、数据源和可执行动作独立处理；仓库另保留历史研究脚本。

> 研究与观察工具，不构成投资建议；不自动下单。

## 怎么选市场

| 识别线索 | 走哪条线 |
|---|---|
| 6 位数字代码、`SH`/`SZ`/`BJ`、沪深京、涨跌停、T+1 | **A股** → `markets/a` + `market-analysis/references/market-a-share.md` |
| 字母代码（如 `SNDK`/`AAPL`）、Nasdaq/NYSE、盘前盘后、EDT | **美股** → `markets/us` + `market-analysis/references/market-us.md` |
| `BTCUSDT` 等永续/现货币 | 加密 → 技能内加密章节（与 A/美股现货分列） |

技能入口：`market-analysis/SKILL.md`（**先识别市场，再读对应规则**）。

## 一键启动网页

```bash
git clone https://github.com/476139183/ai-MarketDashboard.git
cd ai-MarketDashboard
bash start-web.sh
```

首次运行会自动准备独立行情服务（DSA），默认打开：

| 页面 | 地址 | 用途 |
|------|------|------|
| 行情研究 | http://127.0.0.1:8766/research | 搜票、日 K、盘中判断、AI 报告 |
| 明日盯盘 | http://127.0.0.1:8766/a-radar | 盘后扫热榜过滤池，次日观察名单 + 点位清单 |
| 盘后复盘 | http://127.0.0.1:8766/a-review | 指数 / 涨跌家数 / 板块冷热 |
| 盘中信号 | http://127.0.0.1:8766/ | 自选票池 15m 条件信号看板 |

也可：`bash start-web.sh --no-open` 只起服务不弹浏览器。

## 能力一览

### 1. 行情研究（双市场）

- 名称 / 代码搜索，候选确认后切换标的
- 日 K、MA5/MA20、成交量；自选保存在浏览器本地
- **盘中判断**：日线量价定方向 → 已收盘 15m 识别状态 → 满足条件才给出进场带 / 止损 / 目标
- 美股支持趋势日早段空（`open_drive` / `rebound_fail`，daily-volume-v2.1）
- A 股处理午休、T+1、涨跌停；现货默认**不做空**；隔夜风险需手动确认
- AI 报告：手动选择 API / Codex CLI / Claude Code CLI，按股票隔离任务

详见 [DSA 接入说明](docs/dsa-integration.md)、[分析规则](market-analysis/references/analysis-rules.md)。

### 2. A 股明日盯盘（盘后 → 次日）

收盘后扫描人气/飙升榜过滤池（剔除 ST、过小市值与过低流动性），按趋势 / 收复均线 / 突破规则分类：

- **策略候选**：趋势信号 + 沪深300站上 MA20 → 次日优先观察
- **观察池**：有信号但大盘过滤未过，或未达趋势门槛 → 仅防守观察

参考 DSA 交易纪律，为每只票生成**确定性作战计划**（不调用大模型）：

| 项目 | 含义 |
|------|------|
| 理想买 ≈ MA5 | 回踩主买区 |
| 次优买 ≈ MA10 | 更深回踩 |
| 止损参考 | MA20 × 0.98 |
| 目标参考 | 近 20 日高点与约 5% 弹性较大者 |
| 检查清单 | 均线多头、乖离&lt;5%（不追高）、量能、结构空间 |

网页支持：盯住（侧栏明日观察 + 同步研究自选）、看盘跳转、全部盯住 / 清空、筛选。  
**盯住不会自动弹开盘提醒**；点位是次日观察预案，不是自动成交。

命令行：

```bash
python3 markets/a/radar.py
# 结果：markets/a/radar-latest.json
```

### 3. A 股盘后复盘

结构化行情摘要（指数、涨跌家数、领涨/领跌板块）与「明天怎么看」提示；不含 AI 长文、不含北向资金。

```bash
python3 markets/a/market_review.py
# 结果：markets/a/market-review-latest.json
```

网页：http://127.0.0.1:8766/a-review

### 4. 历史研究与样本

- A 股日线研究、热榜回放、候选台账：`markets/a/`（见该目录 README）
- 美股公开历史样本（FinMind / 月度 OHLCV）：[公开样本接入](docs/public-history.md)
- 网页「历史回测样本」可查看已导入覆盖

## 单独启动信号看板

```bash
./start-dashboard.sh          # 启动并打开浏览器
./start-dashboard.sh --live   # 强制刷新行情
./start-dashboard.sh --no-open
```

或：`cd markets/us/dashboard && python3 app.py` → http://127.0.0.1:8765

## 目录

```
market-analysis/     # 统一技能：分流 + 共用契约
markets/
  a/                 # A股：明日盯盘、复盘、日线研究与历史实验
  us/                # 网页工作台、双市场规则适配、美股研究与数据工具
docs/                # DSA 接入、公开样本等说明
```

## 数据与边界（请先读）

- 日线多源协作见 `feeds/`（暂仅 A/美）：A 股 `dsa→腾讯→AKShare→BaoStock→Tushare→新浪`，美股 `dsa→Yahoo→yfinance→AV→Finnhub`；可选 `pip install -r requirements-feeds.txt` 与 `.env` 中 Token / `*_DAILY_SOURCES`
- 行情仍可能限流、延迟或缺日线；分钟/报价链路与日线级联独立
- A 股信号日：一般 **15:05 后**可用当日收盘；源未落地时自动回退上一交易日
- 未完整还原除权、停牌、真实涨跌停队列与盘口；回测与候选有前视/幸存者偏差
- AI 与盘中信号、盘后盯盘是不同链路：AI 是辅助解读，执行以规则面板为准

## 测试

```bash
python3 -m unittest discover -s feeds -p 'test_*.py'
python3 -m unittest discover -s markets/a -p 'test_*.py'
cd markets/us/dashboard && python3 -m unittest test_market_states test_trend_policy
```
