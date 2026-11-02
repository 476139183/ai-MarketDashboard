# 日 K 与 AI 报告接入

本地看板通过 HTTP 调用 [Daily Stock Analysis](https://github.com/ZhuLinsen/daily_stock_analysis)。核验版本为 `db0570edd008a319d7eff14eda3bc0ef9da620eb`。无需在看板 Python 环境安装上游依赖。

## 本机启动

在仓库根目录执行一键启动。首次会安装独立环境并启动行情服务，随后打开网页版：

```bash
bash start-web.sh
```

模型和可选新闻检索服务在 `.cache/daily-stock-analysis/.env` 中按上游 `.env.example` 配置。日线请求不调用大模型；AI 报告需要可用的模型配置。不要把密钥放到前端或提交仓库。安装脚本保留已有 `.env`。

默认页面为 `http://127.0.0.1:8766/research`。端口占用时使用 `--port 8767`；自动验收可加 `--no-open`。不会结束占用端口的其他程序；Ctrl+C 只停止本次创建的服务。日志保存在 `.cache/dsa-service.log` 和 `.cache/web-dashboard.log`。

也可分别安装和启动上游服务：

```bash
bash start-dsa.sh --setup
bash start-dsa.sh
```

另一个终端启动网页服务：

```bash
DSA_BASE_URL=http://127.0.0.1:8000 BOARD_PORT=8766 python3 markets/us/dashboard/app.py
```

输入 `NVDA`、`MU` 或 A 股六位代码（如 `600519`）查看行情。页面自动读取默认 NVDA，支持 1 个月 / 3 个月 / 6 个月 / 1 年范围、缩放、拖动、十字光标、MA5/MA20 与成交量。上游会额外返回预热历史，图表只显示所选范围，均线仍用完整历史计算。

自选列表保存在当前浏览器；搜索后可加入或移除自选。桌面为侧栏、图表、报告布局，手机为横向自选栏和纵向内容。图表使用本地打包的 Lightweight Charts 5.2.1，不依赖 CDN；许可、声明位于 `static/vendor/`。

在「AI 方式与配置」中手动选择 API、Codex CLI 或 Claude Code CLI 并保存。不会自动选择本机工具或自动调用模型；仅点击生成按钮才提交分析，按钮标出当前股票代码。API 密钥仅保存在本机服务端。已有任务查询不会重新调用模型。

两个页面均支持名称与代码输入，例如「闪迪」「谷歌」「海力士」「600519」。输入后列出候选股票、代码和市场；选中后点击「确认」才切换标的，不自动识别后跳转，也不显示识别说明。名称、拼音、别名使用 DSA 股票索引，尚未接入的市场禁用。美股中文常用别名另行补充；SKHY ADR 依据 [SK hynix 官方信息](https://www.skhynix.com/ir/UI-FR-IR03/)补入，韩股本体仍单独标识，不混作 A 股。

工作区切换保留两个页面的文档、图表、输入、翻页位置和任务；不会因来回切换重新初始化页面。显式刷新浏览器会重新读取行情。研究页按股票分别保存 AI 任务编号及会话内报告；切到另一只股票立即隐藏旧报告，返回原股恢复。旧版单任务记录自动迁移，查询结果还会核对报告标的。

如已有 DSA 服务，把 `DSA_BASE_URL` 改为实际地址即可，不需要本机安装。地址应为服务根地址，不带 `/api/v1`。如开启 DSA 管理员认证，通过服务端环境变量 `DSA_SESSION_COOKIE` 设置有效的 `dsa_session` 值。页面不接收或返回该凭证。

可将根目录 `.env.example` 复制为 `.env`。`start-web.sh` 只读取其中的 `DSA_BASE_URL`、`DSA_SESSION_COOKIE`，环境变量优先。单独运行 `app.py`、旧 `start-dashboard.sh` 或后台巡检时，仍需按文件头命令手动导入环境。

## 行为

- 配置 DSA 后，美股看板读取日线优先调用 DSA；请求失败、空数据或不合法 OHLCV 时回退原 Yahoo/缓存。返回项含 `daily_source`、`daily_error`、`daily_as_of`；数据不足时原策略继续观望。
- `SIGNAL_CACHE=1` 时不请求 DSA；未配置 DSA 时原日线链路保持原行为。15 分钟行情仍走原有来源。
- 当日日线可在研究页展示，但不参与看板日线趋势/ATR；按美东日期判断，保留现有保守的日线新鲜度检查。
- DSA 日线 API 未提供实际供应商、复权口径及全市场实时保证，因此只能标记来源为 DSA，不能当作分钟源或已校验的历史回测源。
- AI 请求固定 `async_mode=true`、`notify=false`、中文完整报告。不发送飞书、邮件或其他通知，不触发下单，不覆盖规则信号。
- 刷新研究页只恢复上次任务查询。重复分析返回 409 时复用上游已有任务；查询失败后可手动重新查询。报告保存与任务保留周期由 DSA 决定。
- 展示报告生成时间、结论、建议、情绪评分、策略点位、新闻与完整报告数据。报告内容按文本呈现，不执行模型生成的 HTML。

## 接口

| 本地接口 | 上游接口 |
|---|---|
| GET `/api/dsa/search?q=谷歌` | GET `/stocks.index.json`，服务端缓存十分钟并筛选候选项 |
| GET `/api/dsa/history?symbol=NVDA&days=90` | GET `/api/v1/stocks/NVDA/history?period=daily&days=90` |
| POST `/api/dsa/analysis`，JSON `{"symbol":"NVDA"}` | POST `/api/v1/analysis/analyze` |
| GET `/api/dsa/status?task_id=…` | GET `/api/v1/analysis/status/…` |
| GET `/api/dsa/config` | 健康检查和主模型配置可用性，不调用模型、不返回凭证 |

## 验证与限制

```bash
python3 -m unittest discover -s markets/us/dashboard -p 'test_*.py'
node --test markets/us/dashboard/test_dsa_page.cjs markets/us/dashboard/test_pagination.cjs
bash -n start-dsa.sh
```

2026-11-02 已完成真实服务联调：DSA 健康检查通过；NVDA 与 600519 返回日线并在 Chromium 中完成蜡烛、均线、成交量绘制。1440px 桌面及 390px 手机视口通过布局检查。Codex CLI 已生成完整报告；后续切股、切工作区与搜索回归只查询已有任务，没有新增模型调用。实际 SNDK 报告已验证与 NVDA 隔离，返回 SNDK 后恢复。

单元测试替换 HTTP 传输层，保留 Request/JSON 编解码、本地处理器和页面逻辑；真实浏览器检查另行验证服务与行情。截图保存在本地忽略目录 `.cache/`。免费数据源可能断开、限流或延迟，页面保留明确失败状态与重试入口。

停用：移除启动环境中的 `DSA_BASE_URL` 并重启看板/巡检。上游目录、报告与原有缓存保留。
