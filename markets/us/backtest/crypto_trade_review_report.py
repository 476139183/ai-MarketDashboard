#!/usr/bin/env python3
"""Publish the auditable research decision and a standalone per-trade replay."""
import argparse
from bisect import bisect_right
from datetime import datetime,timezone,timedelta
from html import escape
import json
from pathlib import Path
from crypto_trade_review import snapshots
from crypto_ema_replay import SMALL


def local(t):return datetime.fromtimestamp(t/1000,timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')


def write_report(path):
    r=json.loads(path.read_text());output=path.parent
    def m(name,w):return r['details'][name][w]['metrics']
    def stress(name,w):return next(x for x in r['cost_stress'] if x['model']==name and x['window']==w)
    table=['| 版本 | 开发1收益/笔数 | 开发2收益/笔数 | 三个月收益/回撤 | 最近月收益/回撤 | 全段笔数 | 成本翻倍三个月 |','|---|---:|---:|---:|---:|---:|---:|']
    for name,label in r['labels'].items():
        a,b,f,q=[m(name,w) for w in ('development_1','development_2','full','review_month')]
        table.append(f'| {label} | {a["return_pct"]:+.2f}% / {a["trade_count"]} | {b["return_pct"]:+.2f}% / {b["trade_count"]} | {f["return_pct"]:+.2f}% / {f["max_drawdown_pct"]:.2f}% | {q["return_pct"]:+.2f}% / {q["max_drawdown_pct"]:.2f}% | {f["trade_count"]} | {stress(name,"full")["return_pct"]:+.2f}% |')
    original=[a for a in r['audits'] if a['model']=='original' and a['window']=='full']
    filtered={(t['symbol'],t['entry_at'],t['side']) for t in r['details']['cost_room']['full']['trades']}
    removed=[t for t in original if (t['symbol'],t['entry_at'],t['side']) not in filtered]
    trade_table=['| # | 标的/方向 | 开仓→平仓（北京） | 净盈亏USDT | 持仓h | MFE/MAE价格幅度 | 退出 | 复盘 |','|---:|---|---|---:|---:|---:|---|---|']
    reasons={'stop_loss':'止损','signal_invalid':'状态失效','end_of_test':'期末'}
    for t in original:
        key=f'original-full-{t["trade_id"]}';kept=(t['symbol'],t['entry_at'],t['side']) in filtered
        note=t['notes']+('；成本过滤保留' if kept else '；成本过滤排除')
        trade_table.append(f'| [{t["trade_id"]}](review.html#{key}) | {t["symbol"]}/'+('多' if t['side']==1 else '空')+f' | {local(t["entry_at"])}→{local(t["exit_at"])} | {t["net_pnl"]:+.2f} | {t["holding_hours"]:.2f} | +{t["mfe_pct"]:.2f}% / {t["mae_pct"]:.2f}% | {reasons.get(t["exit_reason"],t["exit_reason"])} | {note} |')
    excluded=['| 被过滤原交易 | 当时到中轨空间 | 原账户净盈亏USDT |','|---|---:|---:|']
    for t in removed:excluded.append(f'| {t["symbol"]} {local(t["entry_at"])} '+('多' if t['side']==1 else '空')+f' | {t["room_pct"]:.3f}% | {t["net_pnl"]:+.2f} |')
    report='''# 最佳候选的选择、逐笔复盘与调整

研究日期：2026-11-07。窗口为北京时间8月7日16:00—11月7日16:00，最近月10月7日16:00起；三币为BTC/ETH/SOL欧易USDT永续。所有数值为历史模拟，不修改线上策略或模拟账户。

## 当前选择与调整结论

**正式保留的研究基准：布林20震荡回归＋2ATR保护止损。下一阶段优先并行模拟：增加“回归空间至少覆盖三倍预估往返成本”的单项过滤。** 后者在已观察的三个月数据上收益/成本耐受更好，但不能冒称已经通过独立新样本验证。

前一轮42组只有原布林模型通过预设开发门槛，因此它是当前有依据的主候选。日SMA10/40＋ATR虽然三个月+254.24%，开发第二段回撤40.67%，继续排除在主候选之外。对原版做逐笔检查后，在查看新版本收益前固定六个单项调整；[本轮规则与筛选预声明](../../plans/2026-11-07-crypto-trade-review.md)保留了条件。

**预设开发排序的赢家实际上是ATR1.5，而不是成本空间版。** ATR1.5两段开发收益+5.08%/+12.29%，但最近月−3.10%、成本翻倍最近月−4.27%；这说明缩紧止损的开发期改善没有延续。成本空间版最低开发段仍为+3.01%，与原版相同，没有达到“提高最差段收益”的严格改进要求。因此本轮不自动替换基准；将其列为最值得影子模拟的复盘候选，是已观察数据后的工程判断，不能包装成预声明选出的赢家。

## 126次追加回放的完整对照

保持10x、每笔保证金目标/上限20%净值、新开仓组合60%上限、每边手续费0.06%及不利滑点0.05%、历史资金费和标记价。7版本×4区间×4账户=112次，另有14次组合成本翻倍重放。所有尝试保留，未出现未解决路径。各区间重新以10,000 USDT空仓开始，不能把分段收益相加。

TABLE

成本空间版全段回撤仍是15.96%，没有改善最差阶段；最近月也仍是+2.06%/3笔，三笔均为SOL，BTC/ETH无交易。最近月成本翻倍仍为+0.73%。ADX不增强和日线同向虽然把最近月提高到+4.73%，却只剩一笔交易，且前段笔数或表现不满足门槛，不据此升级。ATR2.5则未改善收益/回撤。

## 每笔交易是否正确

本轮审计原版三个月23笔、独立最近月3笔，以及成本空间版三个月19笔、独立最近月3笔，共48份逐笔记录；重叠窗口不是48次独立交易。**48份均未发现已定义检查范围内的异常**，并且原版逐笔重放与冻结上一轮结果完全一致。

逐笔重建已闭合4h的布林回归触发，检查信号时间、延迟至少一个5m入场、实际5m开盘与不利滑点、合约乘数和张数步进、保证金与10x数量、初始止损、首次应该触发的退出、两边费用、持仓期间逐仓资金费和净盈亏。账户仍逐笔对账。指标使用同一已测试的ADX/ATR算法，原始行情与撮合假设没有真实成交回单可供认证；因此这里的“正确”指符合冻结规则与模型，不能等同于实盘必然成交。

亏损不自动等于错误。六笔止损属于模型允许的亏损；中轨空间为负的那笔则是规则设计的弱点：重新回到带内时已经越过均值，原版仍允许开空。它机械执行正确，但其回归交易理由已经较弱。

MFE/MAE只表示已知持仓路径内的价格有利/不利幅度，未乘杠杆，未扣成本；退出所在5m若根内先后未知，排除该根极值，仅加入已知退出参考点。所以它是保守的路径描述，不是精确最大浮盈/亏损，也不是可直接兑现的收益。下列每笔编号均可打开对应图，观察前后行情、成交量和中轨。

TRADE_TABLE

## 哪些调整有实际理由

成本空间定义为信号收盘时 `R=方向×(中轨/收盘−1)`。长单需要中轨仍在上方，短单需要中轨仍在下方；往返静态成本代理为 `C=2×(0.0006+0.0005)=0.22%`，过滤条件 `R≥3C=0.66%`。它只决定新开仓，保留原持仓的状态退出与2ATR止损。资金费和下一5m开盘跳动事前未知，所以0.66%并不保证净利润，也不是以实际开仓价算出的精确成本门槛。

该条件排除以下四笔原交易，包含三笔亏损和一笔盈利，没有逐笔按结果挑选：

EXCLUDED

四笔在原账户合计净亏674.99 USDT；新版本总净利润比原版多803.42 USDT，其余差异来自后续仓位按净值复利变化，不能把两者简单相等。19笔仍很少，最大回撤未下降，且这些交易已被观察；门槛是针对这轮复盘提出的假说，必须在新数据中检验。

两笔最近月逆日线多单净亏（最近月独立账户）分别为87.63和175.68 USDT，但加日线同向过滤也会删去其他阶段的盈利回归。不能因为最后一月正好有效就断言所有逆日线交易都应取消。类似地，ETH一笔持仓期间曾有较大有利波动，不能用事后最高价替代当时未知的止盈点；本轮没有凭最高点编造移动止盈收益。

## 可直接复查的材料与下一步

- [逐笔5m行情、成交量和交易标记回放](review.html)：切换原版/成本空间版、三个月/最近月及每笔编号；支持隐藏后续K线并逐根播放。
- [48份审计明细](trade-audit.csv)、[112次常规回放](adjustments.csv)、[完整结果及14次成本压力回放](review.json)。
- [上一轮42组研究报告](../加密%20合约%20策略%20回放%20研究.md)。

建议下一步固定原版和成本空间版同时前向模拟，记录真实到达时间、被过滤原因、虚拟成交、资金费与权益。保持现有10x/20%设定，只比较这一个新过滤；积累新的交易后决定是否替换，期间不继续根据旧样本调门槛。当前完成的是研究回放与选择，没有改线上模拟策略，也没有发送真实订单。

布林带触轨本身不构成买卖信号、趋势中价格可以沿轨运行；这是保留“回到带内＋震荡条件”并审查是否已越中轨的依据。参数适用性仍需检验。[Bollinger本人规则](https://www.bollingerbands.com/bollinger-band-rules)
'''.replace('TABLE','\n'.join(table),1).replace('TRADE_TABLE','\n'.join(trade_table)).replace('EXCLUDED','\n'.join(excluded))
    (output/'report.md').write_text(report)
    bundle=json.loads(Path(r['source_bundle']).read_text());views={s:snapshots(d['slow']) for s,d in bundle['data'].items()};charts=[]
    for t in r['audits']:
        s=t['symbol'];v=views[s];stamps=sorted(v);lo=t['entry_at']-8*3600000;hi=t['exit_at']+8*3600000
        bars=[]
        for row in bundle['data'][s]['fast']:
            if not lo<=row['t']<=hi:continue
            j=bisect_right(stamps,row['t'])-1;snap=v[stamps[j]]
            bars.append(dict(time=row['t']//1000,open=row['o'],high=row['h'],low=row['l'],close=row['c'],volume=row['v'],middle=snap['middle'],lower=snap['lower'],upper=snap['upper']))
        key=f'{t["model"]}-{t["window"]}-{t["trade_id"]}'
        charts.append(dict(id=key,model=t['model'],window=t['window'],symbol=s,side=t['side'],entry_at=t['entry_at'],exit_at=t['exit_at'],entry_price=t['entry_price'],exit_price=t['exit_price'],stop_price=t['stop_price'],net_pnl=t['net_pnl'],audit_ok=t['audit_ok'],notes=t['notes'],mfe_pct=t['mfe_pct'],mae_pct=t['mae_pct'],room_pct=t['room_pct'],adx=t['adx'],daily=t['daily'],bars=bars))
    vendor=Path(__file__).resolve().parents[1]/'dashboard/static/vendor';runtime=(vendor/'lightweight-charts.js').read_text().replace('</script','<\\/script')
    html=Path(__file__).with_name('crypto_trade_review_template.html').read_text()
    payload=json.dumps(dict(charts=charts,labels=r['labels']),ensure_ascii=False,allow_nan=False).replace('</','<\\/')
    (output/'review.html').write_text(html.replace('CHART_RUNTIME',runtime).replace('DATA_PAYLOAD',payload).replace('LICENSE_TEXT',escape((vendor/'lightweight-charts.LICENSE').read_text())))
    print('Report:',output/'report.md');print('Interactive replay:',output/'review.html')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('result',type=Path);args=parser.parse_args();write_report(args.result)
