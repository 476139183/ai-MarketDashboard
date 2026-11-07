"""Isolated-margin OHLC replay engine; no exchange clients or live-account writes."""
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal,ROUND_FLOOR
import math

STEP=14400000
SMALL=300000

@dataclass(frozen=True)
class Rules:
    initial_balance:float=10000
    order_budget:float=1000
    max_positions:int=3
    fee_rate:float=.0006
    slippage_rate:float=.0005
    stop_pct:float=.02
    target_pct:float|None=.04
    leverage:float=10
    margin_fraction:float|None=.2
    margin_scope:str='position'
    max_margin_fraction:float|None=.6


def iso(stamp):return datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat()


def simulate(candles,signals,start,end,*,rules=None,window=STEP,specs=None,funding=None,marks=None,entry_signals=None,risk_schedule=None):
    rules=rules or Rules();specs=specs or {};funding=funding or {}
    if rules.leverage<=0 or (rules.margin_fraction is not None and not 0<rules.margin_fraction<=1):raise ValueError('Invalid leverage/margin fraction')
    if rules.margin_scope not in ('position','portfolio'):raise ValueError('Invalid margin scope')
    if rules.max_margin_fraction is not None and not 0<rules.max_margin_fraction<=1:raise ValueError('Invalid aggregate margin cap')
    entry_signals=entry_signals if entry_signals is not None else signals
    risk_schedule=risk_schedule or {}
    marks=marks or {};mark_maps={s:{r['t']:r for r in rows} for s,rows in marks.items()}
    symbols=list(candles);maps={s:{r['t']:r for r in rows} for s,rows in candles.items()}
    cash=rules.initial_balance;positions={};trades=[];observed={};used=set();curve=[]
    ambiguous=0;max_positions=0;max_margin_use=0;max_gross_leverage=0;peak=rules.initial_balance;drawdown=0
    def close(symbol,reference,stamp,reason):
        nonlocal cash
        p=positions.pop(symbol);exit_price=reference*(1-p['side']*rules.slippage_rate)
        gross=p['side']*(exit_price-p['entry_price'])*p['quantity']
        exit_fee=exit_price*p['quantity']*rules.fee_rate
        cash+=p['margin']+gross-exit_fee
        trade=dict(p,exit_at=stamp,exit_reference=reference,exit_price=exit_price,exit_reason=reason,
                   fees=p['entry_fee']+exit_fee,gross_pnl=gross,net_pnl=gross-p['entry_fee']-exit_fee-p['funding_cost'])
        trade['slippage_cost']=p['quantity']*(abs(p['entry_price']-p['entry_reference'])+abs(exit_price-reference))
        trades.append(trade)
    def mark_at(symbol,row):return mark_maps.get(symbol,{}).get(row['t'],row)
    def guard_liquidation(symbol,price):
        if rules.leverage<=1:return
        p=positions[symbol];side=p['side'];quantity=p['quantity']
        mmr=float(specs.get(symbol,{}).get('mmr',.004))
        remaining=p['margin']+side*quantity*(price-p['entry_price'])
        required=quantity*price*(mmr+rules.fee_rate)
        if remaining<=required:
            raise ValueError(f'{symbol}: liquidation path uncertain at {iso(stamp)}; mark={price}, side={side}, initial_margin={p["initial_margin"]}, remaining={remaining}, required={required}; finer execution/mark data required')
    def intrabar(symbol,r):
        nonlocal ambiguous
        p=positions[symbol];side=p['side']
        mark=mark_at(symbol,r)
        guard_liquidation(symbol,mark['l'] if side==1 else mark['h'])
        hit_stop=r['l']<=p['stop_price'] if side==1 else r['h']>=p['stop_price']
        hit_target=p['target_price'] is not None and (r['h']>=p['target_price'] if side==1 else r['l']<=p['target_price'])
        if hit_stop and hit_target:ambiguous+=1
        if hit_stop or hit_target:
            close(symbol,p['stop_price'] if hit_stop else p['target_price'],r['t']+SMALL-1,'stop_loss' if hit_stop else 'take_profit')
            return True
        return False
    for stamp in range(start,end,SMALL):
        rows={s:maps[s][stamp] for s in symbols};closed=set();boundary=stamp//window*window
        for symbol in list(positions):
            r=rows[symbol];p=positions[symbol];side=p['side']
            rate=funding.get(symbol,{}).get(stamp)
            if rate is not None and p['entry_at']<stamp:
                cost=side*p['quantity']*mark_at(symbol,r)['o']*rate;p['margin']-=cost;p['funding_cost']+=cost
            guard_liquidation(symbol,mark_at(symbol,r)['o'])
            reason=None
            if side*(r['o']-p['stop_price'])<=0:reason='stop_loss'
            elif p['target_price'] is not None and side*(r['o']-p['target_price'])>=0:reason='take_profit'
            elif signals[symbol].get(boundary)!=side:reason='signal_invalid'
            if reason:close(symbol,r['o'],stamp,reason);closed.add(symbol)
        for symbol in symbols:
            direction=entry_signals[symbol].get(boundary,0)
            if direction!=signals[symbol].get(boundary,0):continue
            if not direction or symbol in closed:continue
            key=(symbol,boundary,direction)
            if key not in observed:observed[key]=stamp;continue
            if key in used or stamp<=observed[key] or symbol in positions or len(positions)>=rules.max_positions:continue
            r=rows[symbol];entry=r['o']*(1+direction*rules.slippage_rate)
            spec=specs.get(symbol,{})
            ct=Decimal(str(spec.get('ctVal',1)));lot=Decimal(str(spec.get('lotSz','0.00000001')))
            equity_at_entry=cash+sum(p['margin']+p['side']*p['quantity']*(mark_at(s,rows[s])['o']-p['entry_price']) for s,p in positions.items())
            fraction=rules.margin_fraction/(len(symbols) if rules.margin_scope=='portfolio' else 1) if rules.margin_fraction is not None else None
            budget=equity_at_entry*fraction*rules.leverage if fraction is not None else rules.order_budget
            if rules.max_margin_fraction is not None:
                existing_margin=sum(p['margin'] for p in positions.values())
                mark_open=mark_at(symbol,r)['o']
                immediate_equity_cost_per_margin=rules.leverage*(rules.fee_rate+direction*(entry-mark_open)/entry)
                denominator=1+rules.max_margin_fraction*immediate_equity_cost_per_margin
                if denominator<=0:raise ValueError('Invalid opening cap denominator')
                allowance=max(0,(rules.max_margin_fraction*equity_at_entry-existing_margin)/denominator)
                budget=min(budget,allowance*rules.leverage)
            contracts=(Decimal(str(budget))/Decimal(str(entry))/ct/lot).to_integral_value(rounding=ROUND_FLOOR)*lot
            if contracts<Decimal(str(spec.get('minSz',lot))):continue
            quantity=float(contracts*ct);notional=quantity*entry;fee=notional*rules.fee_rate
            margin=notional/rules.leverage
            if cash<margin+fee:continue
            if 'tier_maxSz' in spec and contracts>Decimal(str(spec['tier_maxSz'])):raise ValueError('Position exceeds verified maintenance tier')
            risk=risk_schedule.get(symbol,{}).get(boundary,{})
            stop_pct=float(risk.get('stop_pct',rules.stop_pct));target_pct=risk.get('target_pct',rules.target_pct)
            if not 0<stop_pct<1 or (target_pct is not None and target_pct<=0):raise ValueError('Invalid stop/target')
            positions[symbol]=dict(symbol=symbol,side=direction,signal_at=boundary,observed_at=observed[key],entry_at=stamp,
                entry_reference=r['o'],entry_price=entry,quantity=quantity,contracts=float(contracts),notional=notional,margin=margin,initial_margin=margin,leverage=rules.leverage,equity_at_entry=equity_at_entry,
                entry_fee=fee,stop_price=entry*(1-direction*stop_pct),target_price=None if target_pct is None else entry*(1+direction*target_pct),funding_cost=0)
            cash-=margin+fee;used.add(key);max_positions=max(max_positions,len(positions))
            after_equity=cash+sum(p['margin']+p['side']*p['quantity']*(mark_at(s,rows[s])['o']-p['entry_price']) for s,p in positions.items())
            positions[symbol]['opening_margin_utilization_pct']=sum(p['margin'] for p in positions.values())/after_equity*100
        # Only after opening-time decisions may this bar's future high/low release cash or capacity.
        for symbol in list(positions):intrabar(symbol,rows[symbol])
        equity=cash+sum(p['margin']+p['side']*p['quantity']*(mark_at(s,rows[s])['c']-p['entry_price']) for s,p in positions.items())
        if equity<=0:raise ValueError('Non-positive account equity')
        max_margin_use=max(max_margin_use,sum(p['margin'] for p in positions.values())/equity*100)
        max_gross_leverage=max(max_gross_leverage,sum(p['quantity']*mark_at(s,rows[s])['c'] for s,p in positions.items())/equity)
        peak=max(peak,equity);drawdown=max(drawdown,(peak-equity)/peak)
        curve.append(dict(t=stamp+SMALL,equity=equity))
    for symbol in list(positions):close(symbol,maps[symbol][end-SMALL]['c'],end-1,'end_of_test')
    curve[-1]['equity']=cash;peak=max(peak,cash);drawdown=max(drawdown,(peak-cash)/peak)
    pnl=sum(t['net_pnl'] for t in trades)
    if not math.isclose(cash,rules.initial_balance+pnl,abs_tol=1e-7):raise AssertionError('Account cash does not reconcile')
    wins=[t['net_pnl'] for t in trades if t['net_pnl']>0];losses=[t['net_pnl'] for t in trades if t['net_pnl']<=0]
    return dict(initial_balance=rules.initial_balance,final_equity=cash,net_profit=pnl,return_pct=(cash/rules.initial_balance-1)*100,
        max_drawdown_pct=drawdown*100,trades=trades,trade_count=len(trades),winning_trades=len(wins),
        win_rate_pct=len(wins)/len(trades)*100 if trades else 0,profit_factor=sum(wins)/abs(sum(losses)) if sum(losses) else None,
        fees=sum(t['fees'] for t in trades),funding_cost=sum(t['funding_cost'] for t in trades),
        slippage_cost=sum(t['slippage_cost'] for t in trades),ambiguous_bars=ambiguous,max_positions=max_positions,max_margin_utilization_pct=max_margin_use,max_gross_leverage=max_gross_leverage,equity_curve=curve)
