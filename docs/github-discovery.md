# GitHub 项目介绍与搜索发现配置

这份文档供仓库维护者发布时使用。README 的项目说明、截图和中英文术语已准备；下面的仓库设置建议尚未写入 GitHub。

## 当前状态

2026-11-07 只读核对 `476139183/ai-MarketDashboard`：仓库公开，默认分支为 `main`，About 仍为早期股票研究介绍，未设置 Topics，也未声明项目级许可证。新雷达和模拟功能目前在 `feature/us-live-market-data` 分支。

README 文件必须发布到访问者查看的分支后才可见；若希望默认仓库首页呈现新文档，需要维护者将对应提交发布到默认分支。Topics 和 About 属于 GitHub 仓库设置，不能仅靠提交 README 自动更新。

## 建议的 About 描述

可直接复制以下描述：

```text
观市 Market Studio：本地 A股/美股行情看板、K线与成交量、选股雷达、OKX/Binance/Bitget 永续合约监控、独立模拟交易与手动 AI 分析。Local market dashboard for stocks, crypto futures and paper trading.
```

描述围绕当前已实现的用途，不写“零延迟实时”“稳赚量化”“自动实盘”等未实现或不可保证的能力。

## 建议的 Topics

下面为 20 个候选主题，使用小写与连字符；发布时可根据主要受众删减：

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

这些改进旨在提高项目的可发现性和可理解性，不能保证搜索排名、收录时间或访问量。当前没有对仓库设置、默认分支或远端代码执行写入操作。
