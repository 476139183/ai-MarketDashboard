"""Sina public US regular-session OHLCV. Provider timestamps mark bar ends."""
from datetime import datetime
import json
import math
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NY=ZoneInfo('America/New_York')

def parse_bars(text,minutes):
    # Parse a fixed JSONP wrapper as JSON; never evaluate provider JavaScript.
    match=re.search(r'\bvar data\s*=\s*\((.*)\);?\s*$',text,re.S)
    if not match:raise ValueError('新浪分钟行情格式无效')
    payload=json.loads(match.group(1))
    if not isinstance(payload,list) or not payload:raise ValueError('新浪分钟行情为空')
    result={}
    for raw in payload:
        dt=datetime.strptime(raw['d'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=NY)
        if dt.weekday()>4 or not 570<dt.hour*60+dt.minute<=960:continue
        values=[float(raw[key]) for key in ('o','h','l','c','v')]
        if not all(math.isfinite(v) for v in values) or min(values[:4])<=0 or values[4]<0:
            raise ValueError('新浪分钟 OHLCV 数值无效')
        opening,high,low,close,volume=values
        if not low<=min(opening,close)<=max(opening,close)<=high:raise ValueError('新浪分钟高低价无效')
        stamp=int(dt.timestamp())-minutes*60
        row=dict(t=stamp,o=opening,h=high,l=low,c=close,v=volume)
        if stamp in result and result[stamp]!=row:raise ValueError('新浪分钟重复且冲突')
        result[stamp]=row
    if not result:raise ValueError('无常规盘分钟行情')
    return [result[key] for key in sorted(result)]


def fetch_rows(symbol,minutes):
    if minutes not in (1,5,15,30,60) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}',symbol):
        raise ValueError('新浪美股仅支持有效代码及 1/5/15/30/60 分钟')
    url='https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/var%20data=/US_MinKService.getMinK?'+urlencode(dict(symbol=symbol.lower(),type=minutes,datalen=1023))
    request=Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
    with urlopen(request,timeout=8) as response:text=response.read(2_000_000).decode('utf-8')
    return parse_bars(text,minutes)
