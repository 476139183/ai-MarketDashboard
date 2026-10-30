# 美股信号看板

**仅美股。** 规则引擎：做多 / 做空 / 观望；关键位 S、E、T1、R1。时间北京时间。A股请勿使用。

## 启动

仓库根目录：

```bash
./start-dashboard.sh          # 推荐：起服务并打开浏览器
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

这些个股已于 **2026-10-30** 通过 [OKX 公开合约接口](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)核验，均存在 `instCategory=3`、`state=live`、`ruleType=normal`、USDT 结算的对应永续合约。该条件只用于筛选票池，看板仍展示 **Nasdaq/Yahoo 美股原股行情**；合约上市状态为核验当日快照，不会自动同步。票池不包含 ETF、非美股交易所标的及 IPO 前合约。

**先趋势、后入场**：上一完整交易日收盘与 MA20 的关系、MA20 近 5 根斜率，确定日线偏向；从常规盘 09:30 起将 15m 合成完整 1h，EMA5/13、斜率和高低点结构连续两根小时线同向，才确认小时方向。日线与小时同向后，15m 同向才生成回踩/反抽计划（盈亏比≥1.5），价格进入入场带才标记到价。方向冲突不反手开仓；缺量、旧报价、过期/不连续 K 线、非美股常规盘均暂停入场。只使用已收盘 K 线；没有交易所节假日日历，假期后可能保守观望。

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

完整模拟成交回放：`python3 markets/us/dashboard/backtest_trend_policy.py`，网页「历史回放」入口为 `/backtest`，逐笔结果写入 `cache/trend-trade-backtest.json`。使用已有分钟 OHLCV，信号后等待 1 分钟、开盘价加不利滑点、入场带与实际盈亏比重新检查，分钟内检查止损/止盈；已知开盘先处理跳空，同分钟先后不明则止损优先，收盘强平。新策略按引擎输出的 `exit_long/exit_short` 处理总体方向失效，旧策略仍按原引擎明确退出提示。网页展示条件式持仓退出提示，巡检保存逐票字段；未读取账户或假定用户实际持仓。

回放按 9 月前/9 月/10 月分段；只在第一段选择 8 组预先限定的参数，再检查后两段及双倍成本。当前证据不足以替换默认 1.5R 参数。详见 [成交回放与微调报告](../../../docs/plans/2026-10-30-trade-replay-report.md)。

逐笔诊断：`python3 markets/us/dashboard/trade_diagnostics.py`，网页 `/diagnostics`。检查入场时间、初始风险/ATR、退出前可确认浮盈浮亏，并在原方向退出入场上固定价格与初始风险比较退出反事实。止损对照同时展示价格净收益及相同入场比较，避免把 R 分母变小或成交样本变化当作盈利改善；分钟入场分为“固定15m计划只检查到价”和“每分钟重算计划”，分别解释。全部是历史诊断，不自动替换线上策略。详见 [逐笔检查结论](../../../docs/plans/2026-10-30-trade-diagnostics.md)。

API：`/api/signals` · `/api/alerts` · `/api/watchlist`

## 说明

- 现价 + 日高/日低：Nasdaq quote（keyStats）；15m：Nasdaq 分时聚合 → Yahoo/缓存
- 正式主源未选：见 `../docs/market-data-sources.md`
- 休市多为观望属正常
