# 美股信号看板

**仅美股。** 规则引擎：做多 / 做空 / 观望；关键位 S、E、T1、R1、5x 压测。时间北京时间。A股请勿使用。

## 启动

仓库根目录：

```bash
./start-dashboard.sh          # 推荐：起服务并打开浏览器
./start-dashboard.sh --live   # 强制刷新 15m/行情
./start-dashboard.sh --no-open
```

或本目录：

```bash
python3 app.py
SIGNAL_LIVE=1 python3 app.py
```

浏览器：http://127.0.0.1:8765  

默认标的：`SNDK,MU,NVDA`（15m 短线趋势）。约每 60 秒刷新。

## 说明

- 现价：Nasdaq quote → Yahoo；15m 优先 Nasdaq 分时
- 正式主源未选：见 `../docs/market-data-sources.md`
- 5x 仅压测；休市多为观望属正常
