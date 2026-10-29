#!/usr/bin/env python3
"""Summarize first-signal directional behavior without profit-target gating."""
import json
import statistics
from datetime import datetime
from pathlib import Path
from research_context import BJ

HERE=Path(__file__).resolve().parent


def summarize(records):
    results=[]
    for symbol in ('SNDK','MU','NVDA'):
        for period in ('before_2026-08-01','from_2026-08-01'):
            for group in ('all','aligned_score_at_least_3'):
                subset=[r for r in records if r['symbol']==symbol and
                    ((r['day']<'2026-08-01')==(period=='before_2026-08-01')) and
                    (group=='all' or r['direction_score']>=3)]
                horizons={}
                for h in ('15','30','60'):
                    values=[r['forward_pct'][h] for r in subset if r['forward_pct'][h] is not None]
                    horizons[h]={'count':len(values), 'same_direction_pct':round(sum(v>0 for v in values)*100/len(values),1) if values else None,
                        'mean_pct':round(statistics.mean(values),3) if values else None}
                results.append({'symbol':symbol,'period':period,'group':group,'first_signals':len(subset),'horizons':horizons})
    return results


def main():
    path=HERE/'.cache'/'first-signal-quality.json'; records=json.loads(path.read_text())['records']
    out={'generated_bj':datetime.now(BJ).isoformat(),'description':'forward price movement uses first simulated entry; positive means move along signal direction; not an executed strategy return; forward horizons ignore any prior exit or stop','results':summarize(records)}
    output=HERE/'.cache'/'direction-quality.json';output.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(output)

if __name__=='__main__':main()
