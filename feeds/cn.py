"""A 股日线提供者。返回 (bars, label)；失败抛异常。"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import List, Optional, Tuple
from urllib.parse import urlencode

from .http_util import read_text
from .schema import last_n, normalize_bars

BarList = List[dict]


def exchange_prefix(code: str) -> str:
    code = code.strip()
    if re.fullmatch(r'(?:60[0135]\d{3}|68[89]\d{3})', code):
        return 'sh'
    if re.fullmatch(r'(?:00[0-3]\d{3}|30[01]\d{3})', code):
        return 'sz'
    if re.fullmatch(r'(?:4\d{5}|8\d{5}|92\d{4})', code):
        return 'bj'
    raise ValueError(f'无法识别 A 股代码: {code}')


def tushare_ts_code(code: str) -> str:
    prefix = exchange_prefix(code)
    return f'{code}.{"SH" if prefix == "sh" else "SZ" if prefix == "sz" else "BJ"}'


def _window_end() -> date:
    return date.today()


def _start_for(days: int) -> date:
    # 交易日约 250/年，日历留余量
    return _window_end() - timedelta(days=max(days * 2, 120))


def from_tencent(code: str, days: int = 180) -> Tuple[BarList, str]:
    symbol = exchange_prefix(code) + code
    query = urlencode({'param': f'{symbol},day,,,{max(days + 40, 120)},qfq'})
    payload = json.loads(read_text(
        'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query,
        referer='https://finance.qq.com/',
    ))
    raw = payload.get('data', {}).get(symbol, {}).get('qfqday') or []
    bars = normalize_bars(
        [dict(date=b[0], open=b[1], close=b[2], high=b[3], low=b[4], volume=b[5]) for b in raw],
        min_bars=5,
    )
    return last_n(bars, days), 'tencent_qfq'


def from_sina(code: str, days: int = 180) -> Tuple[BarList, str]:
    symbol = exchange_prefix(code) + code
    query = urlencode(dict(symbol=symbol, scale=240, ma='no', datalen=max(days + 40, 120)))
    text = read_text(
        'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketData.getKLineData?' + query,
        referer='https://finance.sina.com.cn/',
    )
    begin, end = text.find('['), text.rfind(']')
    if begin < 0 or end < begin:
        raise ValueError('新浪日线未返回数组')
    raw = json.loads(text[begin:end + 1])
    bars = normalize_bars(
        [dict(date=x['day'][:10], open=x['open'], high=x['high'], low=x['low'],
              close=x['close'], volume=x['volume']) for x in raw],
        min_bars=5,
    )
    return last_n(bars, days), 'sina_unadj'


def from_akshare(code: str, days: int = 180) -> Tuple[BarList, str]:
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError('未安装 akshare：pip install akshare') from exc
    start = _start_for(days).strftime('%Y%m%d')
    end = _window_end().strftime('%Y%m%d')
    df = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')
    if df is None or getattr(df, 'empty', True):
        raise ValueError('AKShare 日线为空')
    cols = {str(c): c for c in df.columns}
    date_c = cols.get('日期') or cols.get('date')
    rows = []
    for _, row in df.iterrows():
        rows.append(dict(
            date=str(row[date_c])[:10],
            open=row[cols.get('开盘') or cols.get('open')],
            high=row[cols.get('最高') or cols.get('high')],
            low=row[cols.get('最低') or cols.get('low')],
            close=row[cols.get('收盘') or cols.get('close')],
            volume=row[cols.get('成交量') or cols.get('volume') or 0],
        ))
    return last_n(normalize_bars(rows, min_bars=5), days), 'akshare_qfq'


def from_baostock(code: str, days: int = 180) -> Tuple[BarList, str]:
    try:
        import baostock as bs  # type: ignore
    except ImportError as exc:
        raise RuntimeError('未安装 baostock：pip install baostock') from exc
    prefix = exchange_prefix(code)
    if prefix == 'bj':
        raise ValueError('BaoStock 不覆盖北交所')
    symbol = f'{prefix}.{code}'
    lg = bs.login()
    if lg.error_code != '0':
        raise RuntimeError(f'BaoStock 登录失败: {lg.error_msg}')
    try:
        rs = bs.query_history_k_data_plus(
            symbol,
            'date,open,high,low,close,volume',
            start_date=_start_for(days).isoformat(),
            end_date=_window_end().isoformat(),
            frequency='d',
            adjustflag='2',  # 前复权
        )
        if rs.error_code != '0':
            raise RuntimeError(rs.error_msg)
        rows = []
        while rs.error_code == '0' and rs.next():
            item = rs.get_row_data()
            if not item or not item[0]:
                continue
            rows.append(dict(date=item[0], open=item[1], high=item[2], low=item[3],
                             close=item[4], volume=item[5] or 0))
    finally:
        bs.logout()
    return last_n(normalize_bars(rows, min_bars=5), days), 'baostock_qfq'


def from_tushare(code: str, days: int = 180) -> Tuple[BarList, str]:
    token = os.environ.get('TUSHARE_TOKEN', '').strip()
    if not token:
        raise RuntimeError('未配置 TUSHARE_TOKEN')
    try:
        import tushare as ts  # type: ignore
    except ImportError as exc:
        raise RuntimeError('未安装 tushare：pip install tushare') from exc
    pro = ts.pro_api(token)
    start = _start_for(days).strftime('%Y%m%d')
    end = _window_end().strftime('%Y%m%d')
    # pro_bar 前复权；失败则 daily 未复权
    try:
        df = ts.pro_bar(ts_code=tushare_ts_code(code), adj='qfq', start_date=start, end_date=end)
        label = 'tushare_qfq'
    except Exception:
        df = pro.daily(ts_code=tushare_ts_code(code), start_date=start, end_date=end)
        label = 'tushare_unadj'
    if df is None or getattr(df, 'empty', True):
        raise ValueError('Tushare 日线为空')
    df = df.sort_values('trade_date')
    rows = [dict(date=f"{str(r.trade_date)[:4]}-{str(r.trade_date)[4:6]}-{str(r.trade_date)[6:8]}",
                 open=r.open, high=r.high, low=r.low, close=r.close, volume=getattr(r, 'vol', 0) or 0)
            for r in df.itertuples()]
    return last_n(normalize_bars(rows, min_bars=5), days), label


def from_dsa(code: str, days: int = 180, client=None) -> Tuple[BarList, str]:
    if client is None:
        raise RuntimeError('DSA client 未注入')
    result = client.daily(code, days=days)
    bars = normalize_bars(result['data'], min_bars=5)
    return last_n(bars, days), 'dsa'


PROVIDERS = {
    'dsa': from_dsa,
    'tencent': from_tencent,
    'akshare': from_akshare,
    'baostock': from_baostock,
    'tushare': from_tushare,
    'sina': from_sina,
}

DEFAULT_ORDER = ('dsa', 'tencent', 'akshare', 'baostock', 'tushare', 'sina')
