#!/usr/bin/env python3
"""按公开板块成分和市值、成交额构建可复核样本。"""
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from replay import fetch, replay
from strategy import classify

URL = 'https://push2.eastmoney.com/api/qt/clist/get'


def query(fs, fields, page=1, size=100):
    params = {'pn': page, 'pz': size, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
              'fid': 'f3', 'fs': fs, 'fields': fields}
    with urlopen(Request(URL + '?' + urlencode(params), headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as r:
        data = json.load(r)['data']
    rows = data['diff'] or []
    return list(rows.values()) if isinstance(rows, dict) else rows, data['total']


def all_rows(fs, fields):
    result, total = query(fs, fields)
    for page in range(2, (total + 99) // 100 + 1):
        rows, _ = query(fs, fields, page)
        result.extend(rows)
    return result


SECTORS = [('行业', '银行', 'BK0475'), ('行业', '证券', 'BK0473'),
           ('行业', '白酒', 'BK1277'), ('行业', '半导体', 'BK1036'),
           ('行业', '电池', 'BK1033'), ('行业', '汽车', 'BK1211'),
           ('行业', '医药生物', 'BK1216'), ('行业', '电力', 'BK0428'),
           ('题材', '人形机器人', 'BK1184'), ('题材', '存储芯片', 'BK1137'),
           ('题材', '人工智能', 'BK0800'), ('题材', '算力概念', 'BK1134'),
           ('题材', '新能源车', 'BK0900'), ('题材', '固态电池', 'BK0968')]
ANCHORS = {'人形机器人': '688836', '存储芯片': '688825'}


def positive(value):
    return float(value) if isinstance(value, (int, float)) and value > 0 else 0


def main():
    sectors = []
    candidates = {}
    for category, name, board in SECTORS:
        try:
            rows = all_rows('b:' + board, 'f12,f14,f20,f6,f26')
            eligible = []
            for x in rows:
                try:
                    classify(x['f12'])
                    if 'ST' not in x['f14'].upper() and positive(x.get('f20')) and positive(x.get('f6')):
                        eligible.append(x)
                except ValueError:
                    pass
            # 候选池是市值前 30 与当日成交额前 30 的并集，不声称全板块综合排名。
            short = {x['f12']: x for key in ('f20', 'f6')
                     for x in sorted(eligible, key=lambda v: positive(v.get(key)), reverse=True)[:30]}
            anchor = ANCHORS.get(name)
            if anchor:
                short.update({x['f12']: x for x in eligible if x['f12'] == anchor})
            candidates.update(short)
            sectors.append(dict(category=category, name=name, board=board, total_members=len(rows),
                                eligible_members=len(eligible), candidates=list(short)))
            print(f'板块 {name}: {len(rows)} 个成分，候选 {len(short)}', flush=True)
        except Exception as exc:
            sectors.append(dict(category=category, name=name, board=board, error=str(exc)))
            print(f'板块 {name} 获取失败: {exc}', flush=True)
    fetched, errors = {}, {}
    print(f'获取 {len(candidates)} 只候选的 15 分钟行情', flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, code): code for code in candidates}
        for i, future in enumerate(as_completed(futures), 1):
            code = futures[future]
            try:
                _, board, bars = future.result()
                by_day = defaultdict(list)
                for b in bars:
                    by_day[b['time'][:10]].append(b)
                full_days = [v for v in by_day.values() if v[0]['time'][11:] == '09:45' and v[-1]['time'][11:] == '15:00']
                if len(full_days) < 20:
                    raise ValueError('不足20个完整交易日，无法计算近20日成交额')
                daily = [sum(x['amount'] for x in day) for day in full_days[-20:]]
                if min(daily) <= 0:
                    raise ValueError('成交额缺失')
                listed = str(candidates[code].get('f26') or '')
                listing = f'{listed[:4]}-{listed[4:6]}-{listed[6:8]}' if len(listed) == 8 else None
                min_entry_date = None
                if not listing:
                    raise ValueError('缺少上市日期，不能判断上市初期规则')
                if listing >= bars[0]['time'][:10]:
                    dates = sorted(by_day)
                    active = [d for d in dates if d >= listing]
                    if len(active) < 6:
                        raise ValueError('上市不足6个交易日')
                    min_entry_date = active[5]
                result = replay(bars, .2 if board in ('创业板', '科创板') else .1,
                                min_entry_date=min_entry_date)
                fetched[code] = dict(board=board, amount20=sum(daily)/20, first=bars[0]['time'],
                                     last=bars[-1]['time'], listing_date=listing, min_entry_date=min_entry_date,
                                     result=result, bars=bars)
            except Exception as exc:
                errors[code] = str(exc)
            if i % 50 == 0 or i == len(futures):
                print(f'行情 {i}/{len(futures)}，成功 {len(fetched)}，失败 {len(errors)}', flush=True)
    for sector in sectors:
        if 'candidates' not in sector:
            continue
        codes = [c for c in sector['candidates'] if c in fetched]
        cap_order = sorted(codes, key=lambda c: positive(candidates[c].get('f20')), reverse=True)
        vol_order = sorted(codes, key=lambda c: fetched[c]['amount20'], reverse=True)
        cap_rank = {c: i+1 for i, c in enumerate(cap_order)}
        vol_rank = {c: i+1 for i, c in enumerate(vol_order)}
        codes.sort(key=lambda c: (cap_rank[c] + vol_rank[c], cap_rank[c]))
        chosen = codes[:10]
        anchor = ANCHORS.get(sector['name'])
        if anchor in codes and anchor not in chosen:
            chosen = chosen[:9] + [anchor]
        sector['selected'] = [dict(code=c, name=candidates[c]['f14'], market_cap=candidates[c]['f20'],
                                   amount20=fetched[c]['amount20'], cap_rank=cap_rank[c],
                                   amount_rank=vol_rank[c], representative_override=(c == anchor and codes.index(c) >= 10)) for c in chosen]
        sector['failed_candidates'] = {c: errors[c] for c in sector['candidates'] if c in errors}
    used = {x['code'] for s in sectors for x in s.get('selected', [])}
    output = dict(generated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), sectors=sectors,
                  stocks={code: fetched[code] for code in sorted(used)}, errors=errors,
                  methodology='东方财富当前板块成分；非ST沪深股票先取当前市值前30与当日成交额前30并集，再按近20个完整交易日成交额及市值的候选池内名次相加排序，必要时保留用户指定代表股。市值与盘中成交额为查询时快照；当前成分回测过去有事后选择偏差。')
    with open('markets/a/sector-results.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
