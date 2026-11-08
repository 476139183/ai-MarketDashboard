#!/usr/bin/env python3
"""One-month 10x replay with 20% equity margin and an ex-ante daily trend filter."""
import argparse
from calendar import monthrange
from collections import Counter
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
from html import escape
import json
from pathlib import Path

from crypto_ema_replay import CACHE,COINS,STEP,SMALL,Rules,request,normalize_candles,strategy_signals,daily_trend_signals,simulate,iso
from crypto_replay_report import daily,stamp,write_csv


def month_start(end):
    dt=datetime.fromtimestamp(end/1000,timezone.utc)
    month=dt.month-1;year=dt.year
    if month==0:month=12;year-=1
    return int(dt.replace(year=year,month=month,day=min(dt.day,monthrange(year,month)[1])).timestamp()*1000)


def mark_history(symbol,start,end,offline=False):
    path=CACHE/f'{symbol}-mark-5m-{start}-{end}.json'
    if path.exists():
        payload=json.loads(path.read_text())
    else:
        if offline:raise ValueError('Missing cached mark history: '+str(path))
        raw=[];cursor=end
        while cursor>start:
            batch=request('market/history-mark-price-candles',dict(instId=symbol+'-USDT-SWAP',bar='5m',limit=100,after=cursor))
            if not batch:break
            oldest=min(int(row[0]) for row in batch)
            if oldest>=cursor:raise ValueError('Mark history pagination stalled')
            raw.extend(batch);cursor=oldest
        payload=dict(source='OKX history-mark-price-candles',start=start,end=end,raw=raw)
        path.write_text(json.dumps(payload,separators=(',',':')))
    # Price-only bars have no trading volume; the zero is internal normalization only.
    rows=normalize_candles([r[:5]+['0','0','0',r[5]] for r in payload['raw']],SMALL,start,end)
    for row in rows:row.pop('v')
    return rows,dict(file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),rows=len(rows))


def tiers(symbol,offline=False):
    path=CACHE/f'{symbol}-tiers-isolated.json'
    if path.exists():return json.loads(path.read_text())
    if offline:raise ValueError('Missing cached maintenance tiers')
    data=request('public/position-tiers',dict(instType='SWAP',tdMode='isolated',instFamily=symbol+'-USDT'))
    path.write_text(json.dumps(data,separators=(',',':')))
    return data


def replay(bundle_path,leverage=10,margin_fraction=.2,offline=False):
    bundle=json.loads(bundle_path.read_text());end=bundle['end'];start=month_start(end);data=bundle['data']
    rules=Rules(leverage=leverage,margin_fraction=margin_fraction)
    fast={s:[r for r in data[s]['fast'] if start<=r['t']<end] for s in COINS}
    marks={};specs={};audit={};trends={};price_curves={}
    for s in COINS:
        if [r['t'] for r in fast[s]]!=list(range(start,end,SMALL)):raise ValueError('Execution gaps')
        marks[s],mark_audit=mark_history(s,start,end,offline)
        tier=sorted(tiers(s,offline),key=lambda r:int(r['tier']))[0]
        if float(tier['maxLever'])<leverage:raise ValueError('Requested leverage exceeds verified tier')
        specs[s]=dict(data[s]['spec'],mmr=tier['mmr'],tier_maxSz=tier['maxSz'])
        rows=fast[s];first=rows[0]['o'];last=rows[-1]['c']
        # Whole-month endpoint change is descriptive, never an input to past signals.
        trends[s]=dict(first_price=first,last_price=last,price_change_pct=(last/first-1)*100,
                      low=min(r['l'] for r in rows),high=max(r['h'] for r in rows))
        price_curves[s]=daily([dict(t=r['t']+SMALL,equity=r['c']/first*100) for r in rows],start,100)
        audit[s]=dict(execution_bars=len(rows),signal_bars_with_warmup=len(data[s]['slow']),mark=mark_audit,mmr=float(tier['mmr']))
    base={s:strategy_signals(data[s]['slow'],start,end) for s in COINS}
    bias={s:daily_trend_signals(data[s]['slow'],start,end) for s in COINS}
    filtered={s:{t:v if v==bias[s][t] else 0 for t,v in base[s].items()} for s in COINS}
    funding={s:{int(t):r for t,r in data[s]['funding']['rates'].items() if start<=int(t)<end} for s in COINS}
    for s in COINS:
        expected=list(range(start,end,8*3600000))
        if sorted(funding[s])!=expected:raise ValueError('Funding gap / different settlement schedule requires explicit review')
        audit[s]['funding_events']=len(funding[s]);audit[s]['daily_bias_states']=dict(Counter(bias[s].values()))
    versions={}
    for version,signals in [('original_4h',base),('daily_trend_filtered',filtered)]:
        versions[version]={}
        for name,symbols in [(s,[s]) for s in COINS]+[('COMBINED',list(COINS))]:
            result=simulate({s:fast[s] for s in symbols},{s:signals[s] for s in symbols},start,end,rules=rules,
                            specs=specs,funding=funding,marks={s:marks[s] for s in symbols})
            for trade in result['trades']:
                if trade['initial_margin']/trade['equity_at_entry']>margin_fraction+1e-9:raise AssertionError('Entry margin fraction exceeded')
                if abs(trade['notional']/trade['initial_margin']-leverage)>1e-9:raise AssertionError('Incorrect nominal leverage')
            versions[version][name]=result
            print(version,name,f'return={result["return_pct"]:+.4f}%, drawdown={result["max_drawdown_pct"]:.4f}%, trades={result["trade_count"]}',flush=True)
    return dict(start=start,end=end,generated_at=iso(int(datetime.now(timezone.utc).timestamp()*1000)),rules=asdict(rules),
        source_bundle=str(bundle_path),source_bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        trends=trends,price_curves=price_curves,specs=specs,data_audit=audit,versions=versions)


def export(result,output):
    output.mkdir(parents=True,exist_ok=True)
    reduced={k:v for k,v in result.items() if k!='versions'};reduced['versions']={}
    all_trades=[]
    for version,accounts in result['versions'].items():
        reduced['versions'][version]={}
        for name,r in accounts.items():
            reduced['versions'][version][name]={k:v for k,v in r.items() if k not in ('trades','equity_curve')}
            reduced['versions'][version][name]['equity_daily']=daily(r['equity_curve'],result['start'],r['initial_balance'])
            if name=='COMBINED':all_trades.extend(dict(version=version,**t) for t in r['trades'])
    (output/'summary.json').write_text(json.dumps(reduced,ensure_ascii=False,indent=2,allow_nan=False))
    write_csv(output/'trades.csv',all_trades)
    keys=[(v,s) for v,accounts in result['versions'].items() for s in accounts]
    curves=[]
    for i,p in enumerate(result['versions']['original_4h']['COMBINED']['equity_curve']):
        curves.append(dict(t=p['t'],beijing_time=stamp(p['t']),**{v+'_'+s:result['versions'][v][s]['equity_curve'][i]['equity'] for v,s in keys}))
    write_csv(output/'equity-5m.csv',curves)
    leverage=result['rules']['leverage'];fraction=result['rules']['margin_fraction'];balance=result['rules']['initial_balance']
    lines=[f'# BTC / ETH / SOL 最近一个月：{leverage:g}x、每笔 {fraction*100:g}% 保证金',
        f'\n**{stamp(result["start"])}—{stamp(result["end"])}，北京时间。**',
        f'\n本金 {balance:,.0f} USDT；每笔保证金按开仓前当前净值 {fraction*100:g}% 动态计算，{leverage:g} 倍杠杆，可同时做多或做空，最多三币各一仓。首笔约 {balance*fraction:,.0f} USDT 保证金、{balance*fraction*leverage:,.0f} USDT 名义仓位。单币账户各用独立 {balance:,.0f} USDT；组合共用一份 {balance:,.0f} USDT。',
        '\n## 先看一个月的市场趋势','\n| 币种 | 起始价格 / USDT | 期末价格 | 月内最低 / 最高 | 价格涨跌 |','|---|---:|---:|---:|---:|']
    for s,t in result['trends'].items():lines.append(f'| {s} | {t["first_price"]:,.2f} | {t["last_price"]:,.2f} | {t["low"]:,.2f} / {t["high"]:,.2f} | {t["price_change_pct"]:+.2f}% |')
    lines+=['\n三币月度价格都下跌，做空方向本身并非错误；但整月跌幅不能用于指导月初交易。以下日线过滤逐次只用当时已收盘的数据，不把月底结果反推成入场信号。',
        '\n## 原策略与日线顺势过滤对照',
        '\n原策略：已收盘 4h 收盘价、EMA20 / EMA50 与 EMA20 近 6 根斜率同向。过滤版：额外要求当时最新已收盘日线收盘价 > MA20 > MA60 且 MA20 较 5 天前上升才允许多头；反向排列且 MA20 下降才允许空头，其余观望。过滤影响入场和持仓有效性。',
        '\n| 账户 | 原策略收益率 | 原策略最大回撤 | 日线过滤收益率 | 日线过滤最大回撤 | 原策略 / 过滤版笔数 |','|---|---:|---:|---:|---:|---:|']
    for name in ('BTC','ETH','SOL','COMBINED'):
        a=result['versions']['original_4h'][name];b=result['versions']['daily_trend_filtered'][name]
        lines.append(f'| {"三币组合" if name=="COMBINED" else name} | {a["return_pct"]:+.2f}% | {a["max_drawdown_pct"]:.2f}% | {b["return_pct"]:+.2f}% | {b["max_drawdown_pct"]:.2f}% | {a["trade_count"]} / {b["trade_count"]} |')
    lines+=['\n## 三币组合账目','\n| 项目 | 原策略 | 日线顺势过滤 |','|---|---:|---:|']
    for label,key in [('期末权益 / USDT','final_equity'),('净盈亏 / USDT','net_profit'),('手续费 / USDT','fees'),('估计不利滑点成本 / USDT','slippage_cost'),('资金费净支出 / USDT','funding_cost'),('胜率 %','win_rate_pct'),('最大存量保证金 / 当时净值 %','max_margin_utilization_pct'),('最大组合名义敞口 / 当时净值','max_gross_leverage')]:
        a=result['versions']['original_4h']['COMBINED'][key];b=result['versions']['daily_trend_filtered']['COMBINED'][key]
        lines.append(f'| {label} | {a:,.2f} | {b:,.2f} |')
    lines+=['\n滑点已体现在成交价中，不再重复扣减。资金费负数代表净收款。每笔 20% 指开仓时的当前净值比例；存量保证金不会随之后净值变化自动再平衡，因此亏损后占比可能超过初始约 60%，本回测没有额外追加保证金。',
        '\n## 执行与杠杆风险口径',
        '\n- 信号仅使用已收盘 4h，最近 119 根完整窗口；日线由六根完整 4h 聚合为 UTC 日线。首次观察信号后，下一根 5m 开盘才允许入场；同一 4h 周期和方向只开一次。',
        '- 止损 2%、止盈 4%仍是**币价幅度**，不是保证金收益幅度。10x 下，首笔 2% 价格止损约损失账户净值 4%，另加费用与滑点；三币相关风险可以叠加。',
        '- 手续费每边 0.06%、不利滑点每边 0.05%，均按实际杠杆名义金额计算。资金费用实际历史 `realizedRate` 和结算时 5m 标记价格开盘值估算。',
        '- 按逐仓估值做风险筛查：使用公开当前档位维持保证金率 0.4%及预计平仓手续费，对持仓期间每根 5m 历史标记价格的开盘与不利极值检查强平边界。若穿越边界则中止确定性收益输出，要求更细数据；当前两个版本均未出现该情况。没有完整复刻交易所强平、减仓、保险基金或 ADL。',
        '- 最大回撤按每 5m 标记价格收盘权益计算，不保证覆盖所有盘中尖峰。止损价被跳空越过时按当前执行价格退出；同根同时触发止盈止损按止损先算。',
        '- 保证金与费用从可用现金扣除，已有仓位按当时标记价格估值后再计算下一笔 20% 保证金；不以未来平仓收益为更早开仓提供资金。期末统一退出、扣平仓成本。',
        '\n## 数据与复现',
        '\n每币 8,928 根完整 5m 成交 K 线、8,928 根历史标记价格线、93 条实际资金费记录。4h 与预热数据复用三个月研究缓存；日线过滤有足够的 60 日历史。所有月份范围、文件 SHA-256 和当前合约规格见 [summary.json](summary.json)。',
        '\n```bash','python3 -m unittest discover -s markets/us/backtest -p "test_crypto*replay.py"',
        'python3 markets/us/backtest/crypto_margin_month.py \\',
        '  --bundle markets/us/.cache/crypto-ema-replay/bundle-1786089600000-1794038400000.json \\',
        '  --leverage 10 --margin-fraction 0.2 --offline','```',
        '\n`--offline` 要求已有成交 K 线、标记价格和保证金档位缓存。省略后两者可从欧易公开接口读取。此研究没有改变线上模拟账户、全局策略默认值或真实交易账户。',
        '\n[交互式趋势与资金曲线](report.html) · [组合逐笔 CSV](trades.csv) · [5m 权益序列](equity-5m.csv)',
        '\n## 结论',
        '\n日线过滤减少了逆大级别趋势的交易和交易频次，组合亏损与回撤小于原策略，但仍是亏损；SOL 也未保持原策略的小幅盈利。该对照是在同一历史区间进行的规则调整，不是独立样本外验证，不能据此认定修改后的策略已经盈利有效。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    html_report(reduced,output)


def html_report(result,output):
    vendor=Path(__file__).resolve().parents[1]/'dashboard'/'static'/'vendor'
    runtime=(vendor/'lightweight-charts.js').read_text().replace('</script','<\\/script')
    license_text=escape((vendor/'lightweight-charts.LICENSE').read_text())
    table=''
    for s in ('BTC','ETH','SOL','COMBINED'):
        a=result['versions']['original_4h'][s];b=result['versions']['daily_trend_filtered'][s]
        table+=f'<tr><td>{s}</td><td>{a["return_pct"]:+.2f}%</td><td>{a["max_drawdown_pct"]:.2f}%</td><td>{b["return_pct"]:+.2f}%</td><td>{b["max_drawdown_pct"]:.2f}%</td><td>{a["trade_count"]}/{b["trade_count"]}</td></tr>'
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BTC ETH SOL｜10x 月度策略对照</title><style>*{box-sizing:border-box}body{margin:0;background:#10181d;color:#d6e1e7;font:15px system-ui;line-height:1.8}main{max-width:1200px;margin:auto;padding:30px 20px}h1{font-size:28px}h2{font-size:19px}.panel{background:#18242b;border:1px solid #2a3943;border-radius:12px;margin:20px 0;padding:22px}p{color:#9bb0bd}select{background:#243743;color:#d6e1e7;padding:9px;border:1px solid #476170;border-radius:5px}.plot{height:330px}.table{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap}td,th{padding:12px;text-align:right;border-bottom:1px solid #2b3f4b}td:first-child,th:first-child{text-align:left}a{color:#eab56d}pre{white-space:pre-wrap;font-size:12px}.loss{color:#ef8b87}footer{font-size:12px;color:#9bb0bd}@media(max-width:600px){main{padding:20px 12px}.panel{padding:14px}h1{font-size:23px}.plot{height:290px}}</style><main><div>观市 MARKET STUDIO / 历史策略研究</div><h1>先看月度趋势，再检查 10x 合约策略。</h1><p>WINDOW · 每笔当前净值 20% 保证金 · 双向交易 · 期初 10,000 USDT</p><section class="panel"><h2>最近一个月币价走势（起点＝100）</h2><p>PRICECHANGES。这是事后走势描述，不参与月初决策。</p><div id="prices" class="plot"></div></section><section class="panel"><h2>策略账户权益 <select id="account"><option value="COMBINED">三币组合</option><option>BTC</option><option>ETH</option><option>SOL</option></select></h2><p id="stats"></p><p><span style="color:#ef8b87">原 4h 策略</span>　<span style="color:#65c4aa">加入已收盘日线趋势过滤</span></p><div id="equity" class="plot"></div></section><section class="panel table"><h2>同资金、同杠杆对照</h2><table><thead><tr><th>账户</th><th>原策略收益</th><th>原策略回撤</th><th>过滤版收益</th><th>过滤版回撤</th><th>交易笔数</th></tr></thead><tbody>TABLE</tbody></table></section><section class="panel"><h2 class="loss">减少亏损，尚未变成盈利策略。</h2><p>PERFORMANCE。10x 不能直接乘前一个低敞口账户的收益率；每次净值变化后仓位、费用和资金费都需重新计算。</p><p>止损 2% 和止盈 4% 是价格幅度；首笔止损约对应净值 4% 的损失，另加成本。存量保证金占比会随净值变化，不自动再平衡。采用历史标记价格做逐仓强平边界筛查，未复刻交易所完整强平流程。</p><p>当前对照仅覆盖一个月，同一历史区间调整规则不代表样本外有效性。完整口径、数据源和逐笔记录见下方文件。</p></section><footer><a href="report.md">完整说明</a> · <a href="trades.csv">组合逐笔 CSV</a> · <a href="summary.json">指标与数据审计</a> · <a href="https://www.tradingview.com/">TradingView Lightweight Charts™</a><details><summary>图表第三方许可</summary><pre>LICENSE</pre></details></footer></main>'''
    changes=' · '.join(f'{s} {t["price_change_pct"]:+.2f}%' for s,t in result['trends'].items())
    performance=f'组合原策略 {result["versions"]["original_4h"]["COMBINED"]["return_pct"]:+.2f}%，日线过滤后 {result["versions"]["daily_trend_filtered"]["COMBINED"]["return_pct"]:+.2f}%'
    html=html.replace('PRICECHANGES',changes).replace('PERFORMANCE',performance)
    html=html.replace('WINDOW',stamp(result['start'])+'—'+stamp(result['end'])+'（北京时间）').replace('TABLE',table).replace('LICENSE',license_text)
    script='''const DATA=PAYLOAD;const L=LightweightCharts;const make=id=>L.createChart(document.getElementById(id),{autoSize:true,layout:{background:{color:'#18242b'},textColor:'#9bb0bd'},grid:{vertLines:{color:'#293e4b'},horzLines:{color:'#293e4b'}}});const prices=make('prices');const colors={BTC:'#eab56d',ETH:'#a79af2',SOL:'#65c4aa'};for(const [s,rows]of Object.entries(DATA.price_curves)){const line=prices.addSeries(L.LineSeries,{color:colors[s],title:s});line.setData(rows.map(r=>({time:r.t/1000,value:r.equity})));}prices.timeScale().fitContent();const chart=make('equity');const original=chart.addSeries(L.LineSeries,{color:'#ef8b87',title:'原策略'});const filtered=chart.addSeries(L.LineSeries,{color:'#65c4aa',title:'日线顺势过滤'});function render(){const s=document.getElementById('account').value,a=DATA.versions.original_4h[s],b=DATA.versions.daily_trend_filtered[s];original.setData(a.equity_daily.map(r=>({time:r.t/1000,value:r.equity})));filtered.setData(b.equity_daily.map(r=>({time:r.t/1000,value:r.equity})));document.getElementById('stats').textContent=`原策略 ${a.return_pct.toFixed(2)}% / 最大回撤 ${a.max_drawdown_pct.toFixed(2)}%；过滤版 ${b.return_pct.toFixed(2)}% / 最大回撤 ${b.max_drawdown_pct.toFixed(2)}%`;chart.timeScale().fitContent();}document.getElementById('account').addEventListener('change',render);render();'''.replace('PAYLOAD',json.dumps(result,ensure_ascii=False,allow_nan=False).replace('</','<\\/'))
    (output/'report.html').write_text(html+'<script>'+runtime+'</script><script>'+script+'</script></html>')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--leverage',type=float,default=10);parser.add_argument('--margin-fraction',type=float,default=.2)
    parser.add_argument('--offline',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=replay(args.bundle,args.leverage,args.margin_fraction,args.offline)
    output=args.output or Path(__file__).resolve().parents[3]/'docs'/'reports'/('crypto-margin-month-'+iso(result['end'])[:10])
    export(result,output);print('Report:',output/'report.md')

if __name__=='__main__':main()
