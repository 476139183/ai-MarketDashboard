# 美股信号看板

**仅美股。** 规则引擎：做多 / 做空 / 观望；关键位 S、E、T1、R1。时间北京时间。A股请勿使用。

## 启动

仓库根目录：

```bash
./start-dashboard.sh          # 推荐：起服务并打开浏览器
./start-dashboard.sh --live   # 强制刷新行情
./start-dashboard.sh --no-open
```

或本目录：

```bash
python3 app.py
SIGNAL_LIVE=1 python3 app.py
```

浏览器：http://127.0.0.1:8765  

默认标的：`SNDK,MU,NVDA`。**策略仅 15m**（ORB≈开盘 30 分钟）。卡片展示实时现价、日高/日低、VWAP、ORB，避免漏判盘中极值。

**定时**：可选，只刷新展示，与信号逻辑无关。

API：`/api/signals?symbols=SNDK`

## 说明

- 现价 + 日高/日低：Nasdaq quote（keyStats）；15m：Nasdaq 分时聚合 → Yahoo/缓存
- 正式主源未选：见 `../docs/market-data-sources.md`
- 休市多为观望属正常
