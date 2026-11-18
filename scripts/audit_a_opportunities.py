"""独立日内机会审计：固定事前股票池，分层对照，不控制前向账户。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'markets/a'), str(ROOT/'markets/us/dashboard')]
import a_day_replay as D
import a_share_paper as P
import radar
from hot_backtest import prepare_symbols
from large_universe import exclusion_reason, quotes
from replay import fetch
from strategy import intraday_signal


def opening_range_opportunity(rows, prior_close, code):
    """仅列既有盘中规则的事前信号与下一根开盘采样，不计作成交。"""
    ratio = .2 if code.startswith(('300', '301', '688')) else .1
    lower, upper = P.money(prior_close*(1-ratio)), P.money(prior_close*(1+ratio))
    for i in range(1, len(rows)-1):
        now = datetime.strptime(rows[i]['time'], '%Y-%m-%d %H:%M')
        result = intraday_signal(rows[:i+1], now, True, upper, lower)
        if result['action'] != '观察做多':
            continue
        next_bar = rows[i+1]
        entry_time = datetime.strptime(next_bar['time'], '%Y-%m-%d %H:%M')-timedelta(minutes=15)
        price = P.ASharePaperAccount.fill(next_bar['open'], True, P.DEFAULTS)
        if entry_time != now or next_bar['volume'] <= 0 or not lower*1.005 < price < upper*.995:
            continue
        return dict(signal_time=rows[i]['time'], sample_at=entry_time.isoformat(), sample_price=price,
                    orb_high=result['orb_high'], estimated_vwap=result['estimated_vwap'])
    return None


def write_report(summary, out):
    obs=summary['observations']
    ready=[o for o in obs if o['plan'] and o['plan']['plan_ready']]
    signals=[o for o in obs if o.get('opening_range')]
    above=sum(o['last_close']>o['opening_range']['sample_price'] for o in signals)
    lines=[f"# {summary['trade_date']} A股漏机会审计", '',
           f"生成：{summary['generated_at']}；信号日：{summary['signal_date']}。", '',
           summary['scope'], '',
           f"成功采集 {summary['collected']} 只；失败 {len(summary['failed'])} 只。按昨日数据重建合格计划 {len(ready)} 只；沪深300过滤{'通过' if summary['actual_market_pass'] else '未通过'}。", '',
           '## 同样本分层回放', '',
           '| 对照 | 样本数 | 可执行候选 | 当日新仓 | 收盘估算权益/CNY |',
           '|---|---:|---:|---|---:|']
    labels={'top100_strict':'存档池前100＋原过滤', 'top100_market_off':'前100＋仅关闭大盘过滤',
            'full_pool_strict':'完整存档池＋原过滤', 'full_pool_market_off':'完整存档池＋仅关闭大盘过滤',
            'full_pool_no_cap_market_off':'完整存档池＋关闭市值和大盘过滤'}
    for v in summary['variants']:
        positions='、'.join(f"{p['code']} {p['name']} {p['entry_price']:.2f}→{p['last_price']:.2f}" for p in v['positions']) or '无'
        lines.append(f"| {labels[v['name']]} | {v['stocks']} | {v['candidates']} | {positions} | {v['equity']:.2f} |")
    lines+=['', '## 昨日合格计划为何没买', '',
            '| 股票 | 分型 | 昨日计划区间 | 今日开盘→收盘 | 区间内开盘采样次数 | 其他排除条件 |',
            '|---|---|---|---|---:|---|']
    for o in ready:
        z=o['plan']['sniper']
        lines.append(f"| {o['code']} {o['name']} | {o['strategy_rule']} | {z['secondary_buy']:.2f}～{z['ideal_buy']:.2f} | {o['first_open']:.2f}→{o['last_close']:.2f} | {o['open_samples_in_plan']} | {o['exclusion_now'] or '无市值/流动性排除'} |")
    lines+=['', '区间内开盘采样为0不等于盘中价格从未触及；15分钟高低价相交也不等于有真实盘口可成交。逐笔真实入场、同日T+1与费用仍以引擎对照输出为准。', '',
            '## 独立盘中突破信号对照', '',
            f"复用既有开盘15分钟区间＋估算均价规则，发现 {len(signals)} 只具备已完成突破信号和下一根开盘采样。其中 {above} 只收盘高于采样买入价，{len(signals)-above} 只收盘不高于采样价。此统计未完成T+1卖出，不是已平仓胜率或可交易组合收益。", '',
            '该通道当前未接入运行策略；缺少昨日回踩分型的股票，即使今日盘中转强，现有账户也不会买入。以下仅列信号样本，所有结果在summary.json保留。', '',
            '| 股票 | 完成信号时间 | 下一根开盘含滑点采样 | 收盘 | 昨日分型 |',
            '|---|---|---:|---:|---|']
    for o in sorted(signals, key=lambda x:x['code']):
        s=o['opening_range']
        lines.append(f"| {o['code']} {o['name']} | {s['signal_time']} | {s['sample_price']:.2f} | {o['last_close']:.2f} | {o['strategy_rule'] or '未满足'} |")
    lines+=['', '## 结论与边界', '',
            '现有策略同时受股票池覆盖、市值、大盘开关、昨日分型和固定回踩区间约束。“放量突破”分型的执行仍等待昨日突破位回踩，不具备今日突破后跟随的入场通道。零成交不能证明没有市场机会。', '',
            '关闭过滤也不能自动解决问题：必须同时看真实到价、成本、T+1及后续退出。这个单日审计用于定位漏单层级，不能据此选择新策略或声称新策略已验证盈利。', '',
            '失败样本：'+json.dumps(summary['failed'],ensure_ascii=False), '']
    lines+=['- '+s for s in summary['limitations']]
    (out/'report.md').write_text('\n'.join(lines)+'\n')


def audit(snapshot, signal, today, out):
    if datetime.fromisoformat(snapshot['generated_at']).date().isoformat() > signal:
        raise ValueError('股票池必须在信号日或之前存档，不能使用今日赢家选样')
    out.mkdir(parents=True, exist_ok=True)
    accepted, rejected = prepare_symbols(snapshot['stocks'])
    meta = quotes(accepted)
    benchmark = radar._hs300(signal)
    calendar = P.market_calendar(today)
    if calendar[-2:] != [signal, today]:
        raise ValueError('信号日与回放日不是已验证的相邻交易日')
    daily, minutes, failures = {}, {}, {}
    cache = ROOT/'.cache/a-opportunity-audit'/f'{signal}_{today}'
    cache.mkdir(parents=True,exist_ok=True)

    def one(stock):
        code = stock['code']
        path = cache/(code+'.json')
        if path.exists():
            return json.loads(path.read_text())
        rows, source = radar._current_history(stock, signal)
        _, _, bars = fetch(code)
        pack = dict(daily=rows, minutes=[b for b in bars if b['time'][:10] == today], source=source)
        path.write_text(json.dumps(pack, ensure_ascii=False, allow_nan=False))
        return pack

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(one, s): s['code'] for s in accepted}
        for job in as_completed(jobs):
            code = jobs[job]
            try:
                pack = job.result()
                daily[code], minutes[code] = pack['daily'], pack['minutes']
            except Exception as exc:
                failures[code] = str(exc)[:200]
            print('采集', len(daily)+len(failures), '/', len(accepted), flush=True)

    sample = dict(signal_date=signal, trade_date=today, benchmark=benchmark, calendar=calendar,
                  daily=daily, minutes=minutes, failures=failures,
                  names={s['code']: (meta.get(s['code'], {}).get('name') or s.get('name') or s['code']) for s in accepted},
                  scope=f"{snapshot['generated_at']}已存档的固定热榜池；不是全市场，也不是昨日实际生成的名单。",
                  pool_generated_at=snapshot['generated_at'])
    with gzip.open(out/'input.json.gz', 'wt') as f:
        json.dump(sample, f, ensure_ascii=False, allow_nan=False)
    end = datetime.now(P.BJ).isoformat()
    all_result = D.replay_day(sample, end, market_filter=False)
    observations = all_result['observations']
    top100 = {s['code'] for s in snapshot['stocks'][:100]}
    eligibility = {}
    for o in observations:
        rows = daily[o['code']]
        turnover = sum(b['close']*b['volume'] for b in rows[-20:])/20 if len(rows) >= 20 else None
        cap = meta.get(o['code'], {}).get('float_cap_yi')
        reason = exclusion_reason(o['name'], cap, turnover)
        eligibility[o['code']] = reason
        o.update(top100=o['code'] in top100, float_cap_yi_now=cap, exclusion_now=reason,
                 avg20_turnover=turnover,
                 opening_range=opening_range_opportunity(minutes[o['code']], rows[-1]['close'], o['code']))
    variants = []
    for name, first100, use_cap, use_market in (
        ('top100_strict', True, True, True),
        ('top100_market_off', True, True, False),
        ('full_pool_strict', False, True, True),
        ('full_pool_market_off', False, True, False),
        ('full_pool_no_cap_market_off', False, False, False),
    ):
        codes = {o['code'] for o in observations if (not first100 or o['top100']) and
                 (not eligibility[o['code']] or (not use_cap and eligibility[o['code']] == '当前流通市值低于100亿元'))}
        subset = dict(sample, daily={c: daily[c] for c in codes}, minutes={c: minutes[c] for c in codes})
        result = D.replay_day(subset, end, market_filter=use_market)
        (out/(name+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        variants.append(dict(name=name, stocks=len(codes), candidates=len(result['radar']['strategy_candidates']),
                             positions=result['account']['positions'], equity=result['account']['equity'],
                             market_filter=use_market))
    summary = dict(generated_at=end, signal_date=signal, trade_date=today, scope=sample['scope'],
                   collected=len(daily), failed=failures, unsupported=rejected,
                   actual_market_pass=D.R.setups_radar(sample, signal)['market_pass'],
                   observations=observations, variants=variants,
                   limitations=[
                       '名单为按事前股票池重建的反事实研究，不是昨日真实生成名单，不写前向账本。',
                       '市值与名称为本次报价时点，只用于敏感性分层，不能视为历史时点严格回测。',
                       '逐日截断日线，只按已完成15分钟K线的开盘采样，保留后续新报价、滑点、费税、T+1及仓位上限。',
                       '日内高低点只作事后描述，不能据此假定到价成交。单日新仓没有已平仓胜率。',
                       '固定历史热榜池覆盖有限，不能据此断言全市场没有机会。',
                   ])
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    write_report(summary,out)
    print(json.dumps({k: summary[k] for k in ('collected', 'actual_market_pass', 'variants')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--signal-date', required=True)
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--render-only',action='store_true')
    args = parser.parse_args()
    if args.render_only:
        write_report(json.loads((args.output/'summary.json').read_text()),args.output)
    else:
        audit(json.loads(args.snapshot.read_text()), args.signal_date, args.trade_date, args.output)
