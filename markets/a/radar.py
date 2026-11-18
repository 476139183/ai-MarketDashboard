#!/usr/bin/env python3
"""A 股盘后雷达：按最近完整交易日扫描，给出次日观察名单。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from candidate_monitor import history_issue
from daily_strategy import entry_signal
from hot_backtest import as_of_date, load, prepare_symbols
from hot_source import fetch_names, fetch_rank, now as hot_now
from large_evaluate import market_above_ma20
from large_universe import exclusion_reason, quotes
from trade_plan import build_trade_plan
from stock_setups import VERSION as STRATEGY_VERSION, LABELS as SETUP_LABELS, evaluate_setups, selected_setup
import continuation as C
import oversold_reversal as M

OUT = Path(__file__).resolve().parent / 'radar-latest.json'
HOT = Path(__file__).resolve().parent / 'hot-universe.json'
BJ = ZoneInfo('Asia/Shanghai')
RULE_LABEL = {'trend': '重返MA20（原信号）', 'reclaim': '收复均线（原信号）', 'breakout': '突破前高（原信号）', **SETUP_LABELS}


def _now():
    return datetime.now(BJ)


def _refresh_hot_universe():
    popularity = fetch_rank('getAllCurrentList', include_market=True)
    rising = fetch_rank('getAllHisRcList')
    stocks = {}
    for label, snapshot, rank_key in (('popularity', popularity, 'rk'), ('rising', rising, 'hrcrk')):
        for item in snapshot['raw']:
            symbol = item.get('sc', '')
            if len(symbol) != 8 or symbol[:2] not in ('SH', 'SZ') or not symbol[2:].isdigit():
                raise ValueError(f'无法解析榜单证券代码: {symbol!r}')
            code = symbol[2:]
            row = stocks.setdefault(code, {'code': code, 'name': None, 'rank': item.get('rk'),
                                           'source_symbol': symbol,
                                           'exchange': 'BJ' if code.startswith(('4', '8', '92')) else symbol[:2],
                                           'lists': {}})
            if label == 'popularity':
                row['rank'] = item.get('rk')
            row['lists'][label] = {'list_rank': item.get(rank_key), 'popularity_rank': item.get('rk')}
    names = fetch_names([x['source_symbol'] for x in stocks.values()])
    for code, row in stocks.items():
        row['name'] = names.get(code)
    snapshot = {
        'generated_at': hot_now(),
        'source': '东方财富股吧当前人气榜及人气飙升榜',
        'stocks': sorted(stocks.values(), key=lambda x: (x['rank'] if isinstance(x['rank'], int) else 999999, x['code'])),
        'missing_names': [x['code'] for x in stocks.values() if not x['name']],
    }
    HOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return snapshot


def _load_hot(force=False):
    if not force and HOT.exists():
        snap = json.loads(HOT.read_text(encoding='utf-8'))
        generated = datetime.fromisoformat(snap['generated_at'])
        if _now() - generated < timedelta(hours=6):
            return snap
    return _refresh_hot_universe()


def _hs300(end):
    errors=[];best=None
    for fetch in (lambda:_hs300_tencent(end),lambda:_hs300_sina(end)):
        try:
            rows=fetch()
            rows=[r for r in rows if r['date']<=end]
            if len(rows)<30 or any(a['date']>=b['date'] for a,b in zip(rows,rows[1:])):
                raise ValueError('指数日线不足或乱序')
            if rows[-1]['date']==end:return rows
            if best is None or rows[-1]['date']>best[-1]['date']:best=rows
        except Exception as exc:errors.append(str(exc)[:120])
    if best is not None:return best  # 周末/休市时由调用方明确回退到实际最后交易日。
    raise ValueError('沪深300日线不可用：'+'；'.join(errors))


def _hs300_sina(end):
    params=urlencode(dict(symbol='sh000300',scale=240,ma='no',datalen=1023))
    url='https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketData.getKLineData?'+params
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as response:text=response.read().decode('utf-8')
    start,stop=text.find('(['),text.rfind('])')
    if start<0 or stop<0:raise ValueError('新浪指数日线格式异常')
    return [dict(date=r['day'][:10],close=float(r['close'])) for r in json.loads(text[start+1:stop+1])]


def _hs300_tencent(end):
    rows = []
    for start, stop in (('2025-01-01', end), ('2024-01-01', '2024-12-31')):
        if stop < start:
            continue
        query = urlencode({'param': f'sh000300,day,{start},{stop},1200,qfq'})
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
            payload = json.load(response)['data']['sh000300']
            day = payload.get('day') or payload.get('qfqday') or []
        rows.extend({'date': x[0], 'close': float(x[2])} for x in day)
    rows = sorted({x['date']: x for x in rows}.values(), key=lambda x: x['date'])
    if len(rows) < 30 or any(a['date'] >= b['date'] for a, b in zip(rows, rows[1:])):
        raise ValueError('沪深300日线不足或乱序')
    return [x for x in rows if x['date'] <= end]


def _watch_note(category, rules, market_pass):
    labels = '、'.join(RULE_LABEL.get(r, r) for r in rules)
    if category == '策略候选':
        return f'次日优先观察（做多预案）：{labels}；大盘过滤通过'
    if market_pass:
        return f'次日仅观察：{labels}；分型量价确认或买入计划未通过'
    return f'次日仅观察：{labels}；大盘沪深300未站上MA20，偏防守'


def _latest_daily(code, board):
    """腾讯最新窗口未复权日线；成交量由手统一为股，不拼接异源价格。"""
    prefix = 'bj' if board == '北交所' else ('sh' if code.startswith('6') else 'sz')
    symbol = prefix + code
    query = urlencode({'param': f'{symbol},day,,,220,'})
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
        raw = json.loads(response.read())['data'][symbol].get('day') or []
    rows = [dict(date=x[0], open=float(x[1]), close=float(x[2]), high=float(x[3]),
                 low=float(x[4]), volume=float(x[5]) * 100) for x in raw]
    return rows, '腾讯最新窗口未复权日线（成交量：股）'


def _current_history(stock, signal_date):
    errors = []
    for fetch in (lambda: load(stock['code'], signal_date, stock['board'])[:2],
                  lambda: _latest_daily(stock['code'], stock['board'])):
        try:
            bars, source = fetch()
            bars = [b for b in bars if b['date'] <= signal_date]
            if not bars or bars[-1]['date'] != signal_date:
                raise ValueError('信号日日线未落地')
            if any(a['date'] >= b['date'] for a, b in zip(bars, bars[1:])):
                raise ValueError('日线重复或乱序')
            return bars, source
        except Exception as exc:
            errors.append(str(exc)[:120])
    raise ValueError('信号日日线不可用：' + '；'.join(errors))


def scan(*, force_hot=False, workers=12, max_stocks=None, enable_continuation=None,enable_reversal=None):
    """扫描最近完整交易日，返回次日观察雷达。"""
    hot = _load_hot(force=force_hot)
    if enable_continuation is None:enable_continuation=C.RELEASED
    if enable_reversal is None:enable_reversal=M.RELEASED or M.PAPER_TRIAL
    if max_stocks is None:max_stocks=200 if enable_continuation or enable_reversal else 100
    now = _now()
    signal_date = as_of_date(now)
    calendar_end = signal_date
    accepted, rejected = prepare_symbols(hot['stocks'][:max_stocks])
    quote_map = quotes(accepted)
    benchmark = _hs300(calendar_end)
    calendar = [x['date'] for x in benchmark]
    if signal_date not in calendar and calendar:
        # 数据源尚未落到信号日：回退到指数最后一日
        signal_date = calendar[-1]
    market_pass = signal_date in market_above_ma20(benchmark)
    histories, failures = {}, {}

    def one(stock):
        try:
            bars, source = _current_history(stock, signal_date)
            return stock['code'], bars, source, None
        except Exception as exc:  # noqa: BLE001 - 单票失败不影响整池
            return stock['code'], None, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, stock) for stock in accepted]
        for fut in as_completed(futs):
            code, bars, source, err = fut.result()
            if err:
                failures[code] = err
            else:
                histories[code] = {'bars': bars, 'source': source}

    strategy, watch, excluded, continuations, reversals = [], [], [], [], []
    for stock in accepted:
        code = stock['code']
        quote = quote_map.get(code)
        name = (quote or {}).get('name') or stock.get('name') or code
        bars_pack = histories.get(code)
        if not bars_pack:
            excluded.append({'code': code, 'name': name, 'reason': failures.get(code, '行情缺失')})
            continue
        bars = [b for b in bars_pack['bars'] if b['date'] <= signal_date]
        if not bars or bars[-1]['date'] != signal_date:
            excluded.append({'code': code, 'name': name, 'reason': '信号日日线未落地'})
            continue
        turnover = (sum(float(x['close']) * float(x['volume']) for x in bars[-20:]) / 20
                    if len(bars) >= 20 else None)
        continuation=C.build_plan(bars,benchmark,name) if enable_continuation and stock['board']!='北交所' else None
        if continuation:
            continuations.append(dict(code=code,name=name,board=stock['board'],rules=['strong_continuation'],
                rule_labels=['强势延续确认'],category='策略候选',strategy_version=C.VERSION,
                confirmed_rules=[],setup_plans=[],market_pass=True,close=float(bars[-1]['close']),
                change_pct=None,observe_for='下一交易日',trade_plan=continuation,
                sniper=continuation['sniper'],checklist=continuation['checklist'],plan_ready=True,
                summary=continuation['summary'],note=continuation['summary']))
        reversal=M.build_plan(bars,benchmark,name,version=M.active_version()) if enable_reversal and stock['board']!='北交所' else None
        if reversal:
            reversals.append(dict(code=code,name=name,board=stock['board'],rules=['oversold_reversal'],
                rule_labels=['短期超跌反转'],category='策略候选',strategy_version=M.VERSION,
                confirmed_rules=[],setup_plans=[],market_pass=True,close=float(bars[-1]['close']),
                change_pct=None,observe_for='下一交易日',trade_plan=reversal,sniper=reversal['sniper'],
                checklist=reversal['checklist'],plan_ready=True,summary=reversal['summary'],note=reversal['summary']))
        float_cap = (quote or {}).get('float_cap_yi')
        reason = exclusion_reason(name, float_cap, turnover)
        if reason:
            excluded.append({'code': code, 'name': name, 'reason': reason})
            continue
        issue = history_issue(bars, calendar, signal_date)
        if not issue and (len(bars)<25 or [b['date'] for b in bars[-25:]]!=calendar[-25:]):
            issue = '分型需要最近25个市场交易日完整日线'
        if issue:
            excluded.append({'code': code, 'name': name, 'reason': issue})
            continue
        legacy_signals = [rule for rule in ('trend', 'reclaim', 'breakout') if entry_signal(bars, rule)]
        setups = evaluate_setups(bars)
        setup = selected_setup(setups)
        signals = [s['rule'] for s in setups] or legacy_signals
        if not signals:
            continue
        close = float(bars[-1]['close'])
        row = {
            'code': code,
            'name': name,
            'board': stock['board'],
            'rules': signals,
            'rule_labels': [RULE_LABEL[r] for r in signals],
            'category': '策略候选' if market_pass and setup and setup['plan']['plan_ready'] else '观察池',
            'strategy_version': STRATEGY_VERSION,
            'confirmed_rules': [s['rule'] for s in setups],
            'setup_plans': setups,
            'market_pass': market_pass,
            'close': round(close, 4),
            'change_pct': None,
            'note': '',
            'observe_for': '下一交易日',
        }
        if len(bars) >= 2 and float(bars[-2]['close']) > 0:
            row['change_pct'] = round((close / float(bars[-2]['close']) - 1) * 100, 2)
        row['note'] = _watch_note(row['category'], signals, market_pass)
        plan = setup['plan'] if setup else build_trade_plan(bars)
        if not setup and plan:
            plan = dict(plan, plan_ready=False, summary='仅原始观察信号，未满足分型量价确认')
        if plan:
            row['trade_plan'] = plan
            row['sniper'] = plan['sniper']
            row['checklist'] = plan['checklist']
            row['plan_ready'] = plan['plan_ready']
            row['summary'] = plan['summary']
            row['note'] = row['note'] + '；' + plan['summary']
        (strategy if row['category'] == '策略候选' else watch).append(row)

    existing={r['code'] for r in strategy}
    reversals=sorted((r for r in reversals if r['code'] not in existing),
        key=lambda r:(-r['trade_plan']['close_location'],r['trade_plan']['rsi2'],-r['trade_plan']['avg20_turnover'],r['code']))[:M.MAX_CANDIDATES]
    existing.update(r['code'] for r in reversals)
    continuations=sorted((r for r in continuations if r['code'] not in existing),
                         key=lambda r:(-r['trade_plan']['relative_strength'],-r['trade_plan']['avg20_turnover'],r['code']))[:C.MAX_CANDIDATES]
    added={r['code'] for r in continuations+reversals}
    legacy_exclusion_summary=dict(Counter(r['reason'] for r in excluded + rejected))
    strategy.extend(continuations+reversals)
    watch=[r for r in watch if r['code'] not in added]
    excluded=[r for r in excluded if r['code'] not in added]
    strategy.sort(key=lambda x: (x['trade_plan'].get('entry_mode')=='continuation',x['code']))
    watch.sort(key=lambda x: x['code'])
    payload = {
        'title': 'A股盘后雷达',
        'signal_date': signal_date,
        'observe_session': '下一交易日',
        'generated_at': now.isoformat(timespec='seconds'),
        'strategy_version': STRATEGY_VERSION,
        'entry_revision': M.active_version() if enable_reversal else C.ENTRY_REVISION,
        'continuation_enabled': enable_continuation,
        'continuation_market_pass': C.market_allowed(benchmark),
        'continuation_count': len(continuations),
        'oversold_reversal_enabled':enable_reversal,
        'oversold_reversal_market_pass':M.market_allowed(benchmark),
        'oversold_reversal_paper_trial':bool(enable_reversal and M.PAPER_TRIAL and not M.RELEASED),
        'reversal_count':len(reversals),
        'legacy_exclusion_summary': legacy_exclusion_summary,
        'confirmed_count': sum(bool(r['confirmed_rules']) for r in strategy + watch),
        'market_pass': market_pass,
        'market_label': '沪深300收盘站上MA20' if market_pass else '沪深300未站上MA20（偏防守）',
        'universe_size': len(accepted),
        'scanned': len(histories),
        'data_ready': bool(histories),
        'daily_ready_count': len(histories),
        'daily_unavailable_count': len(failures),
        'strategy_count': len(strategy),
        'watch_count': len(watch),
        'strategy_candidates': strategy,
        'watch_pool': watch,
        'excluded_count': len(excluded) + len(rejected),
        'exclusion_summary': dict(Counter(r['reason'] for r in excluded + rejected)),
        'limitations': [
            '热榜股票池有幸存者/人气偏差，不是全市场扫描',
            '收盘后判断、次日观察；点位为回踩预案，不等于自动下单或保证开盘可买',
            '分型v2：重返MA20确认、持续趋势回踩、放量突破；各自检查量价与计划，仍属待验证研究规则',
            '个股确认与大盘允许交易分开；大盘防守时仅观察，次日须按整分回踩计划到价',
            '强势延续为独立待确认入口：指数温和走弱时以半预算、0.25%权益风险等待盘中确认；急跌禁入',
            '超跌反转按昨日均值计划、盘中反转与净空间执行，风险预算0.5%权益；试运行不代表收益已验证',
            '普通A股现货默认只研究做多方向',
            '未完整处理除权、停牌与真实涨跌停队列',
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def load_cached(*, max_age_hours=12):
    if not OUT.exists():
        return None
    data = json.loads(OUT.read_text(encoding='utf-8'))
    generated = datetime.fromisoformat(data['generated_at'])
    if _now() - generated > timedelta(hours=max_age_hours):
        data = dict(data)
        data['stale'] = True
    else:
        data['stale'] = False
    return data


def get_radar(*, refresh=False):
    if not refresh:
        cached = load_cached()
        if cached is not None and not cached.get('stale'):
            return cached
    return scan(force_hot=refresh)


if __name__ == '__main__':
    result = scan(force_hot=True)
    print(f"{result['signal_date']}: 策略候选 {result['strategy_count']}，观察池 {result['watch_count']}，"
          f"大盘 {result['market_label']}")
    for row in result['strategy_candidates'][:10]:
        print('★', row['code'], row['name'], row['rule_labels'], row['close'])
    for row in result['watch_pool'][:10]:
        print('·', row['code'], row['name'], row['rule_labels'], row['close'])
