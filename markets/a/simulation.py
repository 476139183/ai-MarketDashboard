#!/usr/bin/env python3
"""默认全市场动态选股；保留固定池历史对照，不连接交易账户。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import gzip
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from candidate_monitor import history_issue
from daily_strategy import entry_signal, run
from hot_backtest import as_of_date, load
from large_universe import quotes
from portfolio import equity_curve, period_metrics

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'markets/a/simulation-config.json'


def rank_stocks(histories, names, calendar):
    ranked, excluded = [], {}
    for code, bars in histories.items():
        name = names.get(code, '')
        if not name or 'ST' in name.upper() or '退' in name:
            excluded[code] = '缺少当前名称或存在风险警示'
            continue
        issue = history_issue(bars, calendar, calendar[-1])
        if issue:
            excluded[code] = issue
            continue
        close = float(bars[-1]['close'])
        ma5 = sum(float(x['close']) for x in bars[-5:]) / 5
        ma20 = sum(float(x['close']) for x in bars[-20:]) / 20
        high20 = max(float(x['high']) for x in bars[-21:-1])
        unmet = []
        if ma5 <= ma20:
            unmet.append('MA5未高于MA20')
        if close <= ma20:
            unmet.append('收盘未高于MA20')
        if close <= high20:
            unmet.append('未突破此前20日高点')
        if unmet:
            continue
        ranked.append({'code': code, 'name': name, 'signal_date': calendar[-1],
                       'status': '突破信号' if entry_signal(bars, 'breakout') else '观察',
                       'close': close, 'ma5': round(ma5, 3), 'ma20': round(ma20, 3),
                       'prior20_high': high20, 'breakout_distance_pct': round((close / high20 - 1) * 100, 3),
                       'unmet': unmet})
    # 只展示合格突破信号，不用观察股凑数。
    ranked.sort(key=lambda x: (len(x['unmet']), -x['breakout_distance_pct'], x['code']))
    return ranked, excluded


def fetch_index(end):
    bars = []
    for start, stop in [('2023-01-01', '2024-12-31'), ('2025-01-01', end)]:
        query = urlencode({'param': f'sh000300,day,{start},{stop},1200,qfq'})
        with urlopen(Request('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query,
                             headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
            rows = json.load(response)['data']['sh000300']['day']
        bars.extend({'date': x[0], 'close': float(x[2])} for x in rows if x[0] <= end)
    if len(bars) < 21 or any(a['date'] >= b['date'] for a, b in zip(bars, bars[1:])):
        raise ValueError('指数交易日期不足、重复或乱序')
    return bars


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'markets/a/simulation-latest.json')
    parser.add_argument('--fixed-baseline', action='store_true', help='仅重跑旧60股池历史对照')
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    if config.get('universe_mode') == 'full_market' and not args.fixed_baseline:
        from market_scan import main as scan_main
        scan_main()
        return
    if args.fixed_baseline and config.get('fixed_baseline_config'):
        config=json.loads((ROOT/config['fixed_baseline_config']).read_text(encoding='utf-8'))
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    end = as_of_date(now)
    index = fetch_index(end)
    calendar = [x['date'] for x in index]
    # 日历未绑定交易所：陈旧行情不输出伪实时选股。
    if (now.date() - datetime.fromisoformat(calendar[-1]).date()).days > 4:
        raise ValueError('行情超过4个自然日未更新，请核验交易日历；旧报告保留')
    stocks = config['stocks']
    histories, sources = {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(load, s['code'], end, s['board']): s for s in stocks}
        for future in as_completed(futures):
            stock = futures[future]
            bars, source, errors = future.result()
            if source != '新浪未复权日线':
                raise ValueError(f'{stock["code"]}复权口径变化，停止计算；旧报告保留')
            if bars[-1]['date'] != calendar[-1]:
                raise ValueError(f'{stock["code"]}最新日线缺失，停止计算，避免组合分母变化')
            histories[stock['code']] = bars
            sources[stock['code']] = {'source': source, 'first': bars[0]['date'],
                                      'last': bars[-1]['date'], 'errors': errors}
    current = quotes(stocks)
    names = {code: q['name'] for code, q in current.items()
             if q['quote_time'][:8] >= calendar[-1].replace('-', '')}
    ranked, excluded = rank_stocks(histories, names, calendar)
    results = {}
    for label, start in [('historical_replay', None), ('forward_simulation', config['forward_start'])]:
        per_stock = {}
        for stock in stocks:
            board = stock['board']
            ratio = .3 if board == '北交所' else .2 if board in ('创业板', '科创板') else .1
            # 实际前瞻记账只接受当次合格股票；历史研究保留原池以便比较。
            valid_dates = None if start is None else {
                d for i, d in enumerate(calendar) if d >= start and stock['code'] in names
                and not any(x in names[stock['code']].upper() for x in ('ST', '退'))
                and history_issue([b for b in histories[stock['code']] if b['date'] <= d], calendar[:i+1], d) is None}
            per_stock[stock['code']] = run(histories[stock['code']], ratio, entry_rule='breakout',
                exit_ma=20, min_entry_date=start, trading_calendar=calendar, allowed_signal_dates=valid_dates)
        curve = equity_curve(histories, per_stock)
        results[label] = {
            'period': period_metrics(curve, start or '2026-01-01', calendar[-1]),
            'positions': {c: r['open_position'] for c, r in per_stock.items() if r['open_position']},
            'pending': {c: r['pending'] for c, r in per_stock.items() if r['pending']},
            'by_stock': per_stock, 'curve': curve}
    report = {'generated_at': now.isoformat(), 'data_as_of': calendar[-1], 'strategy': 'breakout_ma20_fixed_baseline',
              'universe_snapshot': config['universe_snapshot'], 'universe_size': len(stocks),
              'forward_start': config['forward_start'], 'sources': sources,
              'selection': ranked[:config['top_n']], 'eligible_signals': sum(not x['unmet'] for x in ranked),
              'excluded': excluded, 'results': results,
              'method': '全池固定初始等权独立资金，允许碎股；含未平仓盯市；单边成本0.15%；排名只用于展示，不改变全池组合仓位',
              'execution': '完整收盘信号，下一交易日开盘模拟；盘中输出为上一完整交易日复盘，不追认已错过的开盘成交。观察股不买入。',
              'limitations': ['股票池事后选择；历史收益非实盘，非独立样本外验证',
                             '未复权；缺少完整历史ST、除权、停牌、真实涨跌停与队列数据',
                             '前瞻模拟按最新快照重算，可能被数据修订；不是不可变成交账本',
                             '前瞻模拟使用本次名称检查，历史时点状态尚未还原',
                             '前5名不是5只集中持仓组合，不能套用全池历史收益']}
    # 保存可复算的取数快照，再原子替换报告；失败不冒充已更新。
    cache = ROOT / 'markets/a/.cache/simulation'
    cache.mkdir(parents=True, exist_ok=True)
    snapshot = cache / (now.strftime('%Y%m%d-%H%M%S') + '.json.gz')
    with gzip.open(snapshot, 'wt', encoding='utf-8') as f:
        json.dump({'histories': histories, 'index': index, 'quotes': current, 'config': config}, f, ensure_ascii=False)
    report['snapshot_file'] = str(snapshot.relative_to(ROOT))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({k: report[k] for k in ('generated_at', 'data_as_of', 'eligible_signals', 'selection')}, ensure_ascii=False))
    print('收益:', results['historical_replay']['period'], '前瞻:', results['forward_simulation']['period'])


if __name__ == '__main__':
    main()
