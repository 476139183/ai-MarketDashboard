# A股强势延续实现计划

依据：[设计](design.md)。按用户本轮“改进”授权在当前会话实施。

## 决策映射
- D1/D2/D3 → Task 1、3
- D4/D5 → Task 1、2
- D6 → Task 2、3、4

## Task 1：纯规则与时间因果测试
- create: `markets/a/continuation.py`、`markets/a/test_continuation.py`
- symbols: `build_plan(rows, benchmark, name)`、`assess(plan, minutes, now)`、`manage(position, minutes, now)`
- verify: `python3 -m unittest discover -s markets/a -p test_continuation.py`；预期无未来数据、准入、放量确认、失效和结构退出用例通过。

## Task 2：撮合纵向接入
- modify: `markets/us/dashboard/a_share_paper.py:approved_plans,daily_rows,fetch_observations,ASharePaperAccount.step,ASharePaperRunner.tick`
- create/modify: 对应独立临时账本测试。
- verify: A股账户原回归与新预算、止损、T+1、小盈不自动卖、重启/去重用例通过。

## Task 3：候选和研究
- modify: `markets/a/radar.py:scan`、`markets/us/dashboard/a_paper_research.py:replay,compare`
- create: 独立历史对照脚本和结果报告。
- verify: 同一数据和成本下比较原分型、延续、双入口、双倍成本；历史截断不改变前段交易。

## Task 4：展示和接入核验
- modify: `markets/us/dashboard/static/a-paper.js`、`markets/us/dashboard/static/a-paper.html`、策略及功能文档。
- verify: Python相关离线回归、Node页面用例、`git diff --check`全通过；启动后只读核对策略修订、心跳、归档与持仓历史。
- 台账：历史盘口与T+1未来收益不可由当日截图验证；不宣称已实盘成交。
