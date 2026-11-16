# 美股多人看板实现计划

> 设计依据：[design.md](./design.md)
> 以下路径为建议的新增结构；实施前确定部署供应商、短信资质及行情授权。

## 0. 决策映射

- D1、D8、D9 → Task 1、4、6
- D2、D6 → Task 2、3
- D3、D4、D5 → Task 1、4、5
- D7 → Task 6

## Task 1：固定多人行情与策略契约

```yaml
decides: [D3, D4, D8, D9]
files:
  create: [server/contracts.py, server/tests/test_contracts.py]
  inspect: [markets/us/dashboard/engine.py, markets/us/periodic_analysis.py, markets/us/research_context.py]
steps:
  - 定义行情、分析、时段、数据质量、首次同向、策略版本和时间字段。
  - 区分实时报价、延迟、失效与历史回放；异常不返回可操作方向。
  - 以假行情验证多空切换、时段与行情时间的处理；记录研究指标适用范围。
verify:
  - command: python3 -m pytest server/tests/test_contracts.py
    expect: 用例通过，过期数据和缺成交量时不会产生可操作信号
risk:
  - 商用展示和派生指标授权未确认时不能接真实行情面向其他用户。
```

## Task 2：验证码注册与登录

```yaml
decides: [D2, D6]
files:
  create: [server/auth.py, server/sms.py, server/tests/test_auth.py]
  modify: [server/contracts.py]
steps:
  - 将短信服务抽象为可替换接口；先接测试桩，随后对接已审核的国内短信服务。
  - 新建验证码发送、校验、会话创建与登出接口；账户由后端自动创建。
  - 配置号码、IP、设备限频和验证码错误次数；防止并发重复消费。
verify:
  - command: python3 -m pytest server/tests/test_auth.py
    expect: 验证码过期、错误、重放、限频、并发消费、登出均通过
risk:
  - 短信签名、模板和主体资质存在审核周期。
```

## Task 3：用户与自选隔离

```yaml
decides: [D2, D6]
files:
  create: [server/models.py, server/watchlists.py, server/tests/test_watchlists.py, server/migrations/]
steps:
  - 建立用户、手机号索引、会话、自选及策略偏好表，默认允许四只预置股票。
  - 实现按当前账户查询与更新自选；做好手机号保护与注销流程。
verify:
  - command: python3 -m pytest server/tests/test_watchlists.py
    expect: 两个账户的数据互不可见，未登录访问被拒绝
risk:
  - 数据迁移和备份须在测试环境演练。
```

## Task 4：集中行情与 15 分钟结构计算

```yaml
decides: [D3, D4, D5, D8]
files:
  create: [server/market_provider.py, server/market_worker.py, server/market_service.py, server/tests/test_market_worker.py]
  inspect: [markets/us/periodic_analysis.py, markets/us/session_replay.py]
steps:
  - 实现授权供应商适配器和固定数据质量契约；测试使用模拟行情。
  - 统一采集股票行情与分钟数据，按交易所日历和 15 分钟结束时间计算结构并缓存。
  - 提供只读查询接口及首次同向方向、强度、时间、参考价；过期不可视为最新。
verify:
  - command: python3 -m pytest server/tests/test_market_worker.py
    expect: 多用户复用采集结果；断线、空数据、乱序、缺 K、跨日、假日和盘前切换处理符合契约
risk:
  - 当前采样方向强度未经胜率校准，需保留研究属性，不能称为收益概率。
```

## Task 5：H5 登录与多股看板

```yaml
decides: [D1, D2, D5, D8]
files:
  create: [web/index.html, web/login.js, web/dashboard.js, web/styles.css, web/tests/]
  inspect: [markets/us/dashboard/static/market_watch.html]
steps:
  - 实现手机号验证码登录与自选股票选择；保持简洁的价格与方向双栏。
  - 读取后端缓存，展示最新行情时间、时段、方向、强度与首个同向参考价。
  - 在过期或缺数据时展示不可用状态；不把研究指标写成收益承诺。
verify:
  - command: npm test --prefix web
    expect: 登录状态、选股隔离、过期提示和手机屏幕交互测试通过
risk:
  - 公开使用范围、行情延迟与展示授权可能改变字段和刷新频率。
```

## Task 6：云端运行与公测

```yaml
decides: [D7, D9]
files:
  create: [deploy/multiuser/Dockerfile, deploy/multiuser/compose.yaml, deploy/multiuser/runbook.md, server/tests/test_end_to_end.py]
steps:
  - 部署到已选云环境，启用 HTTPS、服务端密钥管理、数据库备份和基础监控。
  - 以测试账户验证注册、登录、选股、行情刷新、重启恢复和退出。
  - 对 50 并发用户进行接口和页面压测；完成授权、隐私和短信审核后再开放邀请。
verify:
  - command: python3 -m pytest server/tests/test_end_to_end.py
    expect: 主链路通过；用户资料和后台密钥不可被其他账户读取
  - command: 在测试环境运行压测脚本，记录配置和结果
    expect: 50 并发下缓存接口及页面达到 design §4，未达标记录瓶颈
risk:
  - 正式公网发布、供应商费用和用户协议需要按实际情况单独确认。
```

## 全局验收

- 登录、限频、会话撤销、自选隔离和行情质量测试均通过。
- 跨交易时段及夏令时、休市、断线场景不会将旧价标为实时。
- 真实行情供应商书面确认多人显示与策略计算权限；短信资质、备案及隐私要求由相关服务商或专业人员核实。
- 以测试环境实测注册、登录、四只股票展示、一次 15 分钟更新及重启恢复。
