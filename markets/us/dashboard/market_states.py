"""日线量价定方向，完整 15m 识别盘中状态；A 股/美股分别处理时段与动作。"""
from datetime import datetime, timedelta
import math
from statistics import mean
from zoneinfo import ZoneInfo

VERSION = 'daily-volume-v2.1'
BJ = ZoneInfo('Asia/Shanghai')
ZONES = {'us': ZoneInfo('America/New_York'), 'cn': BJ}
SESSIONS = {'us': [(570, 960)], 'cn': [(570, 690), (780, 900)]}
MIN_RR = 1.5
# 趋势日早段可开仓形态（与延续加速/反抽确认并列）
EARLY_SETUP_CODES = frozenset({'open_drive', 'rebound_fail'})
ENTRY_SETUP_CODES = frozenset({'acceleration', 'pullback_confirmed', 'reversal'}) | EARLY_SETUP_CODES


def market_session(now, market='us'):
    local = now.astimezone(ZONES[market])
    minute = local.hour*60 + local.minute
    opened = local.weekday() < 5 and any(a <= minute < (897 if market == 'cn' and b == 900 else b)
                                                   for a,b in SESSIONS[market])
    if opened:
        phase = '连续交易' if market == 'cn' else '常规盘中'
    elif local.weekday() >= 5:
        phase = '周末休市'
    elif market == 'cn':
        phase = '午休' if 690 <= minute < 780 else '收盘集合竞价' if 897 <= minute < 900 else '非连续交易时段'
    else:
        phase = '盘前' if 240 <= minute < 570 else '盘后' if 960 <= minute < 1200 else '休市'
    return dict(is_rth=opened, phase=phase, beijing=now.astimezone(BJ).isoformat(),
                local=local.isoformat(), market=market)


def _time(bar, market):
    return datetime.fromtimestamp(int(bar['t']), ZONES[market])


def _price_ok(bar):
    try:
        o,h,l,c = [float(bar[k]) for k in ('o','h','l','c')]
        return all(math.isfinite(v) and v > 0 for v in (o,h,l,c)) and l <= min(o,c) <= max(o,c) <= h
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _volume_ok(bar):
    try:
        return math.isfinite(float(bar.get('v'))) and float(bar['v']) > 0
    except (TypeError, ValueError):
        return False


def _atr(rows):
    ranges = [max(b['h']-b['l'], abs(b['h']-a['c']), abs(b['l']-a['c'])) for a,b in zip(rows, rows[1:])]
    return mean(ranges[-14:]) if ranges else None


def _ema(values, period):
    current = values[0]
    for value in values[1:]:
        current += (value-current)*2/(period+1)
    return current


def _early_vol_ok(*, rvol, strong, cum=None, fallback_v=0.0, today=None):
    """早段：单根/累计量比达标，或开盘砸盘已主导当日量；无历史样本时回退到有量。"""
    if strong or (rvol is not None and rvol >= 1.0) or (cum is not None and cum >= .85):
        return True
    if today and len(today) >= 2 and today[0]['c'] < today[0]['o']:
        total = sum(float(b.get('v') or 0) for b in today)
        if total > 0 and float(today[0].get('v') or 0) / total >= .4:
            return True
    return rvol is None and cum is None and float(fallback_v or 0) > 0


def _open_drive_down(today, *, vwap, daily, rvol, strong, cum=None):
    """日线偏空 + 开盘大阴且高点仍在开盘附近 → 趋势日开盘弱势（可空）。"""
    if daily.get('bias') != 'down' or len(today) < 2:
        return False
    first = today[0]
    span = float(first['h'] - first['l'])
    if span <= 0 or first['c'] >= first['o']:
        return False
    weak = (first['h'] - first['c']) / span >= .5
    atr = daily.get('atr')
    wide = atr is None or span >= .35 * float(atr)
    hod = max(b['h'] for b in today)
    held_high = hod <= first['h'] * 1.0005
    # 允许略贴 VWAP 上方（反抽初期），但仍须明显低于开盘价
    below = today[-1]['c'] < first['o'] and today[-1]['c'] <= vwap * 1.002
    vol_ok = _early_vol_ok(rvol=rvol, strong=strong, cum=cum, fallback_v=first.get('v'), today=today)
    return weak and wide and held_high and below and vol_ok


def _rebound_fail_down(today, *, vwap, daily, rvol, strong, cum=None):
    """日线偏空：早盘先砸后反抽，反抽高点失守 → 早盘反抽失败（可空）。"""
    if daily.get('bias') != 'down' or len(today) < 4:
        return False
    first = today[0]
    if first['c'] >= first['o']:
        return False
    low_i = min(range(len(today)), key=lambda i: today[i]['l'])
    if low_i <= 0 or low_i >= len(today) - 1:
        return False
    bounce = today[low_i:]
    bounce_high = max(b['h'] for b in bounce)
    peak_i = low_i + max(range(len(bounce)), key=lambda i: bounce[i]['h'])
    morning_low = min(b['l'] for b in today)
    if bounce_high < morning_low * 1.004:
        return False
    latest = today[-1]
    span = float(latest['h'] - latest['l'])
    # 峰值在当根：收阴且上影占比高 → 视作反抽拒绝，无需再等下一根
    reject_now = (peak_i == len(today) - 1 and span > 0 and latest['c'] < latest['o']
                  and (latest['h'] - latest['c']) / span >= .5)
    failed_after = (peak_i < len(today) - 1 and latest['c'] < bounce_high
                    and latest['h'] <= bounce_high * 1.0005 and latest['c'] <= today[-2]['c']
                    and (latest['c'] < vwap or peak_i <= len(today) - 3))
    vol_ok = _early_vol_ok(rvol=rvol, strong=strong, cum=cum, fallback_v=latest.get('v'), today=today)
    return (reject_now or failed_after) and vol_ok


def daily_context(rows, now, market='us'):
    local = now.astimezone(ZONES[market])
    # 收盘后留半小时供数据源落地；盘中不使用当天的部分日线。
    allow_today = local.hour*60+local.minute >= SESSIONS[market][-1][1]+30
    cutoff = local.date() if allow_today else local.date()-timedelta(days=1)
    expected = cutoff
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    prior = sorted((b for b in rows if _time(b,market).date() <= cutoff), key=lambda b:b['t'])
    out = dict(bias='unknown', label='日线数据不足', confirmed=False, volume_ready=False,
               volume_label='量能待核', last_volume_ratio=None, pressure=None, as_of=None,
               ma5=None, ma20=None, ma60=None, atr=None, support=None, resistance=None,
               fresh=False, evidence=[])
    dates = [_time(b,market).date() for b in prior]
    if len(prior) < 60 or len(set(dates)) != len(dates) or not all(_price_ok(b) for b in prior):
        out['evidence'] = ['至少需要 60 根有效且日期唯一的完整日线']
        return out
    closes = [float(b['c']) for b in prior]
    ma5,ma20,ma60 = [mean(closes[-n:]) for n in (5,20,60)]
    atr = _atr(prior)
    slope = ma20-mean(closes[-25:-5])
    rising = min(b['l'] for b in prior[-5:]) >= min(b['l'] for b in prior[-10:-5])
    falling = max(b['h'] for b in prior[-5:]) <= max(b['h'] for b in prior[-10:-5])
    bias = 'up' if closes[-1]>ma20>ma60 and slope>0 and rising else (
        'down' if closes[-1]<ma20<ma60 and slope<0 and falling else 'range')
    volume_ready = all(_volume_ok(b) for b in prior[-21:])
    ratio = float(prior[-1]['v'])/mean(float(b['v']) for b in prior[-21:-1]) if volume_ready else None
    pressure = None
    if volume_ready:
        signed = [((1 if b['c']>a['c'] else -1 if b['c']<a['c'] else 0)*b['v']) for a,b in zip(prior[-11:-1],prior[-10:])]
        pressure = sum(signed)/sum(b['v'] for b in prior[-10:])
    distribution = volume_ready and ratio>=1.5 and ((bias=='up' and closes[-1]<closes[-2]) or (bias=='down' and closes[-1]>closes[-2]))
    label = '量价背离' if distribution else '量价同向' if pressure is not None and (
        (bias=='up' and pressure>0) or (bias=='down' and pressure<0)) else '量能中性' if volume_ready else '量能待核'
    out.update(bias=bias, label={'up':'上涨趋势','down':'下跌趋势','range':'震荡/过渡'}[bias],
               confirmed=bias in ('up','down'), volume_ready=volume_ready, volume_label=label,
               last_volume_ratio=round(ratio,3) if ratio is not None else None,
               pressure=round(pressure,3) if pressure is not None else None,
               as_of=dates[-1].isoformat(), fresh=dates[-1]>=expected,
               ma5=round(ma5,4), ma20=round(ma20,4), ma60=round(ma60,4), ma20_slope=round(slope,4),
               atr=round(atr,4) if atr else None, support=min(b['l'] for b in prior[-20:]),
               resistance=max(b['h'] for b in prior[-20:]))
    out['evidence'] = [f'完整日线截至 {out["as_of"]}；MA20 与 MA60 位置及近端结构定方向',
                       f'前一完整日量比 {ratio:.2f}，对比此前 20 日' if ratio is not None else '最近 21 根日线缺少可信成交量']
    return out


def intraday_context(daily, rows, now, market='us'):
    local = now.astimezone(ZONES[market])
    past = [b for b in rows if int(b['t'])+900 <= now.timestamp()]
    slots = [i for a,b in SESSIONS[market] for i in range(a,b,15)]
    past = [b for b in past if (_time(b,market).hour*60+_time(b,market).minute) in slots]
    today = sorted((b for b in past if _time(b,market).date()==local.date()),key=lambda b:b['t'])
    out = dict(code='watch', label='观望', direction='flat', data_ready=False, volume_confirmed=False,
               bar_rvol=None, cumulative_rvol=None, volume_reference_days=0, vwap=None,
               bar_count=len(today), last_close=None, last_bar_end=None, evidence=[], trigger=None,
               structure_stop=None, intraday_atr=None, volume_label='量能待核', error=None)
    if len(today) < 2:
        out['error'] = '今日至少需要 2 根已收盘 15m K 线'
        return out
    actual = [_time(b,market).hour*60+_time(b,market).minute for b in today]
    expected = [slot for slot in slots if slot<=actual[-1]]
    feeds = {b.get('feed') for b in today}
    if actual != expected or len(feeds)>1 or not all(_price_ok(b) and _volume_ok(b) for b in today):
        if feeds <= {'nasdaq_sampled'} and today and not any(_volume_ok(b) for b in today):
            out['error']='仅有Nasdaq无量采样（Yahoo/带量源不可用或被限流），不能确认结构与VWAP'
        else:
            out['error']='今日15m数据缺失、重复或缺少可信成交量，不能确认结构与VWAP'
        return out
    latest = today[-1]
    vwap = sum((b['h']+b['l']+b['c'])/3*b['v'] for b in today)/sum(b['v'] for b in today)
    latest_slot = actual[-1]
    history = {}
    for bar in past:
        when = _time(bar,market)
        if when.date()<local.date() and bar.get('feed')==today[-1].get('feed') and _price_ok(bar) and _volume_ok(bar):
            history.setdefault(when.date(),{}).setdefault(when.hour*60+when.minute,[]).append(bar)
    references = []
    cumulatives = []
    for day, daybars in sorted(history.items(),reverse=True)[:10]:
        if len(daybars.get(latest_slot,[]))==1:
            references.append(daybars[latest_slot][0]['v'])
        if all(len(daybars.get(slot,[]))==1 for slot in expected):
            cumulatives.append(sum(daybars[slot][0]['v'] for slot in expected))
    rvol = latest['v']/mean(references) if len(references)>=3 else None
    cum = sum(b['v'] for b in today)/mean(cumulatives) if len(cumulatives)>=3 else None
    strong = rvol is not None and rvol >= 1.5
    early_kw = dict(vwap=vwap, daily=daily, rvol=rvol, strong=strong, cum=cum)

    def _fill_early(code, label, direction, trigger, stop, atr):
        volume_confirmed = _early_vol_ok(rvol=rvol, strong=strong, cum=cum,
                                         fallback_v=latest.get('v'), today=today)
        out.update(code=code, label=label, direction=direction, data_ready=True,
                   volume_confirmed=volume_confirmed,
                   bar_rvol=round(rvol, 3) if rvol is not None else None,
                   cumulative_rvol=round(cum, 3) if cum is not None else None,
                   volume_reference_days=len(references), vwap=round(vwap, 4), last_close=latest['c'],
                   last_bar_end=int(latest['t']) + 900, trigger=trigger, structure_stop=stop,
                   intraday_atr=atr,
                   volume_label=('放量' if strong else '量能正常' if (rvol is not None and rvol >= .8)
                                 or (cum is not None and cum >= 1.0)
                                 else '缩量' if rvol is not None or cum is not None else '历史同时段量能不足'))
        out['evidence'] = [f'15m 已收盘 {len(today)} 根；收盘 {latest["c"]:.3f}，VWAP估算 {vwap:.3f}',
                           (f'同时段量比 {rvol:.2f}，参考 {len(references)} 个历史交易日'
                            if rvol is not None else '历史同时段样本不足 3 日，量能不能确认')]
        if cum is not None:
            out['evidence'].append(f'早盘累计量比 {cum:.2f}')
        out['evidence'].append(f'早段形态 {label}；止损参考 {stop:.3f}，触发参考 {trigger:.3f}')
        return out

    # 两根即可识别开盘弱势；常规形态仍要满 3 根
    if len(today) < 3:
        if _open_drive_down(today, **early_kw):
            return _fill_early('open_drive', '开盘弱势（趋势日）', 'down',
                               today[0]['h'], max(b['h'] for b in today), _atr(today))
        out['error'] = '今日至少需要 3 根已收盘 15m K 线'
        return out
    closes = [b['c'] for b in today]
    e5,e13 = _ema(closes,5),_ema(closes,13)
    score = (1 if e5>e13 else -1 if e5<e13 else 0)+(1 if closes[-1]>closes[-2] else -1 if closes[-1]<closes[-2] else 0)+(1 if closes[-1]>vwap else -1)
    direction = 'up' if score>=2 else 'down' if score<=-2 else 'flat'
    high = max(b['h'] for b in today[-4:-1])
    low = min(b['l'] for b in today[-4:-1])
    breakout = latest['c']>high if direction=='up' else latest['c']<low if direction=='down' else False
    opposite = direction in ('up','down') and daily['bias'] in ('up','down') and direction!=daily['bias']
    was_up = any(b['c']>vwap for b in today[:-2])
    reversed_down = direction=='down' and was_up and all(b['c']<vwap for b in today[-2:]) and breakout and strong
    reversed_up = (direction=='up' and len(today)>=5 and today[2]['c']<today[0]['c']
                   and all(b['c']>vwap for b in today[-2:]) and breakout and strong)
    code,label = 'watch','观望'
    if daily['bias']=='down' and direction=='up':
        code,label = 'rebound','反弹（日线仍偏空）'
    elif reversed_down:
        code,label = 'reversal','盘中向下反转'
    elif reversed_up:
        code,label = 'reversal','盘中向上反转（回调结束）'
    elif direction==daily['bias'] and breakout and strong:
        code,label = 'acceleration', '上涨加速' if direction=='up' else '下跌加速'
    elif direction==daily['bias'] and breakout and rvol is not None and rvol>=1.2:
        code,label = 'pullback_confirmed','回踩企稳' if direction=='up' else '反抽转弱'
    elif opposite:
        code,label = 'pullback','回调（趋势待守住）'
    elif direction in ('up','down'):
        code,label = 'probe','上行试探' if direction=='up' else '下行试探'
    atr = _atr(today)
    stop = min(b['l'] for b in today[-3:]) if direction=='up' else max(b['h'] for b in today[-3:])
    trigger = high if direction=='up' else low
    # 趋势日早段：先识别反抽失败，再识别仍未反抽的开盘弱势
    if _rebound_fail_down(today, **early_kw):
        low_i = min(range(len(today)), key=lambda i: today[i]['l'])
        bounce_high = max(b['h'] for b in today[low_i:])
        return _fill_early('rebound_fail', '早盘反抽失败', 'down', bounce_high, bounce_high, atr)
    if _open_drive_down(today, **early_kw):
        return _fill_early('open_drive', '开盘弱势（趋势日）', 'down',
                           today[0]['h'], max(b['h'] for b in today), atr)
    volume_confirmed = rvol is not None and rvol >= 1.2
    out.update(code=code,label=label,direction=direction,data_ready=True,volume_confirmed=volume_confirmed,
               bar_rvol=round(rvol,3) if rvol is not None else None,cumulative_rvol=round(cum,3) if cum is not None else None,
               volume_reference_days=len(references), vwap=round(vwap,4),last_close=latest['c'],
               last_bar_end=int(latest['t'])+900,trigger=trigger,
               structure_stop=stop,intraday_atr=atr,
               volume_label='放量' if strong else '量能正常' if rvol is not None and rvol>=.8 else '缩量' if rvol is not None else '历史同时段量能不足')
    out['evidence']=[f'15m 已收盘 {len(today)} 根；收盘 {latest["c"]:.3f}，VWAP估算 {vwap:.3f}',
                     f'同时段量比 {rvol:.2f}，参考 {len(references)} 个历史交易日' if rvol is not None else '历史同时段样本不足 3 日，量能不能确认']
    return out


def build_market_context(daily, bars, now, market='us'):
    if market not in ZONES:
        raise ValueError('不支持的市场')
    day = daily_context(daily,now,market)
    state = intraday_context(day,bars,now,market)
    return dict(version=VERSION,market=market,daily_context=day,intraday_state=state,
                trend_context=dict(bias=day['bias'],confirmed=day['confirmed'],daily_bias=day['bias'],
                                   reason=day['label']+' · '+day['volume_label'],label=day['label']))


def entry_signal(context, *, px, quote_fresh, session_open, now, accept_overnight=False, limits=None, execution_issues=None):
    day,state,market = context['daily_context'],context['intraday_state'],context['market']
    sign = 1 if state['direction']=='up' else -1
    ready = (state['data_ready'] and quote_fresh and session_open and market_session(now,market)['is_rth']
             and 0<=now.timestamp()-(state['last_bar_end'] or 0)<900)
    exit_long = bool(ready and state['code']=='reversal' and state['direction']=='down')
    exit_short = bool(ready and state['code']=='rebound' and state['direction']=='up'
                      and state['volume_confirmed'] and state['last_close']>state['trigger'])
    out = dict(advice='观望',signal='观望',actionable=False,at_entry=False,entry_ready=False,
               entry_low=None,entry_high=None,entry_mid=None,ref_price=px,stop=None,target=None,
               S=None,E_band=None,T1=None,R1=None,worst_R1=None,reason='',action_label='等待确认',
               trend={'up':'上涨','down':'下跌'}.get(day['bias'],'震荡'),structure=state['label'],
               setup=state['label'],exit_long=exit_long,exit_short=exit_short,position_rule=None,
               strength=None,strength_label=None,risk_pct=None,risk_label=None,vwap=state['vwap'],
               strategy_version=VERSION,valid_until_bj=None,target_note=None,stop_note=None,
               execution_note='A股T+1：当日新买持仓不能当日卖出，止损为隔夜预案' if market=='cn' else '美股原股规则研究，未核验借券及实际成交')
    reason = None
    if not session_open or not market_session(now,market)['is_rth']:
        reason='当前非连续交易时段，仅作观察，不生成实时进场信号'
    elif not state['data_ready']:
        reason=state['error']
    elif not quote_fresh:
        reason='报价过期或无法核验报价时间'
    elif now.timestamp()<state['last_bar_end'] or now.timestamp()-state['last_bar_end']>=900:
        reason='15m结构过期，等待新一根完整K线'
    elif not day['fresh'] or not day['confirmed']:
        reason='完整日线不足、新鲜度不足或趋势未确认'
    elif not day['volume_ready'] or day['volume_label']=='量价背离':
        reason='日线成交量不足或量价背离，暂缓进场'
    elif not state['volume_confirmed']:
        reason='盘中量能未确认，或历史同时段样本不足'
    elif state['direction']!=day['bias'] or state['code'] not in ENTRY_SETUP_CODES:
        reason='盘中形态与日线方向不一致，反弹/回调不直接转为新开仓'
    elif market=='cn' and sign<0:
        reason='A股普通现货不发做空信号'
    elif execution_issues:
        reason='；'.join(execution_issues)
    elif market=='cn' and not accept_overnight:
        reason='A股T+1：当日止损无法保证执行，需先确认可接受隔夜风险'
    elif market=='cn' and (not limits or not 0<limits[1]<px<limits[0]):
        reason='缺少或无法核实当天实际涨跌停价'
    elif market=='cn' and (px>=limits[0]*.995 or px<=limits[1]*1.005):
        reason='临近涨跌停且封单不明，暂不提供普通入场'
    if reason:
        out['reason']=reason
        return out
    atr = day['atr']
    intra_atr = state['intraday_atr']
    if not atr or not intra_atr or not math.isfinite(px) or px<=0:
        out['reason']='缺少有效波动或价格数据'
        return out
    early_short = state['code'] in EARLY_SETUP_CODES and sign < 0
    trigger = state['trigger']
    width = max(.25*intra_atr,.0002*trigger)
    low,high = (trigger,trigger+width) if sign>0 else (trigger-width,trigger)
    stop = state['structure_stop']-sign*max(.05*atr,.1*intra_atr)
    mid = (low+high)/2
    visible_target = day['resistance'] if sign>0 else day['support']
    ref = high if early_short else mid
    stretch = abs(atr)
    # 非早段：即使结构很近也保留该结构（盈亏比不足则观望，禁止用 ATR 翻过近端结构）
    if visible_target is not None and sign*(visible_target-ref)>0:
        if early_short and abs(visible_target-ref) < .8*stretch:
            target,note=ref+sign*stretch,'早段趋势日：日线结构过近或已失守，改用1日ATR投射'
        else:
            target,note=visible_target,'日线近20日可见结构位'
    else:
        target,note=ref+sign*stretch,('早段趋势日：以1日ATR投射目标' if early_short
                                       else '无已知前方结构位，以1日ATR估算目标（非保证）')
    # 早段若现价已离开触发区，目标改为从现价再投射 1ATR，避免「止损在高点、目标贴着现价」假盈亏比
    if early_short and sign < 0 and px < high:
        target = min(target, px - stretch)
        note = '早段趋势日：自现价投射1日ATR目标'
    elif early_short and sign > 0 and px > low:
        target = max(target, px + stretch)
        note = '早段趋势日：自现价投射1日ATR目标'
    if market=='cn':
        target=min(target,limits[0])
        if stop<=limits[1]:
            out['reason']='结构止损受跌停价约束，无法按普通止损估算'
            return out
    # 早段空：最差按「贴近触发上沿」估风险；非早段用真实目标结构估盈亏比
    worst_entry = high if sign > 0 else (high if early_short else low)
    gate_target = ((high if early_short else mid) + sign*stretch) if early_short else target
    risk,reward = sign*(worst_entry-stop),sign*(gate_target-worst_entry)
    if low<=0 or risk<=0 or reward<=0 or reward/risk<MIN_RR:
        out['reason']='可见目标空间不足，最差入场盈亏比低于1.5，观望'
        return out
    current_risk,current_reward = sign*(px-stop),sign*(target-px)
    in_band = low <= px <= high
    if early_short and state['code'] == 'rebound_fail':
        # 反抽失败：在触发区附近（含小幅跌破）做空；更深则等回抽
        near_trigger = px >= high * .97
        at_entry = (in_band or (px <= high and near_trigger)) and current_risk>0 and current_reward/current_risk>=MIN_RR
    elif early_short:
        at_entry = px <= high and current_risk>0 and current_reward/current_risk>=MIN_RR
    else:
        at_entry = in_band and current_risk>0 and current_reward/current_risk>=MIN_RR
    expires = datetime.fromtimestamp(state['last_bar_end']+900,ZONES[market])
    if market=='cn':
        end = 690 if expires.hour<12 else 897
        expires=min(expires,expires.replace(hour=end//60,minute=end%60,second=0,microsecond=0))
    if early_short and state['code'] == 'rebound_fail':
        wait_note = '反抽失败区附近可做空' if at_entry else '等待反抽回触发区附近，不在低位追空'
        stop_note = '反抽高点加波动缓冲'
    elif early_short:
        wait_note = '开盘弱势延续，可按计划做空' if at_entry else '等待反抽至开盘高点附近'
        stop_note = '开盘高点加波动缓冲'
    else:
        wait_note = '已回到进场带' if at_entry else '等待回踩/反抽进场带，当前不追价'
        stop_note = '最近三根完整15m结构极值加波动缓冲'
    out.update(advice='做多' if sign>0 else '做空',signal='做多' if sign>0 else '做空',
               actionable=at_entry,at_entry=at_entry,entry_ready=at_entry,entry_low=round(low,4),entry_high=round(high,4),
               entry_mid=round(mid,4),ref_price=round(px if at_entry else mid,4),
               stop=round(stop,4),target=round(target,4),S=round(stop,4),E_band=[round(low,4),round(high,4)],T1=round(target,4),
               R1=round(current_reward/current_risk,3) if at_entry else round(sign*(target-mid)/(sign*(mid-stop)),3),
               worst_R1=round(reward/risk,3),target_note=note,stop_note=stop_note,
               reason=f'{state["label"]}，量能确认，'+wait_note,
               action_label='进场条件满足' if at_entry else '等待到价',valid_until_bj=expires.astimezone(BJ).isoformat(),
               risk_pct=round(current_risk/px*100,3) if at_entry else round(abs(mid-stop)/mid*100,3),
               position_rule=f'已有{("多" if sign>0 else "空")}仓：结构止损 {stop:.3f}；{out["execution_note"]}')
    return out
