# 观市盘中信号看板

支持美股与沪深普通 A 股；以日线量价定方向、已收盘 15m 判断状态和条件进场。A 股独立处理午休、T+1 与涨跌停，不提供做空计划。时间显示北京时间。

## 启动

仓库根目录：

```bash
bash start-web.sh             # 观市工作台，默认 8766，含日 K 与手动 AI 报告
./start-dashboard.sh          # 单独启动信号看板
./start-dashboard.sh --live   # 强制刷新行情
./start-dashboard.sh --no-open
```

或本目录：

```bash
python3 app.py
SIGNAL_LIVE=1 python3 app.py
```

浏览器：http://127.0.0.1:8765  

默认读 [`watchlist.txt`](watchlist.txt) 票池，共 **82 只美股个股**，涵盖半导体、AI 算力、软件、消费、医疗和金融等。页面每页分析 8 只，使用「上一页 / 下一页」浏览完整票池；编辑标的后刷新会回到第一页。

搜索支持名称或代码，先展示候选列表，选中后点击「确认」切换；搜索失败时保留已有卡片并就近提示。搜索、筛选与刷新独立布局。筛选使用「全部标的 / 仅有效信号」两个固定选项，定时刷新使用开关。翻页显示加载动画与目标页码，等待期间禁止重复点击。切换工作区保留输入、当前页、K 线和 AI 报告；手机也显示两个工作区入口。AI 任务与报告按股票隔离，只有手动生成才调用模型。

这些个股已于 **2026-10-30** 通过 [OKX 公开合约接口](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)核验，均存在 `instCategory=3`、`state=live`、`ruleType=normal`、USDT 结算的对应永续合约。该条件只用于筛选票池，看板仍展示 **Nasdaq/Yahoo 美股原股行情**；合约上市状态为核验当日快照，不会自动同步。票池不包含 ETF、非美股交易所标的及 IPO 前合约。

**先趋势、后入场**：实时路径使用 `market_states.py` 的 `daily-volume-v1`。日线收盘、MA20/60、斜率、结构与成交量确定方向；15m 已收盘结构、EMA5/13、VWAP 和历史同时段量比判断盘中状态。进场还要求新鲜报价、量价一致、最差入场盈亏比≥1.5，以及各市场交易约束。缺量、旧报价、过期或不连续 K 线、非连续交易时段均暂停进场。没有交易所节假日日历，假期后可能保守观望。

美股的总体方向、15m 入场状态与欧易合约价是不同口径，当前仍使用原股行情。历史研究脚本未传入 `trend_context` 时保留旧版方向逻辑，其结果不能当作新策略验收。

**后台巡检**（有信号才 macOS 通知）：

```bash
./start-watch.sh          # 后台
./start-watch.sh --once   # 扫一轮
./start-watch.sh --stop
```

巡检启动即扫描完整票池，最多 4 路并发、单票取数失败继续；常规盘按北京时间 15m 对齐，非活跃时段每 30 分钟、周末每小时运行一次。网页会显示已扫描/票池总数、错误数与更新时间。未勾选网页定时刷新也不影响后台巡检。

告警写入 `cache/alerts.json`；看板可切「仅信号」。开仓告警要求总体方向已确认且当前确实到价，退出提示不受开仓过滤限制。`python3 markets/us/dashboard/signal_watch.py --no-notify` 可只更新网页、不弹系统通知。

验证：`python3 -m unittest discover -s markets/us/dashboard -p 'test_*.py'`、`node --test markets/us/dashboard/test_pagination.cjs`。本地历史方向审计：`python3 markets/us/dashboard/trend_policy_audit.py`，结果写入 `cache/trend-policy-audit.json`；与固定基线 `09a8a1f` 的引擎比较，只衡量方向，不计算收益。详见 [策略验证说明](../../../docs/plans/2026-10-30-trend-first-validation.md)。

旧版策略模拟成交回放（不代表当前 daily-volume-v1 规则）：`python3 markets/us/dashboard/backtest_trend_policy.py`，网页「历史回放」入口为 `/backtest`，逐笔结果写入 `cache/trend-trade-backtest.json`。使用已有分钟 OHLCV，信号后等待 1 分钟、开盘价加不利滑点、入场带与实际盈亏比重新检查，分钟内检查止损/止盈；已知开盘先处理跳空，同分钟先后不明则止损优先，收盘强平。新策略按引擎输出的 `exit_long/exit_short` 处理总体方向失效，旧策略仍按原引擎明确退出提示。网页展示条件式持仓退出提示，巡检保存逐票字段；未读取账户或假定用户实际持仓。

回放按 9 月前/9 月/10 月分段；只在第一段选择 8 组预先限定的参数，再检查后两段及双倍成本。当前证据不足以替换默认 1.5R 参数。详见 [成交回放与微调报告](../../../docs/plans/2026-10-30-trade-replay-report.md)。

逐笔诊断：`python3 markets/us/dashboard/trade_diagnostics.py`，网页 `/diagnostics`。检查入场时间、初始风险/ATR、退出前可确认浮盈浮亏，并在原方向退出入场上固定价格与初始风险比较退出反事实。止损对照同时展示价格净收益及相同入场比较，避免把 R 分母变小或成交样本变化当作盈利改善；分钟入场分为“固定15m计划只检查到价”和“每分钟重算计划”，分别解释。全部是历史诊断，不自动替换线上策略。详见 [逐笔检查结论](../../../docs/plans/2026-10-30-trade-diagnostics.md)。

API：`/api/signals` · `/api/alerts` · `/api/watchlist`

## 日 K 与 AI 报告

配置 `DSA_BASE_URL` 后，日线优先使用 Daily Stock Analysis，失败回退原来源；分钟线沿用原链路。首页「日 K 与 AI 报告」进入 `/research`，支持 A 股/美股日 K 展示及手动异步 AI 报告。AI 报告独立于看板策略，不自动推送。安装、模型配置与验证边界见 [接入说明](../../../docs/dsa-integration.md)。

## 说明

- 现价 + 日高/日低：Nasdaq quote（keyStats）；15m：Nasdaq 分时聚合 → Yahoo/缓存
- 正式主源未选：见 `../docs/market-data-sources.md`
- 休市多为观望属正常
