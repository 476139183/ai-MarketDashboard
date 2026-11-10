# 独立引擎回测

入口：[独立引擎研究](http://127.0.0.1:8766/quant-research)。A股和期货模拟页均提供链接。

RQAlpha 6.5.0已通过本地A股与月份期货的实际撮合验证。TqSdk 3.10.4已安装，提供TqSim/TqBacktest入口；用户确认暂不配置账号，因此没有运行天勤服务端回测。两者均不连接实盘，不迁移前向模拟账户。

## 安装及运行

```bash
# 默认使用Python3.12；可用QUANT_PYTHON指定兼容解释器
bash scripts/setup-quant.sh
bash start-web.sh --no-open
```

依赖位于`.venv-quant`，顶层版本见`requirements-quant.txt`，当前验证环境约束见`requirements-quant.lock.txt`。网页主进程不导入RQAlpha或TqSdk。实际研究由固定CLI子进程运行，结果存于`.cache/quant-research/<任务ID>/result.json`，可在网页导出完整JSON。

网页选择市场、框架、策略、成本及期货仓位对照后明确点击运行。GET只读取元数据或结果，不启动回测、不启用模拟账户。输入仅允许预置市场、策略、月份合约和1/2倍成本，不接受自定义代码或文件路径。每次最多一个任务，最长600秒。

## 本地数据与策略

| 市场 | 输入 | 支持研究 |
|---|---|---|
| A股 | 既有`.cache/a-paper-research/sample-latest.json.gz` | 旧版重返MA20、分型v2、扩展候选对照 |
| 期货 | `docs/reports/futures-backtest-2026-11-08`中RB2701、M2701、AU2612、SC2701归档 | SMA20/60、Donchian20/10；固定一手或2ATR风险预算 |

A股实际样本日期以结果为准，目前44票、2026-08-04～11-03。首次没有缓存时，请先在A股模拟页运行历史样本采集。不是获取完整全市场或最新分钟数据的替代品。

A股通过独立RQAlpha订单、资金、持仓、T+1及费用流程执行；策略保留计划内连续两次采样、新信号去重、最多3只、单票2万元、MA10/原保护价退出等语义。开盘采样不暴露当前整根分钟高低收盘；日终收盘单独用于估值，不触发订单。历史证券状态、公司行动、实际盘口与涨跌停价仍未完整恢复，完整分钟筛查也不是时点可知条件。

期货归档指纹先验证再运行。使用前一日完整日线在当日09:00开盘采样，保护只检查开盘跳空及此前收盘触发，不从当日日线高低点假设盘中止损。因此和旧引擎盘中保护假设不同，收益不能直接归因于某个引擎优劣。逐日使用收盘盯市、固定保证金与每手费用，未重建夜盘、交割、涨跌停队列和历史结算参数。

期货风险仓位对照以2ATR×乘数计算每手计划风险，将总计划风险限制为权益0.5%，同时按发单时价格检查50%保证金占用；不足一手直接拒绝。跳空、滑点、费用可使实际损失超预算。它只是一组固定研究定义，不自动成为前向账户默认仓位。

每次结果由框架权益、逐笔费用和未平仓估值核对；0笔交易的胜率为空值，期末不强制卖出。成本与样本局限、实际区间、框架版本、订单成交和拒单一并导出。[30组首轮报告](../reports/quant-engines-2026-11-10/report.md)。

## TqSdk入口

当前无账号时页面显示待配置且禁用运行。未来如需使用，可在本机设置`TQ_USER`、`TQ_PASSWORD`并重启网页进程；不在表单或URL传递凭据。天勤的数据权限取决于账号。

代码仅构造`TqSim`、`TqBacktest`和`TargetPosTask`，没有实盘账户类。首版支持固定手数、原始成本、双均线或通道方向，固定2ATR保护；风险仓位和成本压力对照先使用本地引擎。该分支的联网行为尚未使用真实凭据验收，不能描述为天勤回测已通过。

## 接口

- `GET /api/quant-research`：依赖版本、账号配置与本地样本可用性，以及最近结果。
- `GET /api/quant-research?id=<任务ID>`：内存任务进度及结果。
- `POST /api/quant-research`：允许`engine/market/strategy/symbol/cost_scale/sizing`；同源、JSON及长度检查沿用主服务。

## 参考与许可

- [RQAlpha官方仓库及非商业许可说明](https://github.com/ricequant/rqalpha)：本项目将其作为本机独立研究依赖，商业使用需另行核对授权，不重新分发框架源码。
- [RQAlpha扩展数据接口](https://github.com/ricequant/rqalpha/blob/master/rqalpha/interface.py)。
- [TqSdk官方仓库](https://github.com/shinnytech/tqsdk-python)及[历史回测说明](https://doc.shinnytech.com/tqsdk/latest/usage/backtest.html)。
