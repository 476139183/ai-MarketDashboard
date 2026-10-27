# 美股数据来源

仅用于 **美股现货 / ETF / 美股相关永续**。A股见 [data-sources-a.md](data-sources-a.md)。规则见 [market-us.md](market-us.md)。

整理日期：2026-10-26。**尚未绑定唯一生产主源**。现货价 ≠ 永续标记价。

## 选型维度

| 维度 | 说明 |
|---|---|
| 延迟 | 实时 / 约 15 分钟 / EOD |
| 覆盖 | IEX 单交易所 vs SIP 全美综合 |
| 形态 | REST、WebSocket、历史 K（1m/5m/日） |
| 用途 | 盘中信号需分钟 K+近时点价；仅 EOD 不够 |

## A. 美股现货 — 免费 / 低成本

| 源 | 实时性 | 能力与限额（约） | 入口 | 备注 |
|---|---|---|---|---|
| Alpaca Market Data | Basic：IEX 实时 | WS≈30 标的；升 Algo Trader Plus≈$99/月更全 | [文档](https://docs.alpaca.markets/docs/about-market-data-api) | 个人开发友好 |
| Finnhub | 宣传免费美股实时 | ≈60 次/分；WS≈50 | [finnhub.io](https://api.finnhub.io/) | 注意个人/商用条款 |
| Tiingo | 免费档偏历史 | ≈50 次/时 | [pricing](https://www.tiingo.com/about/pricing) | 盘中主源偏弱 |
| Alpha Vantage | 免费紧 | ≈25 次/日 | [alphavantage](https://www.alphavantage.co/premium/) | 不适合多标的轮询 |
| Yahoo Finance Chart | 非官方 | `query1/query2.finance.yahoo.com/v8/finance/chart/{symbol}` | 无 SLA | 回测/备份，不宜单独生产主源 |
| Nasdaq 公开报价 | 页面级 | `https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=stocks` | 衍生 | 看板现价候选；交叉核对 |

## B. 美股现货 — 付费专业

| 源 | 实时性 | 价位量级（约） | 备注 |
|---|---|---|---|
| Polygon.io → Massive | Basic EOD；中档常 15m 延迟；Advanced 实时 | $0 / ~$29 / ~$79 / ~$199 月 | [massive.com/pricing](https://massive.com/pricing) |
| Alpaca Algo Trader Plus | 更接近全市场实时 | ~$99/月 | |
| Nasdaq 官方馈送 | 实时或延迟 | 销售报价 | 个人信号站通常非首选 |

## C. 美股永续 / HIP-3

| 源 | 能力 | 备注 |
|---|---|---|
| Hyperliquid `POST https://api.hyperliquid.xyz/info` | metaAndAssetCtxs、资金费、OI、标记价 | 免 Key；如 `xyz:SNDK` |
| 0xArchive 等封装 | K 线、深度、清算 REST | 第三方；核延迟与符号 |

## D. 基本面

公司 IR、[SEC EDGAR](https://www.sec.gov/edgar/search/)。港股披露易仅港股，不与美股混用。

## 候选组合

| 场景 | 组合 |
|---|---|
| MVP | Alpaca IEX 或 Finnhub + 自算 5m；Yahoo 备份 |
| 认真盘中 | Alpaca Plus 或 Massive Advanced |
| HL 闪迪永续 | Hyperliquid info；现货仅基差对照 |
| 回测 | Yahoo / Tiingo / Massive 历史 |

看板实现暂用 Nasdaq→Yahoo，见 `markets/us/dashboard`。速览副本：`markets/us/docs/market-data-sources.md`。
