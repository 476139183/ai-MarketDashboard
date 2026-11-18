#!/usr/bin/env python3
"""按当次全A列表扫描完整日线，只输出合格突破股；不回填历史全市场收益。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from hot_backtest import as_of_date
from simulation import fetch_index, rank_stocks
from strategy import classify
from trend_exit import assess_exit, fetch_flows

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'markets/a/.cache/full-market'
HOST = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'


def fetch_json(url):
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                      'Referer': 'https://finance.sina.com.cn/'}), timeout=15) as r:
        return json.load(r)


def fetch_universe():
    count = int(fetch_json(HOST + 'Market_Center.getHQNodeStockCount?node=hs_a'))
    if count < 5000:
        raise ValueError('全A列表数量异常，停止扫描，不能冒充全市场')
    def page(number):
        return fetch_json(HOST + 'Market_Center.getHQNodeData?' + urlencode(
            {'page': number, 'num': 100, 'sort': 'symbol', 'asc': 1, 'node': 'hs_a', 'symbol': ''}))
    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(page, range(1, math.ceil(count/100)+1)):
            if not isinstance(part, list):
                raise ValueError('股票列表分页异常')
            rows.extend(part)
    if len(rows) != count or len({x['symbol'] for x in rows}) != count:
        raise ValueError('股票列表分页缺失或重复，停止扫描')
    stocks, excluded = [], {}
    for row in rows:
        code, name = row['code'], row['name']
        if 'ST' in name.upper() or '退' in name:
            excluded[code] = '当前风险警示或退市名称'
            continue
        try:
            if row['symbol'].startswith('bj'):
                exchange, board = 'BJ', '北交所'
            else:
                symbol, board = classify(code)
                exchange = symbol[:2].upper()
            stocks.append({'code': code, 'name': name, 'exchange': exchange, 'board': board})
        except ValueError as exc:
            excluded[code] = str(exc)
    return count, stocks, excluded


def fetch_recent(stock, end, count=60):
    symbol = stock['exchange'].lower() + stock['code']
    url = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketData.getKLineData?' + urlencode(
        {'symbol': symbol, 'scale': 240, 'ma': 'no', 'datalen': count})
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=12) as response:
        text = response.read().decode('utf-8')
    start, stop = text.find('(['), text.rfind('])')
    if start < 0 or stop < 0:
        raise ValueError('日线响应格式异常')
    raw = json.loads(text[start+1:stop+1])
    bars = [dict(date=x['day'][:10], **{k: float(x[k]) for k in ('open', 'high', 'low', 'close', 'volume')})
            for x in raw if x['day'][:10] <= end]
    if not bars or any(a['date'] >= b['date'] for a, b in zip(bars, bars[1:])):
        raise ValueError('日线为空、重复或乱序')
    return bars


def exclude_locked_signals(rows, histories, metadata):
    kept, excluded = [], {}
    for row in rows:
        code = row['code']
        bars = histories[code]
        board = metadata[code]['board']
        ratio = .3 if board == '北交所' else .2 if board in ('创业板', '科创板') else .1
        if bars[-1]['high'] == bars[-1]['low']:
            excluded[code] = '全天一字线，不作为可执行候选'
        elif bars[-1]['close'] >= bars[-2]['close'] * (1+ratio) * .995:
            excluded[code] = '接近按前收估算的涨停，真实限价及排队尚未核验'
        else:
            kept.append(row)
    return kept, excluded


def qualification_changes(current, previous):
    now = {r['code'] for r in current}
    before = {r['code'] for r in previous}
    return {'new_codes': sorted(now-before), 'continuing_codes': sorted(now & before),
            'no_longer_qualified_codes': sorted(before-now)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'markets/a/market-scan-latest.json')
    args = parser.parse_args()
    config=json.loads((ROOT/'markets/a/simulation-config.json').read_text())
    entry_profile=config.get('entry_profile','legacy_breakout')
    exit_policy=config.get('exit_policy','trend_flow')
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    index = fetch_index(as_of_date(now))
    calendar = [x['date'] for x in index]
    day = calendar[-1]
    if (now.date() - datetime.fromisoformat(day).date()).days > 4:
        raise ValueError('完整日线超过4个自然日未更新，请核验交易日历；旧报告保留')
    count, stocks, excluded = fetch_universe()
    print(f'当次全A列表 {count}，名称/板块筛选后 {len(stocks)}，完整日线截至 {day}', flush=True)
    previous_path = ROOT / 'markets/a/paper-account.json'
    account = json.loads(previous_path.read_text()) if previous_path.exists() else None
    if account and account.get('entry_profile','legacy_breakout')!=entry_profile:
        if account['events'] or account['positions']:
            raise ValueError('已有模拟成交，不允许混入不同入场策略；应先建立独立研究账户')
        account=None
    tracked = []
    current_codes = {s['code'] for s in stocks}
    for code, position in (account['positions'] if account else {}).items():
        if code not in current_codes:
            exchange = 'BJ' if position['board'] == '北交所' else classify(code)[0][:2].upper()
            tracked.append({'code': code, 'name': 'ST状态待核验的原持仓',
                            'board': position['board'], 'exchange': exchange})
    load_stocks = stocks + tracked
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (day + '-daily.json.gz')
    histories = json.load(gzip.open(path, 'rt', encoding='utf-8')) if path.exists() else {}
    failures = {}
    missing = [s for s in load_stocks if s['code'] not in histories]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_recent, s, day): s for s in missing}
        for number, future in enumerate(as_completed(futures), 1):
            stock = futures[future]
            try:
                histories[stock['code']] = future.result()
            except Exception as exc:
                failures[stock['code']] = str(exc)
            if number % 500 == 0 or number == len(futures):
                print(f'日线 {number}/{len(missing)}，请求失败 {len(failures)}', flush=True)
    # 即使源部分失败，也保存已取数据便于重试；报告列出实际覆盖，不冒充全覆盖。
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(histories, f, ensure_ascii=False)
    names = {s['code']: s['name'] for s in stocks}
    current_histories = {c: b for c, b in histories.items() if c in names}
    if len(current_histories) < len(stocks)*.9:
        raise ValueError('日线请求成功不足90%，不更新选股报告；可重试使用已取缓存')
    ranked, invalid = rank_stocks(current_histories, names, calendar)
    excluded.update(invalid)
    breadth={}
    if entry_profile!='legacy_breakout':
        from adaptive_strategy import rank_adjusted,market_breadth
        breadth=market_breadth(current_histories,calendar)
        if entry_profile=='original_breakout_breadth':
            from adaptive_strategy import breadth_allowed
            from selection_study import rank_current_original
            ranking=config.get('selection_ranking','breakout_desc')
            ranked=rank_current_original(current_histories,names,calendar,ranking) if day in breadth_allowed(breadth,calendar) else []
        else:
            ranked=rank_adjusted(current_histories,names,calendar,index,entry_profile,breadth)
    technical_count = len(ranked)
    by_code = {s['code']: s for s in load_stocks}
    ranked, locked = exclude_locked_signals(ranked, current_histories, by_code)
    excluded.update(locked)
    previous_histories = {c: b[:-1] for c,b in current_histories.items() if b and b[-1]['date']==day}
    previous_ranked, _ = rank_stocks(previous_histories, names, calendar[:-1])
    if entry_profile!='legacy_breakout':
        if entry_profile=='original_breakout_breadth':
            previous_ranked=rank_current_original(previous_histories,names,calendar[:-1],ranking) if calendar[-2] in breadth_allowed(breadth,calendar[:-1]) else []
        else:
            previous_ranked=rank_adjusted(previous_histories,names,calendar[:-1],index[:-1],entry_profile,breadth)
    previous_ranked, _ = exclude_locked_signals(previous_ranked, previous_histories, by_code)
    changes = qualification_changes(ranked, previous_ranked)
    continued = set(changes['continuing_codes'])
    for row in ranked:
        row['qualification'] = '持续符合' if row['code'] in continued else '新入选'
    chosen = ranked[:5]
    # 资金流只为最终候选和实际模拟持仓取数；失败时保留原因，不用量价代理冒充。
    flow_status, flows = {}, {}
    flow_codes = {x['code'] for x in chosen} | set(account['positions'] if account else {})
    for code in sorted(flow_codes):
        if code not in by_code:
            flow_status[code] = '已离开当次股票列表，持仓仍须核验'
            continue
        try:
            flows[code] = fetch_flows(by_code[code], day)
            flow_status[code] = '东方财富主力净额估算，已取数'
        except Exception as exc:
            flow_status[code] = '资金流缺失：' + str(exc)
    for row in chosen:
        row['trend_assessment'] = assess_exit(current_histories[row['code']], row['close'], flows.get(row['code']))
    # 只保留信号日盘后实际采集的候选，盘中补跑不能追认今晨成交。
    prospective = now.date().isoformat() == day and now.hour >= 17
    signals_path = CACHE / (day + '-'+entry_profile+'-signals.json')
    if prospective and not signals_path.exists():
        signals_path.write_text(json.dumps({'signal_date': day, 'recorded_at': now.isoformat(),
                                           'entry_profile':entry_profile,'stocks': chosen}, ensure_ascii=False, indent=2)+'\n')
    from paper_account import update_account
    signals = [json.loads(p.read_text()) for p in sorted(CACHE.glob('*-signals.json'))]
    signals=[s for s in signals if s.get('entry_profile','legacy_breakout')==entry_profile]
    account = update_account(account, histories, by_code, calendar, signals, flows, now.date().isoformat(),
                             exit_policy=exit_policy,breadth=breadth)
    account['entry_profile']=entry_profile
    report = {'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'scan_started_at': now.isoformat(),
              'data_as_of': day, 'scope': '当次全A列表动态扫描',
              'total_listed': count, 'screened': len(stocks), 'history_downloaded': len(current_histories),
              'history_failed': failures, 'excluded': excluded, 'eligible_count': len(ranked),
              'selection': chosen, 'all_eligible': ranked, 'technical_signal_count': technical_count,
              'qualification_changes': changes,
              'flow_status': flow_status, 'fund_flows': flows,
              'entry_profile':entry_profile,'exit_policy':exit_policy,
              'market_breadth_ratio':breadth.get(day),
              'simulation_status':config.get('simulation_status','未完成盈利验证'),
              'fund_flow_usage':'参考记录；本次价格与趋势覆盖实验不使用资金流' if entry_profile!='legacy_breakout' else '退出条件之一，缺数时仅按价格分支',
              'monthly_target_pct': 5, 'account': account, 'prospective_signal_saved': prospective,
              'limitations': ['当次列表不是历史时点股票池，不能反推历史动态组合月收益',
                             '新浪未复权日线；真实复权、停牌和涨跌停队列尚未还原',
                             '资金流是供应商大单分类估算，数据缺失明确显示，不当作确认撤离',
                             '收盘确认退出，下一交易日尝试；尚未接入可验证的盘中资金流与自动盯盘',
                             '新组合为前5名共享现金模拟，历史固定60股收益不适用；允许碎股、每边成本0.15%']}
    validation_path = ROOT / 'markets/a/strategy-validation.json'
    if validation_path.exists():
        validation = json.loads(validation_path.read_text())
        report['strategy_validation'] = {
            'report': str(validation_path.relative_to(ROOT)),
            'price_rules_status': validation['results']['year_structure'].get('validation_status','需读取详细报告'),
            'fund_rules_status': validation['results']['matched_flow'].get('validation_status','需读取详细报告')}
    adjustment_path=ROOT/'markets/a/strategy-adjustment-phase2.json'
    if entry_profile!='legacy_breakout' and adjustment_path.exists():
        adjustment=json.loads(adjustment_path.read_text())
        selected=adjustment.get('selected_by_training')
        if selected==entry_profile+'__'+exit_policy:
            row=adjustment['results'][selected]
            report['baseline_validation']=report.get('strategy_validation')
            report['strategy_validation']={'report':str(adjustment_path.relative_to(ROOT)),
                'status':'未通过盈利与每月5%目标，仅作研究模拟',
                'historical_year':row['year'],'locked_check_jul_oct':row['holdout_jul_oct'],
                'full_months_at_5pct':row['year_full_months_at_5pct'],
                'warning':'历史研究收益不是当前前瞻账户收益，保留区间此前已被观察'}
    original_study=ROOT/'markets/a/original-market-risk-study.json'
    if entry_profile=='original_breakout_breadth' and original_study.exists():
        study=json.loads(original_study.read_text())
        selected=study.get('selected_by_training')
        if selected==ranking+'__'+exit_policy:
            row=study['results'][selected]
            report['strategy_validation']={'report':str(original_study.relative_to(ROOT)),
                'status':'全年研究回放转正，7至10月检查亏损；未通过稳定盈利与每月5%目标',
                'historical_year':row['year'],'locked_check_jul_oct':row['check_jul_oct'],
                'full_months_at_5pct':row['months_at_5pct'],
                'warning':'全量扫描、原突破入场；历史已被观察，不是独立样本外或前瞻账户收益'}
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(args.output)
    previous_path.write_text(json.dumps(account, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('data_as_of', 'total_listed', 'history_downloaded', 'eligible_count', 'selection')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
