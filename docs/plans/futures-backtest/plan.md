# 期货回测实施计划

> 设计：[design.md](design.md)

## 0. 决策映射
- D1/D2/D3/D4/D5 → Task 1
- D3/D6 → Task 2
- D6 → Task 3

## Task 1
- create `markets/us/dashboard/futures_backtest.py`、`test_futures_backtest.py`。
- symbols：`normalize_config`、`signal`、`replay`、`compare`、`BacktestJobs`。
- steps：先写会失败的因果与数值测试，再实现策略/费用/账目/任务。
- verify：`python3 -m unittest test_futures_backtest`，expect 通过。

## Task 2
- modify `markets/us/dashboard/app.py:Handler.do_POST/do_GET`、`static/futures.html`、`static/futures.css`。
- create `static/futures-backtest.js`、`test_futures_backtest_page.cjs`；modify `test_futures_routes.py`。
- steps：先测显式启动、参数、错误与竞态；接通独立任务接口和页面。
- verify：后端路由测试、`node --test test_futures_backtest_page.cjs`，expect 通过，不调用模拟账户。

## Task 3
- create `docs/features/futures-backtest.md`，真实样本报告写入 `docs/reports/futures-backtest-2026-11-08/`。
- modify `README.md`、`README.en.md`、`CHANGELOG.md`、`docs/features/domestic-futures.md`、`docs/strategy-policy.md`。
- verify：期货与图表相关Python测试、全部前端测试通过；真实浏览器桌面/390px完成回测；重启8766并回读任务接口；`git diff --check`无输出。
