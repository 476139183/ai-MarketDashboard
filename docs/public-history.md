# 公开历史样本

本模块给离线研究提供可复核的数据入口，独立保存在 `markets/us/.cache/public-history.sqlite3`，不替换实时报价。

| 来源 | 接入能力 | 口径 |
|---|---|---|
| [FinMind](https://finmind.github.io/tutor/UnitedStatesMarket/Technical/) | 美股日线，匿名查询；可选服务端 `FINMIND_TOKEN` | OHLCV 按供应商返回保存，Adj_Close 单独保存，不混合拼接 |
| [公开 OHLCV-1m](https://huggingface.co/datasets/mito0o852/OHLCV-1m) | 固定版本月度 Parquet，匿名下载并筛选股票 | 明确 UTC 时间；复权/交易所量能覆盖仍需独立核验 |

研究页展开「历史回测样本」可查看已导入的股票、真实月份、条数及完整常规盘天数。面板只读本地数据，不自动下载、不调用 AI。状态接口为 `GET /api/history-samples`。

## 少量样本操作

日线不需要额外依赖：

```bash
python3 markets/us/public_history.py daily --symbols AAPL,MSFT,NVDA,MU --start 2010-01-01 --end 2025-12-31
python3 markets/us/public_history.py audit
python3 markets/us/public_history.py daily-study --output .cache/public-history/daily-study.json
```

`daily-study` 沿用当前日线方向规则，仅用当日及以前的完整日线，按股票、年份、方向分别统计后续5/20个交易日价格变化。该统计没有盘中成交、成本或账户收益含义，前瞻窗口也不是独立样本。

Parquet 在单独研究环境运行，不给网页环境增加依赖：

```bash
python3 -m venv .cache/research-venv
.cache/research-venv/bin/python -m pip install -r requirements-research.txt
.cache/research-venv/bin/python markets/us/public_history.py catalog
.cache/research-venv/bin/python markets/us/public_history.py minute --months 1992-01,2010-01 --symbols AAPL,MSFT,IBM,JPM --max-download-mb 256
```

只选两个非连续月份做接入抽样。整批文件先检查预算，默认最多256MiB，不下载全库。已校验的缓存重复使用；按批筛选股票，不把整个库载入内存。需要其他月份时先查看 `catalog` 中的实际大小；不要直接扩大下载范围。

下载绑定仓库提交版本，核对 SHA256 和长度，临时文件失败即清除。每次入库记录版本、文件、实际股票/条数和范围外时段记录。重复导入按股票/时间/来源去重；非法价格、负量、重复分钟或不明时区导致整批事务回滚。范围外时段单独标记 `off_session`，不会作为常规盘回测数据。

## 接入冻结规则回放

```bash
python3 markets/us/dashboard/replay_market_states.py --db markets/us/.cache/public-history.sqlite3 --source hf --start 1992-01-01 --end 2010-01-31 --symbols AAPL,MSFT,IBM,JPM --output markets/us/dashboard/cache/public-history-replay.json
```

保留原 Massive 默认入口；`--source hf` 显式选择公开样本。日线热身由同一来源的完整390分钟交易日合成，不用 FinMind 日线替代。跨片段超过7天重置历史，不能把1992和2010拼成连续均线。缺失分钟不填，完整日线不足60日时明确提示不能评价策略收益。半日市暂保守排除。

首轮采样结果见 [公开样本验收](reports/public-history-samples.md)。这些样本只验证接入与数据质量，没有自动优化或更新线上策略参数。下载文件、数据库、详细研究输出都在忽略目录中，不上传 GitHub。
