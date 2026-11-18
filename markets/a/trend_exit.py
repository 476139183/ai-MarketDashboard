"""趋势与资金结合的收盘退出候选；不把盈利或净流出单独当卖点。"""
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def assess_exit(bars, entry_price, flows=None):
    flows = flows or {}
    if len(bars) < 11:
        return {'action': '待核验', 'reason': '日线不足11日', 'flow_status': '未核验'}
    closes = [float(x['close']) for x in bars]
    close, previous = closes[-1], closes[-2]
    ma5, ma10 = sum(closes[-5:])/5, sum(closes[-10:])/10
    previous_ma5 = sum(closes[-6:-1])/5
    values = [flows.get(x['date']) for x in bars[-2:]]
    known = all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
    outflow = known and all(v < 0 for v in values)
    flow_status = ('连续两日供应商主力净流出' if outflow else '未出现连续两日主力净流出') if known else '缺少连续两日资金流'
    reason = None
    if close <= entry_price * .95:
        reason = '触发原有5%收盘风险阈值，退出价格仍需次日核验'
    elif close < ma10 and ma5 < previous_ma5 and close < previous:
        reason = '跌破MA10且MA5转弱、价格继续走低'
    elif outflow and close < ma5 and close < previous:
        reason = '连续两日主力净流出且跌破MA5、价格走低'
    return {'action': '退出' if reason else '持有',
            'reason': reason or '尚未确认趋势与资金共同转坏，保持原持仓',
            'flow_status': flow_status, 'ma5': round(ma5, 3), 'ma10': round(ma10, 3),
            'timing': '收盘确认后最早下一交易日尝试退出；受T+1及成交条件约束'}


def fetch_flows(stock, end, count=30):
    # 东方财富主力净额是供应商按大单等分类估算，不能等同真实资金账户撤离。
    market = '1' if stock['exchange'] == 'SH' else '0'
    query = urlencode({'lmt': count, 'klt': 101, 'secid': market + '.' + stock['code'],
                       'fields1': 'f1,f2,f3,f7',
                       'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
                       'ut': 'b2884a393a59ad64002292a3e90d46a5', '_': int(time.time()*1000)})
    with urlopen(Request('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?' + query,
                         headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36',
                                  'Referer': 'https://quote.eastmoney.com/'}), timeout=8) as r:
        rows = json.load(r)['data']['klines']
    result = {}
    for row in rows:
        fields = row.split(',')
        if fields[0] <= end:
            result[fields[0]] = float(fields[1])
    return result
