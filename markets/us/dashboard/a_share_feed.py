"""沪深 A 股真实日线/15m/报价适配；不调用美股行情源。"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from market_states import BJ, build_market_context, entry_signal, market_session

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from feeds.daily import fetch_daily as multi_fetch_daily  # noqa: E402


def exchange_symbol(code):
    if re.fullmatch(r'(?:60[0135]\d{3}|68[89]\d{3})', code):
        return 'sh'+code
    if re.fullmatch(r'(?:00[0-3]\d{3}|30[01]\d{3})', code):
        return 'sz'+code
    raise ValueError('盘中信号当前支持沪深普通 A 股；ETF、北交所与特殊证券需单独适配')


def _read(url, encoding='utf-8'):
    request = Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
    with urlopen(request,timeout=15) as response:
        return response.read().decode(encoding)


def fetch_quote(code):
    text = _read('https://qt.gtimg.cn/q='+exchange_symbol(code),'gb18030')
    fields = text.split('"')[1].split('~')
    if fields[2]!=code:
        raise ValueError('A股报价标的不一致')
    optional=lambda index:float(fields[index]) if len(fields)>index and fields[index] else None
    return dict(name=fields[1],price=float(fields[3]),previous_close=float(fields[4]),
                volume=optional(6),bid=optional(9),ask=optional(19),
                day_high=float(fields[33]),day_low=float(fields[34]),
                time=datetime.strptime(fields[30],'%Y%m%d%H%M%S').replace(tzinfo=BJ),
                limits=(float(fields[47]),float(fields[48])))


def parse_minutes(text):
    # 新浪该接口的 day 表示结束时间，统一转换为 K 线开始时间。
    begin,end = text.find('['),text.rfind(']')
    if begin<0 or end<begin:
        raise ValueError('A股分钟接口未返回数组')
    rows=[]
    for raw in json.loads(text[begin:end+1]):
        finish=datetime.fromisoformat(raw['day']).replace(tzinfo=BJ)
        start=finish-timedelta(minutes=15)
        rows.append(dict(t=int(start.timestamp()),dt_ny=start,dt_bj=start,
                         o=float(raw['open']),h=float(raw['high']),l=float(raw['low']),
                         c=float(raw['close']),v=float(raw['volume']),feed='sina_cn_15m'))
    return rows


def fetch_minutes(code):
    query=urlencode(dict(symbol=exchange_symbol(code),scale=15,ma='no',datalen=1023))
    return parse_minutes(_read('https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_/CN_MarketDataService.getKLineData?'+query))


def fetch_daily(code):
    """多源级联日线：DSA → 腾讯 → AKShare → BaoStock → Tushare → 新浪。"""
    result = multi_fetch_daily(code, market='cn', days=180, min_bars=5)
    rows = []
    for b in result['bars']:
        dt = datetime.fromisoformat(b['date']).replace(hour=9, minute=30, tzinfo=BJ)
        rows.append(dict(t=int(dt.timestamp()), o=b['open'], h=b['high'], l=b['low'],
                         c=b['close'], v=b['volume'] or 0))
    label = result.get('source_label') or result['source']
    if result.get('warnings'):
        label = label + '；' + '；'.join(result['warnings'])
    return rows, label


def analyze_symbol(code, *, accept_overnight=False):
    exchange_symbol(code)
    now=datetime.now(BJ)
    errors=[]
    try:
        daily,source=fetch_daily(code)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        daily,source=[], 'unavailable'
        errors.append('日线不可用：'+str(exc))
    try:
        bars=fetch_minutes(code)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        bars=[]
        errors.append('分钟数据不可用：'+str(exc))
    quote=None
    try:
        quote=fetch_quote(code)
    except (OSError,ValueError,KeyError,IndexError) as exc:
        errors.append('报价不可用：'+str(exc))
    context=build_market_context(daily,bars,now,market='cn')
    session=market_session(now,'cn')
    day=context['daily_context']
    px=quote['price'] if quote else None
    fresh=bool(quote and -30<=(now-quote['time']).total_seconds()<=120)
    execution_issues=[]
    if quote and ('ST' in quote['name'].upper() or '退' in quote['name']):
        execution_issues.append('风险警示/退市证券暂不发普通进场信号')
    prior=[b for b in daily if datetime.fromtimestamp(b['t'],BJ).date()<now.date()]
    if quote and prior and abs(prior[-1]['c']/quote['previous_close']-1)>.005:
        execution_issues.append('日线前收与当前报价前收口径不一致，需核验复权/除权')
    result=entry_signal(context,px=px or 0,quote_fresh=fresh,session_open=session['is_rth'],now=now,
                        accept_overnight=accept_overnight,limits=quote['limits'] if quote else None,
                        execution_issues=execution_issues)
    return {**context,**result,'symbol':code,'market':'cn','name':quote['name'] if quote else code,
            'price':px,'prev_close':quote['previous_close'] if quote else None,
            'change_pct':round((px/quote['previous_close']-1)*100,2) if quote and quote['previous_close'] else None,
            'daily_source':source,'daily_as_of':day['as_of'],'daily_error':'；'.join(errors) or None,
            'atr14':day['atr'],'ma20':day['ma20'],'interval':'15m','session':session,
            'quote_fresh':fresh,'quote_time_bj':quote['time'].isoformat() if quote else None,
            'checked_at_bj':now.isoformat(),'price_tag':'腾讯报价' if fresh else '历史/过期报价',
            'source':source+' 日线 · 新浪15m · 腾讯报价','data_mode':'live' if fresh else 'cache',
            'struct_scope':'A股连续竞价15m','watch_only':not result['actionable'],
            'day_high':quote['day_high'] if quote else None,'day_low':quote['day_low'] if quote else None,
            'bar_time_bj':datetime.fromtimestamp(context['intraday_state']['last_bar_end'],BJ).isoformat() if context['intraday_state']['last_bar_end'] else None}
