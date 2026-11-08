# 观市盘中信号看板

支持美股与沪深普通 A 股；以日线量价定方向、已收盘 15m 判断状态和条件进场。A 股独立处理午休、T+1 与涨跌停，不提供做空计划。时间显示北京时间。

新增独立[国内商品期货分析](../../../docs/features/domestic-futures.md)页 `/futures`：12个商品观察池、月份合约查询、日/分钟图与量仓历史统计，不依赖DSA，也不接入模拟账户。

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

## 合约雷达

侧栏「合约雷达」进入 `/contracts`；A 股「明日盯盘」仍为 `/a-radar`。合约 API 为 `/api/contracts?exchange=okx`（默认欧易、优先来源），可切 `exchange=binance` 或 `exchange=bitget`；本机直接读取各所 USDT 永续公开行情，无需账户密钥或 DSA 服务。三源独立采集，不跨交易所回退。

加密币从 6 个扩到 24 个：BTC、ETH、SOL、XRP、DOGE、BNB、ADA、AVAX、LINK、DOT、LTC、BCH、UNI、ATOM、NEAR、APT、ARB、OP、SUI、FIL、AAVE、TRX、ETC、WIF。支持主流、公链、Layer 2、DeFi、Meme、基础设施分类筛选，均研究 USDT 永续。欧易/Bitget 另保留 MU、NVDA、TSLA、SNDK、AMD、AAPL 六个股票合约，合计 30 项；币安雷达只观察 24 个加密币。股票合约使用实际合约价格，不使用原股行情替代。列表来自 `contract_radar.WATCHLIST`；各所未返回行情的合约明确显示不可用。

方向支持 1/5/10/15/30/60 分钟与原默认 4h，基于所选周期已收盘收盘价、EMA20/50 排列与 EMA20 最近 6 根斜率，同向才显示看涨/看跌。支撑与阻力分别参考最近 20 根完整 K 线低点和高点，穿越后不作为当前对应价位。报价超过 30 秒、K 线不足 60 根、不连续或未覆盖最新收盘周期均暂停方向。单票失败保留其余标的；页面请求失败保留上次价格并撤销方向。暂停刷新后仍会检查报价过期。

当前资金费率按接口值转为百分比，非年化、不假定统一结算周期。名义持仓估算为基础币持仓量 × 标记价，非用户仓位。OKX 24h 成交额由基础币成交量 × 当前价估算，页面明确标注；Bitget 使用接口 USDT 成交额。OKX K 线仅保留 confirm=1，基础币量读取 volCcy 而不是合约张数。报价默认每 10 秒刷新（可选 1/5/10/15/30/60 秒），服务端以 10 秒为最短间隔；K 线缓存 60 秒，跨所选周期边界重取。页面请求或模拟交易后台触发采集；首屏显示正在更新后自动查询进度。

数据字段与单位依据 [OKX 官方文档](https://www.okx.com/docs-v5/en/)与 [Bitget 官方合约行情文档](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market)。本模块为规则研究，尚无独立收益回测；不接账户、不下单。测试：`python3 -m unittest discover -s markets/us/dashboard -p 'test_contract*.py'`、`python3 -m unittest discover -s markets/us/dashboard -p test_okx_contracts.py`、`node --test markets/us/dashboard/test_contracts_page.cjs`。

币安使用 [USD-M 官方行情接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)：只纳入在交易的 USDT 加密永续；行情时间取 ticker 的 closeTime 与盘口 time 较早者，缺少盘口时间时不标新鲜。4h K 线检查收盘时间；24h 成交额直接取 quoteVolume，持仓量后台缓存，缺值不伪造。冷扫描超过 15 秒时，完成 K 线后重新取行情再发布；首屏可能需等待数十秒。元数据缓存 1 小时；币安最多 8 路 K 线、4 路持仓量请求。测试：`python3 -m unittest discover -s markets/us/dashboard -p test_binance_contracts.py`。

## 自动模拟交易

入口 `/paper`，默认欧易账户；可在页面选择币安，或使用 `/paper?exchange=binance`。读取 `/api/paper?exchange=okx|binance`，本地控制 `POST /api/paper/control`（如 `{"enabled":true,"exchange":"binance"}`，省略 exchange 时为欧易）。账户初始暂停，启用后按对应交易所雷达自动做多/做空。关闭浏览器后继续，服务重启后恢复启用状态与仓位；没有真实交易接口或 API Key。升级保留原欧易账户，新增币安账户独立初始化为暂停状态，不复制或迁移欧易资金、仓位和历史。

每个账户默认 10,000 USDT 虚拟本金，单笔预算约 1,000，最多 3 仓，1 倍名义敞口。按观察列表顺序占用额度，不做信号间收益择优。欧易张数依据 ctVal / lotSz / minSz 向下取整；币安以基础币为交易数量单位，检查数量步长、最小/最大数量与最小名义金额，不将币数量称作欧易合约张数。只在发现信号之后的新盘口报价模拟成交，每个标的/4h收盘/方向最多开一次。做多卖一进、买一出，做空反之，各边再加 5bp 不利滑点、6bp 手续费（均为假设）。止损 2%、止盈 4%，或完整 4h 方向失效时退出；跳空按当前盘口价，非历史止损价。暂停仅停新开仓，已有仓位仍执行退出规则。

账户、待处理信号和完整交易历史落在被 Git 忽略的 SQLite 中：欧易 `cache/paper-trading.sqlite3`、币安 `cache/paper-trading-binance.sqlite3`。各自事务内统一更新并校验交易所指纹，拒绝跨所行情和账本混用。权益 = 现金 + 冻结名义本金 + 浮盈亏；已实现盈亏已扣两边费用。过期或缺盘口时不撮合，估值标注为历史报价。页面列最近 100 笔平仓，汇总使用全部历史。尚未计入资金费、市场深度/冲击、强平与跨时段拆股处理；按约 10 秒报价采样，可能漏掉采样间触价，服务停机期间不补单。首次试用不要将此记录当作完整交易所撮合或实盘收益。

测试：`python3 -m unittest discover -s markets/us/dashboard -p 'test_paper*.py'`、`node --test markets/us/dashboard/test_paper_page.cjs`。设计与验收范围见 [说明](../../../docs/plans/2026-11-06-auto-paper-trading-design.md)。

## 日 K 与 AI 报告

配置 `DSA_BASE_URL` 后，日线优先使用 Daily Stock Analysis，失败回退原来源；分钟线沿用原链路。首页「日 K 与 AI 报告」进入 `/research`，支持 A 股/美股日 K 展示及手动异步 AI 报告。AI 报告独立于看板策略，不自动推送。安装、模型配置与验证边界见 [接入说明](../../../docs/dsa-integration.md)。

## 说明

- 现价 + 日高/日低：Nasdaq quote（keyStats）；15m：Nasdaq 分时聚合 → Yahoo/缓存
- 正式主源未选：见 `../docs/market-data-sources.md`
- 休市多为观望属正常


## 美股原股雷达与市场账户

`/us-radar` 专门观察美股原股（USD），使用观察列表每页 8 票，也可输入单股代码。六档分钟周期和秒级页面刷新分别配置；时段按纽约盘前 04:00–09:30、盘中 09:30–16:00、盘后 16:00–20:00 切分，自动适配夏令时。节假日与提前收市以源数据为准。

自动模式在配置 `MASSIVE_API_KEY` 时优先使用 Massive 真实分钟 OHLCV，否则常规盘优先新浪分钟 OHLCV，盘前 / 盘后筛选使用 Nasdaq 分时采样。可显式选择 Yahoo/yfinance；分钟源失败回退 Nasdaq，并显示来源和缺失情况。Nasdaq 采样仅画价格线，不伪造成交量或 K 线；趋势保持待确认。Massive 权限和延迟取决于订阅，浏览器不持有 Key。报价与分时图分别标源时间，日期级报价不冒充实时。后端每组查询至少间隔 60 秒。

`/api/us-radar?symbols=NVDA,MU&period=15&session=all&provider=auto`；period 支持 1/5/10/15/30/60，session 支持 all/pre/regular/post，provider 支持 auto/sina/nasdaq/massive/yahoo。10 分钟由完整 5 分钟聚合，60 分钟由完整 30 分钟聚合，按各时段开始时间对齐。

模拟交易 `/paper?market=stock` 是欧易美股合约独立账户，初始暂停、虚拟本金 10,000 USDT。默认 `market=crypto` 保留原账本，历史仓位继续管理；币安只支持 crypto。GET /api/paper 和 POST /api/paper/control 都携带 exchange 与 market，后端拒绝不支持的组合。模拟规则固定 4h，雷达切换周期不改变模拟策略。


## 持续更新的价格与成交量图

合约图与美股图都显示价格及逐根成交量、悬停 OHLCV，形成中的 K 线明确标记。信号及模拟仍只使用收盘线；OKX 的 confirm=0 仅用于形成中图表，不可用于方向判断。合约采集及 K 线缓存约 10 秒更新，所选美股 `/api/us-chart?symbols=SNDK&period=5&provider=auto` 独立每 15 秒采集，仅允许单股。列表仍按 60 秒节流。上述为 REST 轮询，源延迟和订阅权限单独显示。

新浪接口以 K 线结束时间标记，转换为美东周期起点；常规盘量柱单位为股。Nasdaq 源日累计成交量另标日期，与逐根量柱区分。合约量柱为基础币数量。Nasdaq 价格采样无逐根成交量时清空量柱并显示缺失；不填零，不用全日累计量造分钟量。单股 / 来源 / 周期切换清除旧图，持续刷新保留用户缩放范围。

实测 SNDK 5 分钟末根真实成交量为 454,154 股（2026-11-06 15:55 ET 起点），Nasdaq 源日累计量 17,763,666 股；仅为验收快照，不保证后续数据不修订。百度财经公开接口返回 403，未接为可用源。扩展时段真实分钟量仍依赖具备权限的 OHLCV 源。
