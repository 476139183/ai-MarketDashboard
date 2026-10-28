#!/usr/bin/env python3
"""保存东方财富当前人气榜和飙升榜；不声称这是历史榜单。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HOST = 'https://emappdata.eastmoney.com/stockrank/'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json',
           'Referer': 'https://guba.eastmoney.com/'}


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def fetch_rank(endpoint, include_market=False):
    body = {'appId': 'appId01', 'globalId': '786e4c21-70dc-435a-93bb-38', 'pageNo': 1, 'pageSize': 100}
    if include_market:
        body['marketType'] = ''
    req = Request(HOST + endpoint, json.dumps(body).encode('utf-8'), HEADERS, method='POST')
    with urlopen(req, timeout=20) as response:
        payload = json.load(response)
    if payload.get('code') != 0 or not isinstance(payload.get('data'), list) or not payload['data']:
        raise ValueError(f'{endpoint} 返回异常或空榜')
    return {'requested_page_size': 100, 'received_at': now(), 'source_timestamp': None,
            'raw': payload['data']}


def fetch_names(identifiers):
    result = {}
    for offset in range(0, len(identifiers), 50):
        part = identifiers[offset:offset + 50]
        ids = ','.join(('bj' if x[2:].startswith(('4', '8', '92')) else x[:2].lower()) + x[2:] for x in part)
        req = Request('https://qt.gtimg.cn/q=' + ids, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=20) as response:
            payload = response.read().decode('gb18030')
        for line in payload.splitlines():
            parts = line.split('"', 2)
            if len(parts) >= 2:
                fields = parts[1].split('~')
                if len(fields) >= 3 and fields[2].isdigit() and fields[1]:
                    result[fields[2]] = fields[1]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='markets/a/hot-universe.json')
    args = parser.parse_args()
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
    identifiers = [x['source_symbol'] for x in stocks.values()]
    names = fetch_names(identifiers)
    for code, row in stocks.items():
        row['name'] = names.get(code)
    missing_names = [x['code'] for x in stocks.values() if not x['name']]
    snapshot = {'generated_at': now(), 'source': '东方财富股吧当前人气榜及人气飙升榜',
                'source_timestamp': None,
                'time_note': '源榜单未提供榜单生成时间；received_at 为本次请求接收时间，不能作为历史日期的榜单。',
                'coverage_note': '两个接口各请求前100条；并集保留北交所证券，不代表全市场全部热门股票。简称由腾讯行情补充。',
                'stocks': sorted(stocks.values(), key=lambda x: (x['rank'] if isinstance(x['rank'], int) else 999999, x['code'])),
                'boards': {'popularity': popularity, 'rising': rising}, 'missing_names': missing_names}
    Path(args.output).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'人气榜 {len(popularity["raw"])}，飙升榜 {len(rising["raw"])}，去重 {len(stocks)}，简称缺失 {len(missing_names)}；{args.output}')


if __name__ == '__main__':
    main()
