#!/usr/bin/env python3
"""A 股盘后大盘复盘（结构化行情，不依赖大模型）。"""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import math
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


OUT = Path(__file__).resolve().parent / 'market-review-latest.json'
BJ = ZoneInfo('Asia/Shanghai')
UA = {'User-Agent': 'Mozilla/5.0'}


def _now():
    return datetime.now(BJ)


def _get_json(url):
    with urlopen(Request(url, headers=UA), timeout=10) as response:
        return json.load(response)


def _indices():
    ids = 'sh000001,sz399001,sz399006,sh000300,sh000688,sh000852,bj899050'
    text = urlopen(Request('https://qt.gtimg.cn/q=' + ids, headers=UA), timeout=10).read().decode('gb18030')
    rows = []
    for line in text.splitlines():
        if '"' not in line:
            continue
        f = line.split('"')[1].split('~')
        if len(f) < 6 or not f[3]:
            continue
        price, prev = float(f[3]), float(f[4] or 0)
        chg = (price / prev - 1) * 100 if prev else None
        rows.append({
            'code': f[2], 'name': f[1], 'price': price, 'prev_close': prev,
            'open': float(f[5] or 0), 'change_pct': round(chg, 2) if chg is not None else None,
            'asof': f[30] if len(f) > 30 else None,
            'amount': _number(f[37])*10000 if len(f)>37 and _number(f[37]) is not None else None,
            'source': '腾讯指数行情',
        })
    return rows


def _breadth():
    """涨跌家数（东方财富）。失败则返回空。"""
    try:
        url = ('https://push2.eastmoney.com/api/qt/ulist.np/get?'
               + urlencode({
                   'fltt': 2, 'fields': 'f104,f105,f106',
                   'secids': '1.000001',
               }))
        data = _get_json(url)
        diff = (data.get('data') or {}).get('diff') or []
        if not diff:
            return None
        row = diff[0]
        up, down, flat = row.get('f104'), row.get('f105'), row.get('f106')
        if up is None:
            return None
        return {'up': int(up), 'down': int(down), 'flat': int(flat),'scope':'上证指数覆盖股票（非全A）','source':'东方财富上证指数样本'}
    except Exception:  # noqa: BLE001
        return None


def _sectors(limit=5):
    """行业板块涨跌榜。"""
    try:
        base = 'https://push2.eastmoney.com/api/qt/clist/get?'
        common = {
            'pn': 1, 'pz': limit, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3', 'fs': 'm:90+t:2+f:!50',
            'fields': 'f12,f14,f2,f3',
        }
        top = _get_json(base + urlencode({**common, 'po': 1}))
        bottom = _get_json(base + urlencode({**common, 'po': 0}))
        def pack(payload):
            out = []
            for row in ((payload.get('data') or {}).get('diff') or []):
                out.append({
                    'code': row.get('f12'), 'name': row.get('f14'),
                    'price': row.get('f2'), 'change_pct': row.get('f3'),
                })
            return out
        return pack(top), pack(bottom)
    except Exception:  # noqa: BLE001
        return [], []


def _limit_stats(day=None):
    """涨停/跌停粗略统计。"""
    if day:
        def pool_count(endpoint):
            try:
                target = datetime.strptime(day, '%Y-%m-%d').strftime('%Y%m%d')
                payload = _get_json('https://push2ex.eastmoney.com/' + endpoint + '?' + urlencode({
                    'ut': '7eea3edcaed734bea9cbfc24409ed989', 'dpt': 'wz.ztzt',
                    'Pageindex': 0, 'pagesize': 1, 'sort': 'fbt:asc', 'date': target,
                })).get('data') or {}
                value = _number(payload.get('tc'))
                return int(value) if str(payload.get('qdate')) == target and value is not None and value >= 0 and value.is_integer() else None
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            up, down = executor.map(pool_count, ['getTopicZTPool', 'getTopicDTPool'])
        return {'limit_up': up, 'limit_down': down, 'as_of': day, 'source': '东方财富涨跌停池', 'scope': '东方财富涨跌停池覆盖，非自行按涨幅阈值估算'}
    try:
        url = ('https://push2ex.eastmoney.com/getTopicZDFenBu?'
               + urlencode({'ut': '7eea3edcaed734bea9cbfc24409ed989'}))
        data = _get_json(url)
        payload = data.get('data') or {}
        def count(*keys):
            value = next((payload[k] for k in keys if payload.get(k) is not None), None)
            value = _number(value)
            return int(value) if value is not None and value >= 0 and value.is_integer() else None
        up, down = count('ztum', 'zt'), count('dtum', 'dt')
        return {'limit_up': up, 'limit_down': down, 'raw_ok': up is not None or down is not None}
    except Exception:  # noqa: BLE001
        return None


def _number(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):return None


def summarize_stocks(rows,total):
    unique={str(r.get('f12')):r for r in rows if isinstance(r,dict) and len(str(r.get('f12','')))==6}
    complete=isinstance(total,int) and total>0 and len(unique)==total and len(rows)==total
    result={'complete':complete,'expected':total,'received':len(unique),'breadth':None,'amount':None,'distribution':[],'missing_quotes':0,'source':'东方财富全A快照'}
    if not complete:return result
    up=down=flat=missing=0;amount=0;amount_ok=True;bins=[0]*7
    for row in unique.values():
        price=_number(row.get('f2'));change=_number(row.get('f3'))
        if price is None or price<=0 or change is None:missing+=1;continue
        up+=change>0;down+=change<0;flat+=change==0
        index=0 if change<=-5 else 1 if change<=-2 else 2 if change<0 else 3 if change==0 else 4 if change<2 else 5 if change<5 else 6
        bins[index]+=1
        value=_number(row.get('f6'))
        if value is None or value<0:amount_ok=False
        else:amount+=value
    if up + down + flat == 0:
        result['missing_quotes'] = missing
        return result
    result.update(missing_quotes=missing,breadth={'up':up,'down':down,'flat':flat,'scope':'全A股有效报价样本','source':'东方财富全A快照'},amount=amount if amount_ok else None,
                  distribution=[{'label':label,'count':count,'direction':'down' if i<3 else 'flat' if i==3 else 'up'} for i,(label,count) in enumerate(zip(['≤-5%','-5%至-2%','-2%至0','平盘','0至2%','2%至5%','≥5%'],bins))])
    return result


def _stock_snapshot():
    common={'np':1,'fltt':2,'invt':2,'fid':'f12','po':1,'fs':'m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048','fields':'f12,f14,f2,f3,f6'}
    def page(n,size):
        return (_get_json('https://push2.eastmoney.com/api/qt/clist/get?'+urlencode({**common,'pn':n,'pz':size})).get('data') or {})
    try:
        first=page(1,500);total=first.get('total');rows=first.get('diff') or []
        if not isinstance(total,int) or not 1<=total<=10000 or not rows:return {'complete':False,'breadth':None,'amount':None,'error':'全A快照不可用'}
        size=len(rows);pages=(total+size-1)//size
        if pages>100:raise ValueError('Too many pages')
        with ThreadPoolExecutor(max_workers=4) as pool:
            for payload in pool.map(lambda n:page(n,size),range(2,pages+1)):
                if payload.get('total')!=total:raise ValueError('Snapshot universe changed')
                rows.extend(payload.get('diff') or [])
        return summarize_stocks(rows,total)
    except Exception:return {'complete':False,'breadth':None,'amount':None,'error':'全A快照采集失败或覆盖不完整'}


def enrich_review(data,now=None):
    now=now or _now();out=dict(data);indices=out.get('indices') or [];breadth=dict(out['breadth']) if out.get('breadth') else None
    if breadth and not breadth.get('scope'):breadth.update(scope='上证指数覆盖股票（非全A）',source='东方财富上证指数样本')
    out['breadth']=breadth
    try:generated=datetime.fromisoformat(out['generated_at']);generated=generated.replace(tzinfo=BJ) if generated.tzinfo is None else generated
    except (ValueError,TypeError,KeyError):generated=None
    source_day=out.get('as_of');expected=now.date()
    if now.weekday()<5 and (now.hour,now.minute)<(15,5):expected-=timedelta(days=1)
    while expected.weekday()>4:expected-=timedelta(days=1)
    weekend=now.weekday()>4;matched=source_day==expected.isoformat()
    old=not generated or now-generated>timedelta(hours=18)
    out['collection_old']=old;out['stale']=not matched
    note='周末休市，行情日期与最近周五一致' if weekend and matched else '行情日期与工作日收盘窗口一致' if matched else '行情日期需核验；节假日尚未接交易所日历'
    out['session']={'phase':'closed' if weekend or (now.hour,now.minute)>=(15,5) else 'intraday','label':'周末休市' if weekend else '收盘观察' if (now.hour,now.minute)>=(15,5) else '盘中观察','date_note':note}
    out['report_kind']='intraday' if source_day==now.date().isoformat() and not weekend and (now.hour,now.minute)<(15,5) else 'after_close'
    up=_number(breadth.get('up')) if breadth else None;down=_number(breadth.get('down')) if breadth else None;flat=_number(breadth.get('flat')) if breadth else None
    total=up+down+flat if all(v is not None and v >= 0 for v in (up,down,flat)) else 0;ratio=round(up/total*100,2) if total>0 else None
    hs=next((r for r in indices if r.get('code')=='000300'),None);change=_number(hs.get('change_pct')) if hs else None
    tone='待确认';tone_key='unknown'
    if change is not None:
        tone='偏强' if change>=1 else '偏弱' if change<=-1 else '震荡';tone_key='strong' if change>=1 else 'weak' if change<=-1 else 'balanced'
        if ratio is not None and (change<0 and ratio>=55 or change>0 and ratio<=45):tone='指数与个股分化';tone_key='balanced'
    snapshot=out.get('stock_snapshot') or {};amount=snapshot.get('amount') if snapshot.get('complete') else None
    turnover_scope='全A有效报价样本'
    if amount is None:
        exchange_amounts=[next((r for r in indices if r.get('code')==code),{}) for code in ('000001','399001')]
        values=[_number(r.get('amount')) for r in exchange_amounts]
        dates=[r.get('asof') for r in exchange_amounts]
        if all(v is not None and v>=0 for v in values) and (not any(dates) or all(str(d or '')[:8]==str(dates[0])[:8] for d in dates)):
            amount=sum(values);turnover_scope='沪深成交额（不含北交所）'
    limits=out.get('limit_stats') or {};insights=[];missing=[]
    if hs and change is not None:insights.append({'title':'指数结构','text':f"沪深300 {change:+.2f}%，指数表现与个股广度分别观察。"})
    else:missing.append('沪深300指数')
    if ratio is not None:insights.append({'title':'市场广度','text':f"上涨占比 {ratio:.1f}%，涨 {int(up)} / 跌 {int(down)} / 平 {int(flat)}；口径：{breadth['scope']}。"})
    else:missing.append('涨跌家数')
    if amount is None:missing.append('全A成交额')
    else:insights.append({'title':'成交量能','text':f"{turnover_scope} {amount/1e8:,.2f} 亿元；不是资金净流入。"})
    if not snapshot.get('distribution'):missing.append('全A涨跌分布')
    if limits.get('limit_up') is None or limits.get('limit_down') is None:missing.append('涨跌停统计')
    top=out.get('top_sectors') or [];bottom=out.get('bottom_sectors') or []
    if top:insights.append({'title':'领涨方向','text':' / '.join(str(r.get('name','')) for r in top[:3])+'；涨幅排名不代表已验证的主线。'})
    else:missing.append('板块榜单')
    valid_changes=[_number(r.get('change_pct')) for r in indices];valid_changes=[v for v in valid_changes if v is not None]
    out.update(market_tone=tone,tone_key=tone_key,metrics={'advance_ratio':ratio,'breadth_total':int(total) if total else None,'turnover':amount,'turnover_scope':turnover_scope,'index_count':len(indices),'positive_indices':sum(v>0 for v in valid_changes),'limit_up':limits.get('limit_up'),'limit_down':limits.get('limit_down')},
               insights=insights,quality={'missing':missing,'coverage_label':'全A有效报价' if snapshot.get('complete') and snapshot.get('breadth') else '部分市场数据','snapshot_complete':snapshot.get('complete',False)},
               next_day_hint='先核对指数与广度能否同步，再结合量能及事件判断观察方向。' if tone_key=='balanced' else '指数承压，优先观察广度与量能是否修复，避免仅凭板块涨幅追涨。' if tone_key=='weak' else '观察上涨能否获得广度与量能支持，具体标的仍需独立核对。' if tone_key=='strong' else '关键数据不足，补齐后再形成市场判断。')
    out['summary_bullets']=[i['text'] for i in insights]
    return out


def build_review():
    now=_now();jobs={'indices':(_indices,[]),'breadth':(_breadth,None),'sectors':(_sectors,([],[])),'snapshot':(_stock_snapshot,{'complete':False,'breadth':None,'amount':None})}
    results={};errors=[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending={name:pool.submit(call) for name,(call,_) in jobs.items()}
        for name,future in pending.items():
            try:results[name]=future.result()
            except Exception:results[name]=jobs[name][1];errors.append(name+'来源暂不可用')
    indices=results['indices'];dates=[]
    for row in indices:
        value=str(row.get('asof') or '')
        if len(value)>=8:
            try:dates.append(datetime.strptime(value[:8],'%Y%m%d').date().isoformat())
            except ValueError:pass
    asof=max(set(dates),key=dates.count) if dates else None
    results['limits']=_limit_stats(asof) if asof else None
    top,bottom=results['sectors'];snapshot=results['snapshot'];breadth=snapshot.get('breadth') if snapshot.get('complete') else results['breadth']
    if not indices and not breadth and not top and not bottom:
        raise RuntimeError('行情源均未取得有效数据，已保留上次复盘快照')
    payload={'title':'A股市场复盘','as_of':asof,'generated_at':now.isoformat(timespec='seconds'),'indices':indices,'breadth':breadth,'stock_snapshot':snapshot,'limit_stats':results['limits'],'top_sectors':top,'bottom_sectors':bottom,
             'limitations':['指数来自腾讯；个股、板块和涨跌停来自东方财富公开接口','全A快照仅完整覆盖时汇总；失败时保留明确样本口径；分页采集不是同一瞬间成交快照','资讯不自动解释当日涨跌，不构成交易依据','法定节假日尚未接入交易所日历；日期以实际行情为准',*errors]}
    if len(set(dates))>1:payload['limitations'].append('部分指数行情日期不一致，需逐项核对')
    payload=enrich_review(payload,now);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return payload


def load_cached(*,max_age_hours=12):
    if not OUT.exists():return None
    try:return enrich_review(json.loads(OUT.read_text(encoding='utf-8')))
    except (OSError,ValueError,TypeError):return None


if __name__=='__main__':
    review=build_review();print(review['as_of'],review['market_tone'])
