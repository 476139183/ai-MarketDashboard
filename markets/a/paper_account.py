"""只消费事前存档信号的共享现金模拟；不追认错过的历史买点。"""
from copy import deepcopy
from datetime import datetime

from portfolio import period_metrics
from trend_exit import assess_exit


def update_account(account, histories, metadata, calendar, signals, flows, start_date,
                   historical_research=False, exit_policy='trend_flow', fee=.0015, breadth=None):
    mode = 'historical_research' if historical_research else 'forward_simulation'
    if account and account.get('mode', 'forward_simulation') != mode:
        raise ValueError('历史研究账户与前瞻账户不能混用')
    if exit_policy not in ('trend_flow', 'ma20', 'ma10', 'breadth_trend'):
        raise ValueError('未知退出规则')
    if exit_policy=='breadth_trend' and breadth is None:
        raise ValueError('全市场趋势退出缺少覆盖比例')
    state = deepcopy(account) if account else {
        'start_date': start_date, 'cash': 1.0, 'positions': {}, 'pending_sells': {},
        'events': [], 'curve': [], 'last_day': None, 'max_positions': 5,
        'exit_assessments': {}, 'missing_position_prices': []}
    state.update(mode=mode, exit_policy=exit_policy, fee_per_side=fee)
    if not calendar:
        raise ValueError('缺少交易日历')
    by_date = {c: {b['date']: b for b in bars} for c, bars in histories.items()}
    signal_dates = {}
    for signal in signals:
        if historical_research:
            signal_dates[signal['signal_date']] = signal['stocks']
            continue
        if 'recorded_at' not in signal:
            raise ValueError('前瞻模拟信号缺少真实记录时间')
        recorded = datetime.fromisoformat(signal['recorded_at'])
        # 只有信号日盘后真实保存的信号才能在次一交易日模拟成交。
        if recorded.date().isoformat() == signal['signal_date'] and recorded.hour >= 17:
            signal_dates[signal['signal_date']] = signal['stocks']
    previous_dates = dict(zip(calendar[1:], calendar))
    missing = set(state.get('missing_position_prices', []))
    for day in calendar:
        if day < state['start_date'] or (state['last_day'] and day <= state['last_day']):
            continue
        previous_day = previous_dates.get(day)
        positions = state['positions']
        # 持仓即使不在新候选中，也持续判断退出；不能因离开候选池忽略风险。
        for code in list(state['pending_sells']):
            p = positions[code]
            bar = by_date.get(code, {}).get(day)
            if not bar or not previous_day or p['entry_date'] >= day:
                continue
            prev = by_date[code].get(previous_day)
            ratio = .3 if p['board'] == '北交所' else .2 if p['board'] in ('创业板', '科创板') else .1
            if not prev or bar['volume'] <= 0 or bar['open'] <= prev['close'] * (1-ratio)*1.005:
                continue
            state['cash'] += p['quantity'] * bar['open'] * (1-fee)
            state['events'].append({'side': 'sell', 'code': code, 'date': day, 'price': bar['open'],
                                    'quantity': p['quantity'], 'reason': state['pending_sells'].pop(code)})
            del positions[code]
        equity_before = state['cash'] + sum(
            p['quantity']*by_date.get(c, {}).get(day, {}).get('open', p['last_close'])
            for c, p in positions.items())
        for signal in signal_dates.get(previous_day, []):
            code = signal['code']
            if previous_day < state['start_date'] or code in positions or len(positions) >= state['max_positions']:
                continue
            bar, prev = by_date.get(code, {}).get(day), by_date.get(code, {}).get(previous_day)
            if not bar or not prev or code not in metadata or bar['volume'] <= 0:
                continue
            m = metadata[code]
            if 'ST' in m['name'].upper() or '退' in m['name']:
                continue
            ratio = .3 if m['board'] == '北交所' else .2 if m['board'] in ('创业板', '科创板') else .1
            if bar['open'] <= 0 or abs(bar['open']/prev['close']-1) >= .09 or bar['open'] >= prev['close']*(1+ratio)*.995:
                continue
            allocation = min(state['cash'], equity_before/state['max_positions'])
            if allocation <= 1e-12:
                continue
            quantity = allocation/(bar['open']*(1+fee))
            state['cash'] -= allocation
            positions[code] = {'name': m['name'], 'board': m['board'], 'entry_date': day,
                               'entry_price': bar['open'], 'quantity': quantity, 'last_close': bar['close']}
            state['events'].append({'side': 'buy', 'code': code, 'date': day,
                                    'signal_date': previous_day, 'price': bar['open'], 'quantity': quantity})
        for code, p in positions.items():
            bar = by_date.get(code, {}).get(day)
            if not bar:
                missing.add(code)
                continue
            missing.discard(code)
            p['last_close'] = bar['close']
            past = [b for b in histories[code] if b['date'] <= day]
            if exit_policy == 'trend_flow':
                decision = assess_exit(past, p['entry_price'], flows.get(code))
                coverage = state.setdefault('flow_decision_coverage', {}).setdefault(day[:7], {'total': 0, 'known': 0})
                coverage['total'] += 1
                coverage['known'] += decision.get('flow_status') not in ('缺少连续两日资金流', '未核验', None)
            else:
                length = 20 if exit_policy in ('ma20','breadth_trend') else 10
                average = sum(b['close'] for b in past[-length:])/len(past[-length:])
                should_exit = bar['close'] < average or bar['close'] <= p['entry_price']*.95
                decision = {'action': '退出' if should_exit else '持有',
                            'reason': f'收盘跌破MA{length}或触发原5%风险阈值'}
                if exit_policy=='breadth_trend' and breadth.get(day,1)<.4:
                    weak=assess_exit(past,p['entry_price'],{})
                    if weak['action']=='退出':
                        decision={'action':'退出','reason':'全市场趋势覆盖低于40%且'+weak['reason']}
            state['exit_assessments'][code] = decision
            if decision['action'] == '退出':
                state['pending_sells'].setdefault(code, decision['reason'])
        equity = state['cash'] + sum(p['quantity']*p['last_close'] for p in positions.values())
        state['curve'].append({'date': day, 'equity': equity})
        state['last_day'] = day
    state['missing_position_prices'] = sorted(missing)
    state['performance'] = period_metrics(state['curve'], state['start_date'], calendar[-1])
    months = sorted({x['date'][:7] for x in state['curve']})
    state['monthly'] = {m: period_metrics(state['curve'], m+'-01', m+'-31') for m in months}
    state['monthly_target_pct'] = 5
    completed_before = calendar[-1][:7] if historical_research else start_date[:7]
    completed = {m: r for m, r in state['monthly'].items()
                 if m+'-01' >= state['start_date'] and m < completed_before}
    state['completed_months'] = len(completed)
    state['completed_months_at_target'] = sum(r['return_pct'] >= 5 for r in completed.values())
    state['monthly_note'] = '当月及启动不足整月的月份不计完整月达标次数'
    state['status'] = ('历史研究回放，非事前信号绩效' if historical_research else
                       '尚无启动后的完整交易日' if not state['curve'] else '按事前快照进行研究模拟')
    return state
