"""统一日线字段：date/open/high/low/close/volume。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _f(v: Any) -> float:
    if v is None:
        return 0.0
    return float(v)


def normalize_bars(rows: Iterable[dict], *, min_bars: int = 1) -> List[Dict[str, Any]]:
    by_date: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        date = str(raw.get('date') or raw.get('Date') or '')[:10]
        if len(date) != 10:
            continue
        try:
            bar = {
                'date': date,
                'open': _f(raw.get('open', raw.get('Open'))),
                'high': _f(raw.get('high', raw.get('High'))),
                'low': _f(raw.get('low', raw.get('Low'))),
                'close': _f(raw.get('close', raw.get('Close'))),
                'volume': _f(raw.get('volume', raw.get('Volume', 0))),
            }
        except (TypeError, ValueError):
            continue
        if not (bar['high'] >= bar['low'] and bar['close'] > 0 and bar['open'] > 0):
            continue
        by_date[date] = bar
    bars = [by_date[k] for k in sorted(by_date)]
    if len(bars) < min_bars:
        raise ValueError(f'有效日线不足 {min_bars} 根（得 {len(bars)}）')
    if any(a['date'] >= b['date'] for a, b in zip(bars, bars[1:])):
        raise ValueError('日线日期乱序或重复')
    return bars


def last_n(bars: List[dict], days: Optional[int]) -> List[dict]:
    if not days or days <= 0 or len(bars) <= days:
        return bars
    return bars[-days:]
