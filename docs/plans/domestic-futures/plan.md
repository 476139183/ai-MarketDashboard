# 国内商品期货实现计划

> 设计依据：[design.md](design.md)

## 0. 决策映射
- D1、D2、D3 → Task 1
- D4 → Task 1、Task 2
- D5 → Task 1、Task 2

## Task 1：数据与只读接口
- create：`markets/us/dashboard/futures_cn.py`，`test_futures_cn.py`，`test_futures_routes.py`
- modify：`markets/us/dashboard/app.py:Handler.do_GET`，导入独立服务。
- symbols：`parse_quotes(text, symbols, now)`、`parse_bars(text, period, now)`、`analyze(bars)`、`FuturesService.snapshot()`、`FuturesService.chart(symbol, period)`。
- steps：先写解析/未收盘/量仓/非法输入/后台缓存测试并见红；实现；接通路由。
- verify：`python3 -m unittest test_futures_cn test_futures_routes`，expect 全通过且不调用股票引擎和模拟账户。

## Task 2：页面与导航
- create：`static/futures.html`、`static/futures.js`、`static/futures.css`、`test_futures_page.cjs`。
- modify：有 workspace-nav 的静态页添加 `/futures` 顶层导航。
- steps：先写异步切换与安全渲染测试；实现行情表/筛选/图表/历史结构卡片；复用 MarketChart。
- verify：`node --test test_futures_page.cjs`，expect 切换清空、错误可见、迟到响应隔离、未知值不伪造。

## Task 3：文档与验证
- modify：`README.md`、`README.en.md`、`CHANGELOG.md`；create `docs/features/domestic-futures.md`。
- verify：新增后端/前端及现有导航、图表路由测试全通过；`git diff --check` 无输出。
- live：抽取4个默认品种及具体月份合约，核对来源戳与K线；不可达或无报价明确记录。
