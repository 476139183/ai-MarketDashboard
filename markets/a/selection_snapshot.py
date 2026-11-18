"""全市场选股研究的只读摘要，不作为自动交易候选来源。"""
from datetime import date
import json
from pathlib import Path

LATEST=Path(__file__).with_name('selection-latest.json')


def load(path=LATEST):
    try:
        result=json.loads(path.read_text())
        date.fromisoformat(result['signal_date'])
        if result.get('kind')!='a_share_selection_research' or result.get('execution_enabled') is not False:return None
        if not isinstance(result.get('total'),int) or result['total']<=0:return None
        counts=result.get('stages',{})
        if not isinstance(counts,dict) or not counts or any(type(v) is not int or v<0 for v in counts.values()) or sum(counts.values())!=result['total']:return None
        return result
    except (OSError,ValueError,KeyError,TypeError):return None
