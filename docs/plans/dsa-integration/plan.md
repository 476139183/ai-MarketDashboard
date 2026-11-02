# DSA 接入计划

设计依据：[design.md](./design.md)

## 决策映射
- D1 → 1, 4
- D2 → 1, 2
- D3 → 1, 3
- D4 → 1, 3, 4
- D5 → 5
- D6 → 6

## 1. HTTP 客户端
- 新建 markets/us/dashboard/dsa_client.py；统一传输、配置、日线校验与异步任务接口。
- 新建 test_dsa_integration.py；本地 HTTP fixture 验证上游契约。
- 验收：python3 -m unittest discover -s markets/us/dashboard -p 'test_dsa_integration.py'；expect: 全通过。

## 2. 日线趋势
- 修改 engine.py: fetch_chart，在 interval=1d 且配置 DSA 时优先调用。
- 修改 analyze_symbol 输出 daily_source、daily_error、daily_as_of。
- 验收：原日线/趋势回归加 DSA 故障回退测试；expect: 分钟接口及无配置行为不变。

## 3. 页面与本地 API
- 修改 app.py: /research、/api/dsa/config、/api/dsa/history、/api/dsa/analysis、/api/dsa/status。
- 新建 static/research.html、static/research.js；首页增加入口。
- 新建 test_dsa_page.cjs；验证提交、轮询、失败、任务恢复、文本渲染。
- 验收：Python 集成测试 + node --test；expect: 全通过。

## 4. 运行与文档
- 新建 start-dsa.sh、.env.example、docs/dsa-integration.md；更新看板 README 与 CHANGELOG。
- 验收：bash -n start-dsa.sh；expect: 0。
- 尝试独立环境安装和真实上游服务健康/日线联调；模型调用需已有可用配置，未具备时如实记录。

## 5. 网页工作台
- 修改 static/research.html、research.js；新增 research.css、vendor/Lightweight Charts 5.2.1 及许可声明。
- 支持浏览器自选、日期范围、缩放、拖动、均线和成交量；预热历史保留用于计算。
- 验收：真实 NVDA / 600519 接口与 Chromium 桌面/手机截图；expect: 有真实图表、无脚本异常和页面横向溢出。

## 6. 一键运行与真实连接状态
- 新建 start-web.sh、scripts/serve_web.py；修改 app.py、dsa_client.py 返回真实健康/主模型状态。
- 自动安装独立环境并启动服务，复用已运行的本工程服务，不结束占用端口的其他程序。
- 验收：bash start-web.sh --no-open；expect: 健康检查通过、http://127.0.0.1:8766/research 可访问。
