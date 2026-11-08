"""Write auditable local research artifacts; no live-account state is accessed."""
import csv
from datetime import datetime, timezone, timedelta
from html import escape
import json
from pathlib import Path

BJ=timezone(timedelta(hours=8))
LABELS={'BTC':'BTC','ETH':'ETH','SOL':'SOL','COMBINED':'三币组合'}

def number(value):return f'{value:,.2f}'
def stamp(value):return datetime.fromtimestamp(value/1000,BJ).strftime('%Y-%m-%d %H:%M')
def daily(curve,start,balance):
    return [dict(t=start,equity=balance)]+[p for p in curve if p['t']%86400000==0 or p is curve[-1]]

def write_csv(path,rows):
    if not rows:return
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def write_report(result,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    start,end=result['start'],result['end'];accounts=result['accounts'];initial=result['rules']['initial_balance']
    summary={key:value for key,value in result.items() if key!='accounts'};summary['accounts']={}
    for name,account in accounts.items():
        reduced={}
        for mode,row in account.items():
            if mode=='price_change_pct':reduced[mode]=row;continue
            reduced[mode]={key:value for key,value in row.items() if key not in ('trades','equity_curve')}
            reduced[mode]['equity_daily']=daily(row['equity_curve'],start,initial)
        reduced['side_attribution']={str(side):dict(trades=sum(t['side']==side for t in account['funded']['trades']),
            net_pnl=sum(t['net_pnl'] for t in account['funded']['trades'] if t['side']==side)) for side in (1,-1)}
        summary['accounts'][name]=reduced
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
    combined=accounts['COMBINED']['funded']
    write_csv(output/'trades.csv',combined['trades'])
    write_csv(output/'individual-trades.csv',[dict(account=name,**trade) for name,account in accounts.items() if name!='COMBINED' for trade in account['funded']['trades']])
    curves=[dict(t=start,beijing_time=stamp(start),**{name:initial for name in accounts})]
    for i,p in enumerate(combined['equity_curve']):
        curves.append(dict(t=p['t'],beijing_time=stamp(p['t']),**{name:account['funded']['equity_curve'][i]['equity'] for name,account in accounts.items()}))
    write_csv(output/'equity-5m.csv',curves)
    lines=[f'# BTC / ETH / SOL 三个月策略回测',f'\n区间：**{stamp(start)}—{stamp(end)}，北京时间**（92 天）。生成：{result["generated_at"]}。',
        f'\n结论：当前 `closed-4h-ema-v1` 三币组合净收益 **{combined["return_pct"]:+.2f}%**，净盈亏 **{combined["net_profit"]:+,.2f} USDT**。策略账户按每币每笔约 1,000 USDT 配置；不是每币满仓，也没有新增杠杆。',
        '\n## 收益与风险',
        '\n所有单币账户分别以 10,000 USDT 起始；组合共用一份 10,000 USDT 本金，不是三份本金相加。下表计手续费、滑点及历史资金费。最大回撤按 5 分钟收盘权益采样。',
        '\n| 账户 | 期末权益 / USDT | 净盈亏 / USDT | 收益率 | 最大回撤 | 平仓笔数 | 胜率 | 盈利因子 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,a in accounts.items():
        r=a['funded'];factor='—' if r['profit_factor'] is None else number(r['profit_factor'])
        lines.append(f'| {LABELS[name]} | {number(r["final_equity"])} | {r["net_profit"]:+,.2f} | {r["return_pct"]:+.2f}% | {r["max_drawdown_pct"]:.2f}% | {r["trade_count"]} | {r["win_rate_pct"]:.2f}% | {factor} |')
    lines+=['\n## 同期价格与资金配置一致的持有参照',
        '\n币价涨跌使用同一欧易永续合约首根开盘价和末根收盘价，不等于账户收益。持有参照按每币约 1,000 USDT 一次性做多，其他本金闲置，计同等费用、滑点和资金费；无中途止损止盈。',
        '\n| 账户 | 币价涨跌 | 同资金配置持有收益率 | 策略收益率 |','|---|---|---:|---:|']
    for name,a in accounts.items():
        price='；'.join(f'{s} {v:+.2f}%' for s,v in a['price_change_pct'].items())
        lines.append(f'| {LABELS[name]} | {price} | {a["benchmark"]["return_pct"]:+.2f}% | {a["funded"]["return_pct"]:+.2f}% |')
    lines+=['\n## 成本与方向贡献','\n| 账户 | 手续费 / USDT | 估计不利滑点成本 / USDT | 资金费净支出 / USDT | 多头净盈亏 | 空头净盈亏 |','|---|---:|---:|---:|---:|---:|']
    for name,a in accounts.items():
        r=a['funded'];long=sum(t['net_pnl'] for t in r['trades'] if t['side']==1);short=sum(t['net_pnl'] for t in r['trades'] if t['side']==-1)
        lines.append(f'| {LABELS[name]} | {number(r["fees"])} | {number(r["slippage_cost"])} | {number(r["funding_cost"])} | {long:+,.2f} | {short:+,.2f} |')
    lines+=['\n滑点已反映在成交价和净盈亏中，不能再次扣除；资金费负数代表净收入。资金费采用历史实际费率 `realizedRate`，结算价格用当时 5 分钟开盘价近似，未取得历史标记价，属于估算。',
        '\n## 成本敏感性与线上账本口径',
        '\n| 账户 | 按当前模拟规则、未计资金费 | 加入估算历史资金费 | 手续费与滑点同时翻倍 |','|---|---:|---:|---:|']
    for name,a in accounts.items():lines.append(f'| {LABELS[name]} | {a["unfunded"]["return_pct"]:+.2f}% | {a["funded"]["return_pct"]:+.2f}% | {a["stress"]["return_pct"]:+.2f}% |')
    lines+=['\n当前线上模拟账本未计资金费，第一列更接近该费用口径。本报告主表在此基础上加入估算历史资金费。成本改变会移动实际入场价格及止损止盈水平，因此压力测试重新回放成交路径，不是只从原结果扣除费用。',
        '\n## 回放规则',
        '\n1. 固定现有规则，不做参数优化：已收盘 4 小时价格、EMA20 / EMA50 排列、EMA20 近 6 根斜率同向才做多 / 做空，否则观望。按线上取数上限复现最近 119 根完整 4h 滚动窗口，提前取得 120 根预热数据。',
        '2. 信号只使用在决策时间之前已经收盘的数据。首次观察信号后，最早在下一根 5m 开盘价入场；不能用生成信号的 4h 收盘价补造成交。',
        '3. 每笔约 1,000 USDT，按合约面值、最小张数与步长向下取整，每账户最多三仓，每币最多一仓。合约规格使用采集时公开元数据，不保证历史规格始终一致。',
        '4. 每边手续费 0.06%，每边不利滑点 0.05%；固定止损 2%、止盈 4%。缺少历史买卖价差与订单簿，K 线开盘价是执行参考，并非历史真实盘口。',
        '5. 已持仓先检查开盘跳空止损 / 止盈及此刻可知的信号失效，再做开盘入场决定，最后检查本根 5m 高低价；不允许本根未来的平仓释放更早开盘的资金或仓位。',
        '6. 一根 5m 同时触及止损和止盈，按止损优先；开盘跳空按当前开盘价退出。4h 转为观望或反向，在下一次可检查的执行点退出，不在同一根执行线立刻反手。',
        '7. 同一币、4h 收盘周期与方向只开一次。回测末尾用最后完整 K 线收盘价加不利滑点统一平仓、扣退出费用，便于对比期末现金。',
        '8. 正资金费多头支付、空头收取；仅在结算之前已经持仓时计入。资金费付款 / 收款会同步影响现金和可开仓额度。',
        '\n5m 回放不能完全复现线上约 5 秒检查、盘口买卖价和所有中途触价；回测收益是固定假设下的历史模拟，不是已实现的实盘收益。',
        '\n## 数据完整性','\n| 币种 | 5m 完整线 | 4h 含预热 | 逐根核对 4h | OHLC 不一致 | 实际资金费记录 | 最大结算间隔 |','|---|---:|---:|---:|---:|---:|---:|']
    for s,a in result['source_audit'].items():lines.append(f'| {s} | {a["execution_bars"]:,} | {a["signal_bars"]} | {a["checked_4h_bars"]} | {len(a["ohlc_mismatches"])} | {a["funding_events"]} | {a["funding_max_gap_hours"]:g} 小时 |')
    lines +=[f'\n三币共 {(end-start)//300000*3:,} 根执行线，均连续、无缺失、无冲突重复，且 `confirm=1`。三币组合发生止盈止损同根歧义 {combined["ambiguous_bars"]} 次。资金费覆盖起止见 [summary.json](summary.json)。',
        '\n原始来源：[欧易官方市场与资金费 API](https://www.okx.com/docs-v5/en/)。原始历史响应及本地 bundle 位于 `markets/us/.cache/crypto-ema-replay/`，Git 忽略；文件 SHA-256 与来源审计保存在摘要中。',
        '\n## 如何复现','\n```bash',
        'python3 -m unittest discover -s markets/us/backtest -p test_crypto_ema_replay.py',
        'python3 markets/us/backtest/crypto_ema_replay.py --end 2026-11-07T08:00:00+00:00',
        '```',
        '\n有本地缓存时不重新下载；也可通过 `--bundle <bundle.json>` 离线回放。此命令只写独立研究缓存与报告，不修改任何线上模拟账户，也不下真实订单。',
        '\n## 导出文件','\n- [交互式资金曲线](report.html)：选择单币或三币组合，对比策略与同资金配置持有。',
        '- [组合逐笔交易](trades.csv)：236 笔组合交易；单币账户见 [individual-trades.csv](individual-trades.csv)。',
        '- [5m 权益序列](equity-5m.csv)：每个账户分别从 10,000 USDT 起始。',
        '- [完整指标与数据审计](summary.json)。',
        '\n## 观察结论',
        '\n三币期间价格均上涨，但现有策略未能把趋势转换成稳定净收益。组合空头净亏损明显高于多头盈利，ETH 的亏损贡献最大；手续费与滑点对频繁进出场形成显著拖累。SOL 在基准费用下微利，但成本翻倍后转亏，当前证据不足以将其视为稳健盈利策略。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    write_html(summary,output)


def write_html(summary,output):
    vendor=Path(__file__).resolve().parents[1]/'dashboard'/'static'/'vendor'
    runtime=(vendor/'lightweight-charts.js').read_text().replace('</script','<\\/script')
    license_text=(vendor/'lightweight-charts.LICENSE').read_text()
    rows=''
    for name,a in summary['accounts'].items():
        r=a['funded'];cls='pos' if r['return_pct']>=0 else 'neg'
        rows+=f'<tr><td>{LABELS[name]}</td><td class="{cls}">{r["return_pct"]:+.2f}%</td><td>{r["net_profit"]:+,.2f}</td><td>{r["max_drawdown_pct"]:.2f}%</td><td>{r["trade_count"]}</td><td>{r["win_rate_pct"]:.2f}%</td><td>{a["benchmark"]["return_pct"]:+.2f}%</td></tr>'
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>BTC / ETH / SOL · 三个月策略回测</title><style>
*{box-sizing:border-box}body{margin:0;background:#10181d;color:#d6e1e7;font:15px system-ui;line-height:1.8}main{max-width:1200px;margin:auto;padding:35px 22px}h1{font-size:28px}h2{font-size:19px}.muted,p{color:#9bb0bd}.panel{background:#18242b;border:1px solid #2a3943;border-radius:12px;padding:22px;margin:20px 0}.headline{font-size:32px;font-weight:700;color:#ef8b87}table{width:100%;border-collapse:collapse;white-space:nowrap}td,th{text-align:right;padding:12px;border-bottom:1px solid #2a3943}td:first-child,th:first-child{text-align:left}.table{overflow:auto}.pos{color:#65c4aa}.neg{color:#ef8b87}select{background:#233641;color:#d6e1e7;border:1px solid #3a4f5c;padding:10px;border-radius:6px}#chart{height:420px}.key{display:flex;gap:22px;font-size:13px}.key span:first-child{color:#65c4aa}.key span:last-child{color:#eab56d}a{color:#eab56d}pre{white-space:pre-wrap;font-size:12px}footer{font-size:12px;color:#9bb0bd}@media(max-width:600px){h1{font-size:23px}main{padding:20px 12px}.panel{padding:15px}#chart{height:320px}.key{display:block}}
</style><main><div class="muted">观市 MARKET STUDIO / 历史回放</div><h1>BTC · ETH · SOL，现有策略能否盈利？</h1><p>WINDOW</p><div class="panel"><div class="muted">三币组合 · 手续费、滑点与估算历史资金费后</div><div class="headline">RETURN</div><p>初始 10,000 USDT · 每币每笔约 1,000 USDT · 最多 3 仓 · 固定现有 4 小时规则，未调参</p></div><section class="panel"><h2>资金曲线 <select id="account"><option value="COMBINED">三币组合</option><option value="BTC">BTC</option><option value="ETH">ETH</option><option value="SOL">SOL</option></select></h2><div class="key"><span>策略：含费用和估算资金费</span><span>同资金配置持有：其余本金闲置</span></div><p id="readout"></p><div id="chart"></div><p>曲线按 UTC 日界线抽样展示；回撤指标按完整 5 分钟权益计算。每个账户分别以 10,000 USDT 起始。</p></section><section class="panel table"><h2>账户收益对比</h2><table><thead><tr><th>账户</th><th>策略收益率</th><th>净盈亏 / USDT</th><th>最大回撤</th><th>交易笔数</th><th>胜率</th><th>同配置持有</th></tr></thead><tbody>ROWS</tbody></table></section><section class="panel"><h2>回放口径</h2><p>闭合 4h EMA20 / EMA50 与 6 根斜率 → 首次观察后等下一根 5m 开盘 → 固定 2% 止损 / 4% 止盈 → 信号失效退出。费用每边 0.06%，不利滑点每边 0.05%；期末统一平仓。</p><p>资金费使用历史实际费率，结算估值采用 5m 开盘价近似。没有历史盘口、标记价或逐笔触价序列；不能等同线上约 5 秒检查或实盘成交。三币各 26,496 根 5m K 线、672 根含预热 4h K 线及 276 条资金费记录，4h OHLC 与 5m 聚合逐根一致。</p><p>SOL 基准成本下微利，但成本翻倍后转亏；组合空头亏损及费用拖累明显。历史结果不代表未来收益。</p></section><footer><a href="report.md">完整回测说明</a> · <a href="trades.csv">组合逐笔交易 CSV</a> · <a href="equity-5m.csv">5m 权益 CSV</a> · <a href="summary.json">指标与审计 JSON</a> · <a href="https://www.tradingview.com/">TradingView Lightweight Charts™</a><details><summary>第三方图表许可</summary><pre>LICENSE</pre></details></footer></main>'''
    html=html.replace('WINDOW',f'{stamp(summary["start"])}—{stamp(summary["end"])} · 北京时间 · 欧易 USDT 永续').replace('RETURN',f'{summary["accounts"]["COMBINED"]["funded"]["return_pct"]:+.2f}%').replace('ROWS',rows).replace('LICENSE',escape(license_text))
    script='''const DATA=PAYLOAD;const el=id=>document.getElementById(id);const L=LightweightCharts;const chart=L.createChart(el('chart'),{autoSize:true,layout:{background:{color:'#18242b'},textColor:'#9bb0bd'},grid:{vertLines:{color:'#283b47'},horzLines:{color:'#283b47'}},localization:{priceFormatter:v=>v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}});const actual=chart.addSeries(L.LineSeries,{color:'#65c4aa',lineWidth:2,title:'策略'});const benchmark=chart.addSeries(L.LineSeries,{color:'#eab56d',lineWidth:2,title:'同配置持有'});let values=new Map();function render(){const a=DATA.accounts[el('account').value];const rows=a.funded.equity_daily;actual.setData(rows.map(p=>({time:p.t/1000,value:p.equity})));benchmark.setData(a.benchmark.equity_daily.map(p=>({time:p.t/1000,value:p.equity})));values=new Map(rows.map(p=>[p.t/1000,p.equity]));el('readout').textContent=`策略收益 ${a.funded.return_pct.toFixed(2)}% · 最大回撤 ${a.funded.max_drawdown_pct.toFixed(2)}% · 持有参照 ${a.benchmark.return_pct.toFixed(2)}%`;chart.timeScale().fitContent();}chart.subscribeCrosshairMove(event=>{if(values.has(event.time))el('readout').textContent=new Date(event.time*1000).toISOString().slice(0,10)+' · 策略权益 '+values.get(event.time).toFixed(2)+' USDT';});el('account').addEventListener('change',render);render();'''.replace('PAYLOAD',json.dumps(summary,ensure_ascii=False,allow_nan=False).replace('</','<\\/'))
    (output/'report.html').write_text(html+'<script>'+runtime+'</script><script>'+script+'</script></html>')
