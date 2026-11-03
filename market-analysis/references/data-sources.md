# 数据来源与口径（总入口）

正式分析强制联网；每次更新重新获取。优先交易所/监管/发行人原始资料，其次有时间戳行情源，再次可交叉核验聚合源。

**按市场分册（必选其一，勿混用主源）：**

| 市场 | 分册 |
|---|---|
| A股 | [data-sources-a.md](data-sources-a.md) |
| 美股 / 美股永续 | [data-sources-us.md](data-sources-us.md) |
| 加密合约人数比等 | 见下方「合约人数比」；与 A/美股现货独立 |

实现：仓库根目录 `feeds/`（A/美日线级联 + 可选交叉核对；港股暂不纳入）。可选依赖 `requirements-feeds.txt`。

## 查询与证据（共用）

内部保留轻量记录：标的、产品、平台、币种；字段与单位；事件时间（源时区）与**北京时间**；来源 URL；状态（核实/延迟/过期/缺失/冲突）。

新鲜度随问题判断：分钟决策需近时点；延迟 15 分钟不能称即时。财报看最新已披露期。

**当前建议前提：** 产品身份核实 + 符合该市场时段的最新有效报价 + 支撑价位的结构数据。否则只能「等待数据确认」。休市时明确最近收盘与下次重评，不把历史价说成可实时成交。

冲突先核产品、时段、币种、复权、日期、单位；未解决则并列并降低力度。

## 合约人数比入口（加密永续）

整理于 2026-09-28；用前查官方文档。只需公开只读，不需交易密钥。

| 平台 | 路径线索 | 口径注意 |
|---|---|---|
| Binance | `fapi.binance.com/futures/data/globalLongShortAccountRatio` | 全体账户；top* 为另一样本 |
| Bybit | `api.bybit.com/v5/market/account-ratio` | buyRatio/sellRatio |
| OKX | `www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio` | 币种级，核对是否聚合多合约 |
| Bitget | `api.bitget.com/api/v2/mix/market/long-short` | 核实样本定义 |
| Gate | `api.gateio.ws/api/v4/futures/{settle}/contract_stats` | lsr_account / lsr_taker |
| HTX | `api.hbdm.com/linear-swap-api/v1/swap_elite_account_ratio` | 精英账户 |

股票永续另查标的映射与公司行动。CoinGlass 等仅交叉核验。

## 请求失败（共用）

限流按 Retry-After 有界重试；403/451 注明不绕过；空数组不证明未上市。无有效行情时明确无法给出当前交易价位，不把模拟数据包装成建议。
