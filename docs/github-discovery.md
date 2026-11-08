# GitHub 项目介绍与搜索发现配置

这份文档记录仓库的搜索发现设置，并说明 README 发布和后续公开网站收录的区别。

## 当前状态

2026-11-08 通过 GitHub API 核对 `476139183/ai-MarketDashboard`：仓库公开，默认分支为 `main`，网站地址为空。本次已更新下方 About 描述和 20 个 Topics，并在本次文档改动中补充中英文 README 的标题与常见问题；公开展示以远端默认分支为准。项目自有材料的使用权限仍以根目录 LICENSE 为准，不因搜索优化变更。

README 文件必须发布到访问者查看的分支后才可见；若希望默认仓库首页呈现新文档，需要维护者将对应提交发布到默认分支。Topics 和 About 属于 GitHub 仓库设置，不能仅靠提交 README 自动更新。

## 已配置的 About 描述

当前配置：

```text
观市 Market Studio：Python A股/美股行情看板、选股雷达、K线成交量、合约监控、模拟交易与手动AI分析。Local stock market dashboard, stock screener, crypto futures monitor and paper trading.
```

描述围绕当前已实现的用途，不写“零延迟实时”“稳赚量化”“自动实盘”等未实现或不可保证的能力。

## 已配置的 Topics

下面为本次配置的 20 个主题，使用小写与连字符：

```text
stock-market
stock-analysis
stock-screener
market-data
trading-dashboard
candlestick-chart
technical-analysis
paper-trading
cryptocurrency
perpetual-futures
okx
binance
bitget
a-shares
us-stocks
python
javascript
sqlite
ai-stock-analysis
lightweight-charts
```

GitHub 官方说明指出，Topics 可帮助用户通过主题浏览和搜索找到相关项目；每个仓库最多 20 个 Topics，名称使用小写字母、数字和连字符，长度不超过 50 字符。参见 [Classifying your repository with topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)。

操作位置：仓库首页 → About 旁的设置入口 → 填写 Description 和 Topics → 保存。仅维护者或具有相应仓库权限的用户可修改。

## README 的内容组织

中文主文档面向 A 股、美股与中文用户；英文文档面向 stock market dashboard、candlestick charts、volume analysis、market scanner 和 paper trading 等实际使用场景。两者相互链接，避免在文末堆放与功能无关的关键词。

- 首屏说明项目是什么、适合谁、采用何种运行方式及关键边界。
- 使用真实运行截图和描述性图片替代文本，帮助读者判断界面和功能。
- 优先提供无需模型和交易所账户密钥的轻量启动方式，再介绍完整 DSA 工作台。
- 明确 REST 轮询与数据源延迟、形成中 K 线与已收盘信号、原股与股票合约之间的区别。
- 保留可点击的安装、API、贡献、数据来源和限制说明，避免用未经验证的效果承诺吸引流量。

GitHub 对 README 的说明强调项目用途、价值、上手方式、帮助入口和贡献者，并支持仓库内的相对链接和图片路径，参见 [About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)。

## 发布后核对

1. 从未登录窗口打开仓库默认分支，检查 README、英文入口、图片及章节跳转。
2. 在新目录按 README 命令检出已发布分支，验证轻量启动、健康接口与首次异步采集提示。
3. 核对 About 与 Topics 是否已保存，确保其描述与实际可用功能一致。
4. 更新截图或数据适配时，标注日期与来源；验证记录保留日期，不将历史通过状态当作当前 CI 结果。
5. 需要正式开放修改、分发和复用权限时，由维护者确认合适的项目许可证；当前文档没有代替维护者选择许可证。

## 搜索引擎与后续网站

GitHub 仓库搜索与 Google 等公共搜索引擎是不同入口。README 提供可读、可链接的功能与上手说明；About 和 Topics 用于仓库发现。不要在 README 中添加 HTML meta 标签、隐藏关键词或无关热门主题：仓库页面的 HTML head 由 GitHub 管理。

当前看板在本机 `127.0.0.1` 运行，公共搜索引擎无法访问。若后续需要独立网站，可先发布只展示功能、真实截图、安装说明和仓库链接的静态介绍页，再基于最终公开网址配置独立标题、描述、canonical、站点地图及搜索站长验证。没有公开网址时，不编造 canonical 或 sitemap 地址，也不把本地行情和模拟账户服务直接作为介绍站部署。

内容应围绕实际用途回答用户问题，并通过描述性链接连接安装、数据覆盖和模拟规则。参见 [Google 搜索基础](https://developers.google.com/search/docs/essentials)和 [SEO 入门指南](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)。

这些改进旨在提高项目的可发现性和可理解性，不能保证搜索排名、收录时间或访问量。合并与推送代码到 `main` 不会自动修改 About、Topics 等仓库设置；本次仓库设置已单独通过 GitHub API 更新。
