# 实现计划
设计依据：[design.md](design.md)
- D1 → Task1；D2/D3 → Task2；D4 → Task2/3。
## Task1 指标到界面
- create: static/technical-indicators.js, test_technical_indicators.cjs
- modify: static/research.html、research.js、research.css，showMetrics/drawCandles/loadDaily
- verify: node --test 指标及research用例；expect固定数值、历史不变、清空和未收盘状态通过。
## Task2 走强筛选与采集
- create: markets/a/strength_radar.py、test_strength_radar.py；后台单任务服务与缓存。
- modify: dashboard/app.py，显式POST与只读GET；复用market_scan.fetch_universe/fetch_recent及指数交易日期。
- verify: unittest规则、API、任务去重、覆盖/日期边界；expect账户无写入。
## Task3 雷达页面与实测
- create: static/strength-radar.js及页面测试；modify a-radar.html/css。
- verify: 匹配项筛选、排序、进度、过热风险、旧快照、390px；所有既有前端及相关Python回归通过。
- 实验：旋极信息300324、联合化学301209与完整缓存截断回放；保留未命中原因，记录真实数据日期，不能将例子用于筛后调参择优。
