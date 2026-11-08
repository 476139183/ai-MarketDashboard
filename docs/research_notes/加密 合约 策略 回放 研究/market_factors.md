# 欧易合约历史因子与成交密集度

## 多空比、主动买卖量与持仓量分别表示什么

### Takeaway

三者不能相互替代：账户多空比描述净多/净空账户数量结构，taker 量描述主动成交方向，OI 描述未平仓规模。多空账户多，不等于该侧名义资金更多，也不是价格下一步方向的证明。

### Cited Findings

- 账户多空比按净多账户数与净空账户数之比展示；官方将其定位为情绪指标。[OKX Account long/short ratio](https://www.okx.com/en-us/help/how-do-i-use-the-account-long-short-ratio-indicator)
- 大户账户比只覆盖持仓价值最大的前 5% 交易者；按账户与按仓位统计是不同接口。[OKX Top account ratio](https://www.okx.com/en-us/help/how-do-i-use-the-top-account-long-short-ratio-indicator)
- OI 表示未平仓合约规模，作为市场活动指标；官方教育说明未赋予其独立预测方向的保证。[OKX Open interest indicator](https://www.okx.com/en-us/help/how-do-i-use-the-open-interest-indicator)
- Taker 买卖量分别反映主动买入及主动卖出订单成交量。[OKX Taker buy/sell volume indicator](https://www.okx.com/en-gb/help/how-do-i-use-the-taker-buy-sell-volume-indicator)

### Inferences

- 合约两侧是配对头寸；账户数量比不等于全市场净名义多空敞口。单个大账户可对应很多小账户，不能用账户数量直接估算资金大小。
- OI 增加说明未平仓量增加，不能仅据此判定新增多头或新增空头；“价格上涨且 OI 增加偏趋势延续”属于待验证的联合解释。
- 建议用 `oiCcy` 或 `oi` 的相对变化衡量参与度，用 `oiUsd` 做规模展示。`oiUsd` 同时受价格变化影响，直接把它的上涨理解为新增仓位会混入价格因子。

### Gaps

- 未找到官方资料证明“多空比高于某固定值便应反向交易”对 BTC、ETH、SOL 有稳定可交易收益。
- 文档与教育文章不构成因子有效性的样本外证据。

## 历史接口、单位、时间戳和可用时刻如何处理

### Takeaway

现有三个合约统计接口可覆盖本次 4h 实验窗口，但接口时间戳不等于数据已向交易者发布的时间。Taker 桶起点已用冻结数据做直接核对；OI 与账户比仍应保守处理。

### Cited Findings

- 官方接口摘要（核对日期 2026-11-07）：[OKX API reference](https://www.okx.com/docs-v5/en/)

| 指标 | `/api/v5/` 后路径 | 返回行 |
|---|---|---|
| 账户比 | `rubik/stat/contracts/long-short-account-ratio-contract` | `[ts,ratio]` |
| 主动量 | `rubik/stat/taker-volume-contract` | `[ts,sellVol,buyVol]` |
| OI | `rubik/stat/contracts/open-interest-history` | `[ts,oi,oiCcy,oiUsd]` |

- 三接口均支持 `instId`、`period=4H`、`begin/end`；每页上限 100，最新可检索 1,440 条。Taker 单位 `0=币、1=张（默认）、2=U`。OI 单位依次为张、币、美元。文档只将 `ts` 标成 Unix 毫秒时间戳。[OKX API reference](https://www.okx.com/docs-v5/en/)
- 本地只读交叉核对：BTC probe 由下列欧易原始请求获得；默认 taker 张数乘本次公开规格 `ctVal=0.01` 后，三个连续桶的买卖量之和分别为 3,229.0345、3,490.7496、4,771.8384 BTC，均与 `[ts,ts+4H)` 内冻结 5m 基础币量之和逐项一致，而不等于前一个 4h 桶。原始缓存为 `/private/tmp/crypto-factor-btc-taker.json`，5m 来源为研究 bundle。这是本次直接数据观测，非官方对所有统计接口的时序承诺。[OKX 原始 taker probe](https://www.okx.com/api/v5/rubik/stat/taker-volume-contract?instId=BTC-USDT-SWAP&period=4H&limit=300&end=1794038400000)

### Inferences

- 本次 BTC taker 样本应按区间起点解释：桶 `ts` 的完整主动量最早在 `ts+4H` 后可知，不能用于 `ts` 开仓。
- 可为全实验预先约定 `available_at=ts+period+5m`。在 4h 信号边界上只采用满足 `available_at <= decision_time` 的最新一条；这通常额外滞后一桶，虽保守但不会因刚闭合桶的发布延迟轻易偷看未来。
- 对 OI、账户比同样采用该延迟，是研究端保守假设，不应写成交易所官方起点语义。后续可用实时原始捕获估计实际发布时间。
- 每个下载序列应验证期距、覆盖、唯一时间戳、有限数值、非负量，缓存原始响应与 SHA-256；不得用现在的 OI 或比率回填历史。Taker 推荐显式 `unit=0`，并对买卖总量与同桶 K 线量做审计。
- 如果某币长短比缺失，主试验应停用该因子或将信号标无数据；不能替换成另一交易所的数据却继续称同一欧易模型。

### Gaps

- 官方统计接口没有给出明确发布 SLA，也未说明历史值是否修订；历史 REST 值不能证明当时毫秒级可用。
- 以上桶起点实证仅覆盖 BTC 的三个样本，尚未对 ETH、SOL 或 OI/ratio 逐项确认。

## 为什么历史 taker 总量与 K 线量有差异

### Takeaway

最近三个 BTC 桶精确相等不能推广到全部历史。完整下载后的较早样本出现差异，应保留两个独立源并报告差异；目前没有官方证据能够解释本次差异成因。

### Cited Findings

- OKX 明确 RPI 成交纳入最后成交价和 K 线数据。[OKX Retail Price Improvement Program](https://www.okx.com/en-gb/help/okx-retail-price-improvement-program-rpi)
- 最新成交及历史成交接口提供 `source=0` 普通、`source=1` RPI 的来源字段；公开大宗成交通道单独提供数据，注明执行后 15 分钟发布。但 `taker-volume-contract` 对 RPI、大宗、强平等成交的纳入范围及历史修订规则没有明确说明。前两项不能反向证明 taker 统计排除了这些成交。[OKX API reference](https://www.okx.com/docs-v5/en/)
- 本次完整下载后由主研究程序观测：BTC `ts=1785484800000` 的 `unit=0` 主动买卖总量为 12,646.2651 BTC，同桶 48 条 5m 基础币量为 12,715.6017 BTC；后续两桶分别为 38,145.4454 对 38,192.5444、10,677.2369 对 10,831.5725。缓存为 `BTC-taker-4H-base-1785456000000-1794038400000.json` 及对应冻结 5m 历史。这是本地观测，不是官方差异解释。

### Inferences

- 逐桶记录 `relative_gap=(takerBuy+takerSell-candleBaseVolume)/candleBaseVolume`、绝对差、差异桶数量、最大值及分位数；零成交桶单独处理。将精确相等和近似相等分开统计，披露阈值。
- 单位、同合约、桶对齐、完整 48 条 5m、重复和缺失应分别严格验证；这些结构检查通过后，总量差异记为数据审计警告，不直接判定 taker 因子无效或整个实验失败。
- 主动买卖失衡仍使用原始独立 taker 序列；价格成交量分布仍使用冻结 K 线基础币量。不要按 K 线总量缩放主动量、删除差异桶或用 K 线涨跌量替换，制造两个来源完全一致的假象。
- RPI 范围、不同聚合链路、后续修订等只能作为向交易所询问的可能检查方向；没有文档或逐笔对账证据之前，不写成差异的实际原因。当前已统一 `unit=0`，不需要用今天的合约面值反算旧主动量。

### Gaps

- 本轮定向核对未找到官方公布的 taker 与 K 线总量恒等保证、差异解释或历史修订政策。无法断言旧数据较少是 RPI、大宗或强平成交被排除造成的。

## 如何因果使用资金费并估算成本

### Takeaway

历史已实现资金费可用于结算费用回放；把某次未来结算的实际费率提前用作入场指标则会泄漏未来。方向因子应只用决策前已经结算、并留出发布缓冲的费率。

### Cited Findings

- `fundingRate` 是预测值，`realizedRate` 是实际历史费率，结算间隔可能变化，历史接口使用 `fundingTime`。[OKX API reference](https://www.okx.com/docs-v5/en/)
- 资金费为持仓价值乘费率；正费率多付空收、负费率相反。实际结算评估可能持续约一分钟，因此恰在名义结算点开平仓仍有时序差异。[OKX Funding fee mechanism](https://www.okx.com/en-gb/help/funding-fee-mechanism-eea)
- USDT 永续名义价值使用合约数量、面值、乘数和标记价格；资金费在持仓双方间结算。[OKX Perpetual funding fee mechanism](https://www.okx.com/en-sg/help/perps-funding-fee-mechanism)

### Inferences

- 费用回放：`fee = side × base_quantity × settlement_mark × realized_rate`，其中多头 `side=+1`、空头 `-1`，正值支出。历史 5m 标记开盘只是结算标记价格的近似。
- 因子回放：仅选择 `fundingTime + 5m <= decision_time` 的最近已结算值；不将本次结算后的 `realizedRate` 前置给结算前的交易。
- 20% 保证金、10x 下，费率和手续费作用在约 2 倍账户净值的单币名义仓位上，不是只作用于保证金；应逐笔按净值重新定仓。
- 在资金费结算点退出的旧仓，是否参与实际评估无法用 5m K 线精确重建。应披露该边界近似，并保持成本敏感性对照。

### Gaps

- 当前冻结数据没有每次资金费实际评估毫秒时间及完整实时预测序列，不能把结算前预测资金费的策略当作已验证。

## 交易密集度与成交密集区怎么定义才可回测

### Takeaway

“交易密集”至少有三种不同含义：时间上的成交活跃度、价格上的成交量分布、真实逐笔成交频率。现有 5m OHLCV 可近似前两者，不能直接还原逐笔成交次数、真实买卖方向或订单簿深度。

### Cited Findings

- TradingView 的 Volume Profile 使用更低周期数据计算；加密资产可使用基础币或计价币成交量。其上涨/下跌量按低周期价格方向分配，不等于真实主动买卖量。[TradingView Volume Profile basic concepts](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/)
- POC 是指定区间成交量最大的价格层；价值区通常取指定比例的成交量，属于历史活动描述。[TradingView Volume Profile basic concepts](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/)
- 低周期选择会改变计算精度与历史覆盖，固定区间工具按低周期数据量选择分辨率。[TradingView Fixed Range Volume Profile](https://www.tradingview.com/support/solutions/43000480324-fixed-range-volume-profile-indicator/)

### Inferences

- 时间活跃度：`RV_t = V_t / median(V_{t-20:t-1})`，不把当前量混入基准；可另记录 `turnover_t = (takerBuy+takerSell)/meanOI` 作为每期存量周转的代理，不称为成交笔数。
- 价格密集区：用决策前 7 天已闭合 5m 的典型价格 `(O+H+L+C)/4` 将基础币量分配到固定数量的价格桶，最大桶中心作为“5m 量价分布近似 POC”。该分配法是研究近似，不是 TradingView 精确算法或逐笔真实 Volume Profile。
- 用 25/50/100 桶做稳定性检查，固定区间和价格分配法；不能在看完收益后挑一个最漂亮的桶数。
- 不要把 `close>=open` 的 K 线量称为主动买量。已有 taker endpoint 时，应使用实际 taker 数据，另把价格分桶仅用于密集区参考。

### Gaps

- 缺少整月逐笔成交、完整订单簿与历史撤单，无法精确验证真实成交笔数密度、挂单墙、吸收/撤单或可成交深度。

## 可预先声明的因子规则与验证方式

### Takeaway

这些指标适合当作可审计的参与度或拥挤度过滤器；它们是否改善收益，必须由固定规则和独立时段检验，不能从指标名称推导出来。

### Cited Findings

- 官方将账户比、OI、taker 分别描述为情绪、活动规模和主动成交量指标，而不是保证获利的机械买卖规则。[OKX account ratio](https://www.okx.com/en-us/help/how-do-i-use-the-account-long-short-ratio-indicator)；[OKX OI](https://www.okx.com/en-us/help/how-do-i-use-the-open-interest-indicator)；[OKX taker volume](https://www.okx.com/en-gb/help/how-do-i-use-the-taker-buy-sell-volume-indicator)

### Inferences

- 可先固定一个不调参的筛查组合：4h/daily 给方向 `s∈{-1,+1}`；实际主动量差 `D=(buy-sell)/(buy+sell)`；要求 `sD>=0.05`（量比约 1.105）作为参与确认。阈值仅是预声明实验值，没有文献保证。
- OI 变化：`ΔOI=(oiCcy_t/oiCcy_{t-1}-1)>0` 可为“新参与”加分，而不单独决定多空。必须使用过去可用期。
- 拥挤度否决：当账户比达到其过去 7 日 80% 分位且前次已结算资金费为正，避免再追多；当达到过去 7 日 20% 分位且资金费为负，避免再追空。历史分位不含当前值。这是待检验假说，不能解释成所有高/低多空比都应反着开仓。
- 密集区确认：`s×(last_closed_price−approx_POC)>0` 可作为结构加分，不把近似 POC 直接等同于一定能守住的支撑阻力。
- 需要消融对照：原策略、只加 orderflow、只加 OI、只加 crowding、只加 volume profile、组合版；成本/仓位/退出规则保持一致。记录信号被剔除的原因、样本数和可用性，不能在组合筛到零笔时宣称“无回撤所以有效”。
- 决策前预声明阈值、窗口、时间延迟和实验总数；各币及各时间段分别报告，已查看过的最近月只能称回看检验，不能称完全未触碰样本外。

### Gaps

- 尚无本研究的因子增量收益结果；本文件只定义可测假说，不能写“多因子一定优于均线”。
