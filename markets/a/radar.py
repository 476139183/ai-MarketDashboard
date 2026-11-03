#!/usr/bin/env python3
"""A 股盘后雷达：按最近完整交易日扫描，给出次日观察名单。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from candidate_monitor import history_issue, signal_category
from daily_strategy import entry_signal
from hot_backtest import as_of_date, load, prepare_symbols
from hot_source import fetch_names, fetch_rank, now as hot_now
from large_evaluate import market_above_ma20
from large_universe import exclusion_reason, quotes
from trade_plan import build_trade_plan

OUT = Path(__file__).resolve().parent / 'radar-latest.json'
HOT = Path(__file__).resolve().parent / 'hot-universe.json'
BJ = ZoneInfo('Asia/Shanghai')
RULE_LABEL = {'trend': '趋势延续', 'reclaim': '收复均线', 'breakout': '突破前高'}


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
        return f'次日仅观察：{labels}；未达趋势候选门槛'
    return f'次日仅观察：{labels}；沪深300未站上MA20，偏防守'


def scan(*, force_hot=False, workers=12, max_stocks=100):
    """扫描最近完整交易日，返回次日观察雷达。"""
    hot = _load_hot(force=force_hot)
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
            bars, source, _ = load(stock['code'], signal_date, stock['board'])
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

    strategy, watch, excluded = [], [], []
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
        float_cap = (quote or {}).get('float_cap_yi')
        reason = exclusion_reason(name, float_cap, turnover)
        if reason:
            excluded.append({'code': code, 'name': name, 'reason': reason})
            continue
        issue = history_issue(bars, calendar, signal_date)
        if issue:
            excluded.append({'code': code, 'name': name, 'reason': issue})
            continue
        signals = [rule for rule in ('trend', 'reclaim', 'breakout') if entry_signal(bars, rule)]
        if not signals:
            continue
        close = float(bars[-1]['close'])
        row = {
            'code': code,
            'name': name,
            'board': stock['board'],
            'rules': signals,
            'rule_labels': [RULE_LABEL[r] for r in signals],
            'category': signal_category(signals, market_pass),
            'market_pass': market_pass,
            'close': round(close, 4),
            'change_pct': None,
            'note': '',
            'observe_for': '下一交易日',
        }
        if len(bars) >= 2 and float(bars[-2]['close']) > 0:
            row['change_pct'] = round((close / float(bars[-2]['close']) - 1) * 100, 2)
        row['note'] = _watch_note(row['category'], signals, market_pass)
        plan = build_trade_plan(bars)
        if plan:
            row['trade_plan'] = plan
            row['sniper'] = plan['sniper']
            row['checklist'] = plan['checklist']
            row['plan_ready'] = plan['plan_ready']
            row['summary'] = plan['summary']
            row['note'] = row['note'] + '；' + plan['summary']
        (strategy if row['category'] == '策略候选' else watch).append(row)

    strategy.sort(key=lambda x: (-('trend' in x['rules']), x['code']))
    watch.sort(key=lambda x: x['code'])
    payload = {
        'title': 'A股盘后雷达',
        'signal_date': signal_date,
        'observe_session': '下一交易日',
        'generated_at': now.isoformat(timespec='seconds'),
        'market_pass': market_pass,
        'market_label': '沪深300收盘站上MA20' if market_pass else '沪深300未站上MA20（偏防守）',
        'universe_size': len(accepted),
        'scanned': len(histories),
        'strategy_count': len(strategy),
        'watch_count': len(watch),
        'strategy_candidates': strategy,
        'watch_pool': watch,
        'excluded_count': len(excluded) + len(rejected),
        'limitations': [
            '热榜股票池有幸存者/人气偏差，不是全市场扫描',
            '收盘后判断、次日观察；点位为回踩预案，不等于自动下单或保证开盘可买',
            '作战计划参考乖离<5%、MA多头与MA5/MA10/MA20结构，属研究规则',
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
