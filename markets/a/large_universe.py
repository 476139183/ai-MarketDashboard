#!/usr/bin/env python3
"""从热榜中排除当前风险警示、退市整理和低市值低流动性股票。"""
from datetime import datetime
import gzip
import json
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MIN_FLOAT_CAP_YI = 100
MIN_TURNOVER_YUAN = 100_000_000


def exclusion_reason(name, float_cap_yi, turnover):
    if 'ST' in name.upper() or '退' in name:
        return '当前名称显示风险警示或退市'
    if float_cap_yi is None or turnover is None:
        return '缺少市值或成交额'
    if float_cap_yi < MIN_FLOAT_CAP_YI:
        return '当前流通市值低于100亿元'
    if turnover < MIN_TURNOVER_YUAN:
        return '近20日估算日均成交额低于1亿元'
    return None


def quotes(stocks):
    data = {}
    for start in range(0, len(stocks), 40):
        chunk = stocks[start:start+40]
        symbols = [((x['exchange'] if x['exchange'] != 'BJ' else 'BJ').lower() + x['code']) for x in chunk]
        with urlopen(Request('https://qt.gtimg.cn/q=' + ','.join(symbols),
                             headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
            text = response.read().decode('gb18030')
        for line in text.splitlines():
            parts = line.split('"')
            if len(parts) < 2:
                continue
            fields = parts[1].split('~')
            try:
                code = fields[2]
                data[code] = {'name': fields[1], 'price': float(fields[3]), 'previous_close': float(fields[4]),
                              'quote_time': fields[30], 'float_cap_yi': float(fields[44]),
                              'total_cap_yi': float(fields[45])}
            except (ValueError, IndexError):
                continue
    return data


def main():
    with open('markets/a/hot-universe.json', encoding='utf-8') as f:
        hot = json.load(f)
    with gzip.open('markets/a/hot-history.json.gz', 'rt', encoding='utf-8') as f:
        histories = json.load(f)
    quotes_by_code = quotes(hot['stocks'])
    included, excluded = [], []
    for stock in hot['stocks']:
        code = stock['code']
        quote = quotes_by_code.get(code)
        bars = histories.get(code, [])
        turnover = sum(x['close'] * x['volume'] for x in bars[-20:]) / 20 if len(bars) >= 20 else None
        name = quote['name'] if quote else stock.get('name') or ''
        cap = quote['float_cap_yi'] if quote else None
        reason = exclusion_reason(name, cap, turnover)
        row = {'code': code, 'name': name, 'board': stock.get('exchange'),
               'quote': quote, 'avg20_turnover_proxy_yuan': turnover,
               'history_as_of': bars[-1]['date'] if bars else None, 'reason': reason}
        (excluded if reason else included).append(row)
    output = {'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'method': '剔除当前名称含ST或退、流通市值低于100亿元、近20日估算日均成交额低于1亿元或数据缺失；成交额近似为每日收盘价乘成交量',
              'limitations': ['市值与名称为本次时点，不是历史时点', '过去的ST状态、退市历史及板块成分未还原',
                              '成交额为价格乘成交量的估算，非逐笔成交额', '热门榜为当前榜单，历史回放有前视偏差'],
              'quote_source': '腾讯公开行情，流通市值单位为亿元', 'included': included, 'excluded': excluded}
    with open('markets/a/large-universe.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(f'候选 {len(hot["stocks"])}，保留 {len(included)}，剔除 {len(excluded)}，行情缺失 {sum(x["quote"] is None for x in excluded)}')


if __name__ == '__main__':
    main()
