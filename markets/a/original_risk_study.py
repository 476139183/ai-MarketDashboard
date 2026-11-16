#!/usr/bin/env python3
"""复算原突破入场在全市场风险约束下的六组研究对照，不改前瞻账户。"""
import json, gzip, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

def main():
    from selection_study import ranked_signals
    from adaptive_strategy import market_breadth, breadth_allowed
    from validate_strategy import load_histories, audit_account
    from paper_account import update_account
    from portfolio import period_metrics
    root = Path('markets/a')
    cache = root / '.cache/validation'
    meta = {s['code']: s for s in json.loads((cache / 'universe.json').read_text())['stocks']}
    hist = load_histories()
    index = json.loads((cache / 'adjustment-index.json').read_text())
    cal = [x['date'] for x in index]
    breadth = json.loads((cache / 'breadth-valid.json').read_text()) if (cache / 'breadth-valid.json').exists() else market_breadth(hist, cal)
    allowed = breadth_allowed(breadth, cal)
    daily = json.load(gzip.open(cache / 'selection-candidates.json.gz', 'rt'))
    daily = {d: r for d, r in daily.items() if d in allowed}
    profiles = {k: ranked_signals(daily, cal, k) for k in ('breakout_desc', 'liquidity', 'confirmed_efficiency')}
    traincal = [d for d in cal if d <= '2026-06-30']
    training = {}
    states = {}
    for ranking, signals in profiles.items():
        for policy in ('ma20', 'breadth_trend'):
            k = ranking + '__' + policy
            st = update_account(None, hist, meta, traincal, signals, {}, '2026-01-01', historical_research=True, exit_policy=policy, breadth=breadth)
            audit = audit_account(st)
            m = st['performance']
            training[k] = {'performance': m, 'closed_trades': audit['closed_trades'], 'eligible': m['return_pct'] > 0 and m['max_drawdown_pct'] >= -20 and (audit['closed_trades'] >= 20), 'score': round(m['return_pct'] + m['max_drawdown_pct'], 4)}
            states[k] = st
            print('TRAIN', k, training[k], flush=True)
    choices = [k for k, v in training.items() if v['eligible']]
    selected = max(choices, key=lambda k: (training[k]['score'], k)) if choices else None
    print('LOCKED', selected, flush=True)
    results = {}
    for k, st in states.items():
        ranking, policy = k.split('__')
        st = update_account(st, hist, meta, cal, profiles[ranking], {}, '2026-01-01', historical_research=True, exit_policy=policy, breadth=breadth)
        results[k] = {'training': training[k], 'year': st['performance'], 'check_jul_oct': period_metrics(st['curve'], '2026-07-01', '2026-10-31'), 'monthly': st['monthly'], 'months_at_5pct': st['completed_months_at_target'], 'audit': audit_account(st), 'account': st}
        print('CHECK', k, results[k]['year'], results[k]['check_jul_oct'], {m: v['return_pct'] for m, v in st['monthly'].items()}, flush=True)
    robust = {}
    if selected:
        ranking, policy = selected.split('__')
        for name, fee, start in [('double_cost', 0.003, '2026-01-01'), ('fresh_july', 0.0015, '2026-07-01')]:
            st = update_account(None, hist, meta, cal, profiles[ranking], {}, start, historical_research=True, exit_policy=policy, breadth=breadth, fee=fee)
            robust[name] = {'year_or_period': st['performance'], 'check_jul_oct': period_metrics(st['curve'], '2026-07-01', '2026-10-31'), 'monthly': st['monthly'], 'audit': audit_account(st), 'account': st}
            print('ROBUST', name, robust[name]['year_or_period'], robust[name]['check_jul_oct'], flush=True)
    out = {'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'data_as_of': cal[-1], 'universe_size': len(hist), 'selected_by_training': selected, 'calibration': ['2026-01-01', '2026-06-30'], 'check': ['2026-07-01', '2026-10-31'], 'results': results, 'robustness': robust, 'limitations': ['当前名单、未复权、估算限价和碎股影响结果', '此前已看过历史结果，不是独立样本外验证', '不使用资金流，不称识别真实资金撤离', '本轮事前定义6组，只按训练选择，不挑保留期最好者']}
    (root / 'original-market-risk-study.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print('DONE', flush=True)
if __name__ == '__main__':
    main()
