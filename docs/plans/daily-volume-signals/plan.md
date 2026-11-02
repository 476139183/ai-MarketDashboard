# 日线量价与盘中信号实现

设计依据：[design.md](./design.md)

## 决策映射
- D1/D2/D3/D4 → Task 1
- D5 → Task 2, 3
- D6 → Task 1, 4

## Task 1：纯规则和反证场景
- 新建 markets/us/dashboard/market_states.py：daily_context、intraday_context、build_market_context、entry_signal。
- 新建 test_market_states.py：场景、缺失、报价有效期、未来行情不变性、目标受结构约束。
- 验收：python3 -m unittest discover -s markets/us/dashboard -p 'test_market_states.py'；expect: 全部通过。

## Task 2：共享信号链路
- 修改 engine.py: analyze_symbol，接入新规则；保留旧研究函数调用语义。
- 修改 signal_watch.py: compact_alert / is_alertable，传递状态与版本；盘中状态本身不触发进场告警。
- 更新 test_trend_policy.py 的实时链路断言与现有告警回归。
- 验收：全 dashboard 单测及美股顶层单测；expect: 全部通过，旧研究入口保持原行为。

## Task 3：页面
- 修改 static/app.js、index.html：显示日线/盘中/量能与等待条件。
- 修改 static/research.html、research.js、research.css：当前美股盘中判断与手动刷新，A 股不调用美股引擎。
- 添加对应页面行为检查；expect: 无数据、非交易时段不显示进场价格为实时可执行。

## Task 4：历史和实机
- 新建 replay_market_states.py，读取本地 Massive 分钟数据库；冻结规则，输出状态/交易/排除统计至 cache。
- 检查前缀因果性、下一分钟成交、成本及同分钟保守处理。
- 重启当前自建服务，实际请求 NVDA；Chromium 检查桌面/手机与状态边界。
- 同步规则说明、README、CHANGELOG；expect: 现行规则和历史旧报告语义明确。
