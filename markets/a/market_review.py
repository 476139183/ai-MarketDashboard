#!/usr/bin/env python3
"""A 股盘后大盘复盘（结构化行情，不依赖大模型）。"""
from datetime import datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from hot_backtest import as_of_date

OUT = Path(__file__).resolve().parent / 'market-review-latest.json'
BJ = ZoneInfo('Asia/Shanghai')
UA = {'User-Agent': 'Mozilla/5.0'}


def _now():
    return datetime.now(BJ)


def _get_json(url):
    with urlopen(Request(url, headers=UA), timeout=20) as response:
        return json.load(response)


def _indices():
    ids = 'sh000001,sz399001,sh000300,sh000688'
    text = urlopen(Request('https://qt.gtimg.cn/q=' + ids, headers=UA), timeout=20).read().decode('gb18030')
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
        return {'up': int(up), 'down': int(down), 'flat': int(flat or 0)}
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


def _limit_stats():
    """涨停/跌停粗略统计。"""
    try:
        url = ('https://push2ex.eastmoney.com/getTopicZDFenBu?'
               + urlencode({'ut': '7eea3edcaed734bea9cbfc24409ed989'}))
        data = _get_json(url)
        fenbu = (data.get('data') or {}).get('fenbu') or {}
        # fenbu keys like "1","2"... mapped ranges; also zteng/dteng in some versions
        zt = data.get('data', {}).get('ztum') or data.get('data', {}).get('zt') 
        dt = data.get('data', {}).get('dtum') or data.get('data', {}).get('dt')
        return {'limit_up': zt, 'limit_down': dt, 'raw_ok': bool(fenbu or zt or dt)}
    except Exception:  # noqa: BLE001
        return None


def build_review():
    now = _now()
    asof = as_of_date(now)
    indices = _indices()
    breadth = _breadth()
    top, bottom = _sectors()
    limits = _limit_stats()
    hs = next((x for x in indices if x['code'] == '000300'), None)
    tone = '平淡'
    if hs and hs.get('change_pct') is not None:
        if hs['change_pct'] >= 1:
            tone = '偏强'
        elif hs['change_pct'] <= -1:
            tone = '偏弱'
        elif hs['change_pct'] >= 0.3:
            tone = '小幅偏强'
        elif hs['change_pct'] <= -0.3:
            tone = '小幅偏弱'
    bullets = []
    if hs:
        bullets.append(f"沪深300 {hs['change_pct']:+.2f}%（{hs['price']}）")
    if breadth:
        bullets.append(f"涨 {breadth['up']} / 跌 {breadth['down']} / 平 {breadth['flat']}")
    if top:
        bullets.append('领涨板块：' + '、'.join(f"{x['name']}({x['change_pct']}%)" for x in top[:3]))
    if bottom:
        bullets.append('领跌板块：' + '、'.join(f"{x['name']}({x['change_pct']}%)" for x in bottom[:3]))
    payload = {
        'title': 'A股盘后复盘',
        'as_of': asof,
        'generated_at': now.isoformat(timespec='seconds'),
        'market_tone': tone,
        'summary_bullets': bullets,
        'indices': indices,
        'breadth': breadth,
        'limit_stats': limits,
        'top_sectors': top,
        'bottom_sectors': bottom,
        'next_day_hint': (
            '大盘偏弱时，明日盯盘以观察池防守为主，优先等待回踩点位，不追高。'
            if tone.endswith('弱') else
            '大盘尚可时，明日可关注策略候选的回踩买入计划，仍需核对清单。'
            if '强' in tone else
            '震荡市以计划内回踩为主，清单未齐的不升格为优先。'
        ),
        'limitations': [
            '结构化行情复盘，不含AI叙事与北向资金',
            '板块与涨跌统计依赖东财公开接口，失败时字段为空',
            '不构成投资建议',
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def load_cached(*, max_age_hours=12):
    if not OUT.exists():
        return None
    data = json.loads(OUT.read_text(encoding='utf-8'))
    generated = datetime.fromisoformat(data['generated_at'])
    data = dict(data)
    data['stale'] = _now() - generated > timedelta(hours=max_age_hours)
    return data


if __name__ == '__main__':
    review = build_review()
    print(review['as_of'], review['market_tone'])
    for line in review['summary_bullets']:
        print('-', line)
