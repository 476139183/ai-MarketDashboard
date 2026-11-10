# 国内商品期货模拟 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 增加可显式启用、独立记账的国内月份合约前向模拟。

**Architecture:** 新增 futures_paper.py 账户与后台执行器，使用 futures_cn 的真实源数据及 futures_backtest 的期货规则/规格；独立页面、API、SQLite。复用公共壳层，不改变现有加密及美股合约账户。

**Tech Stack:** Python标准库、SQLite、原生JavaScript、现有Playwright验收环境。

### 1. 期货账本与撮合

文件：新增 markets/us/dashboard/futures_paper.py、test_futures_paper.py。
先写多空盈亏、保证金、成本、信号与报价时序、暂停/恢复、错误行情和配置保护失败用例；运行 `python3 -m unittest discover -s markets/us/dashboard -p test_futures_paper.py`，确认缺少模块或行为失败。实现独立SQLite、日盘新鲜报价判定、参数验证、日线信号及ATR保护、待成交与去重状态、后台轮询和恢复。直到上述用例通过。

### 2. 路由与生命周期

文件：修改 markets/us/dashboard/app.py；新增 test_futures_paper_routes.py。
先验证GET不启用、POST同源与参数校验、暂停及配置路由隔离，再实现页面、读取/控制/配置API及服务启动恢复。运行对应路由与现有期货、模拟路由测试。

### 3. 页面与入口

新增 static/futures-paper.html、futures-paper.js、test_futures_paper_page.cjs；修改 paper.html/paper.js 及期货雷达/分析入口。
先写只读加载、明确启用、失败保留运行状态待核实、暂停且空仓才能配置、输入安全展示测试；实现CNY摘要、合约/成本配置、持仓/逐笔、运行边界文案，使用统一菜单与主题。运行 `node --test markets/us/dashboard/test_*.cjs`。

### 4. 集成与文档

完善功能文档、策略适用范围新增明确的模拟接入记录及CHANGELOG。运行期货全部单测和前端回归；在隔离临时服务和临时账本中浏览器验收桌面/手机，核对原有账户与行情快照未被迁移；正式服务重启后验证新账户默认暂停。未经新指示不启用、不提交或推送。
