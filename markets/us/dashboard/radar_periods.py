"""Radar display periods and complete OHLCV aggregation."""
import math

MINUTE_PERIODS = (1, 5, 10, 15, 30, 60)
PERIODS = MINUTE_PERIODS + (240,)


def period_minutes(value=240):
    if isinstance(value, bool) or str(value) not in tuple(str(n) for n in PERIODS):
        raise ValueError('周期仅支持 1 / 5 / 10 / 15 / 30 / 60 分钟及原 4 小时周期')
    return int(value)


def period_label(minutes):
    return '4 小时' if minutes == 240 else f'{minutes} 分钟'


def native_bar(minutes):
    return '4H' if minutes == 240 else '1H' if minutes == 60 else f'{minutes}m'


def aggregate_rows(rows, *, source_minutes, target_minutes, now, anchor_ms=0, include_open=False):
    if target_minutes % source_minutes or target_minutes < source_minutes:
        raise ValueError('目标周期必须为源周期的整数倍')
    source_step, step = source_minutes * 60000, target_minutes * 60000
    unique = {}
    for row in rows:
        stamp = int(row[0])
        if stamp > now or (not include_open and stamp + source_step > now):
            continue
        values = [float(v) for v in row[1:7]]
        if (len(values) != 6 or not all(math.isfinite(v) for v in values)
                or min(values[:4]) <= 0 or min(values[4:]) < 0
                or not values[2] <= min(values[0],values[3]) <= max(values[0],values[3]) <= values[1]):
            raise ValueError('聚合源 K 线字段无效')
        normalized = [stamp, *values]
        if stamp in unique and unique[stamp] != normalized:
            raise ValueError('聚合源 K 线重复且冲突')
        unique[stamp] = normalized
    groups = {}
    for stamp, row in unique.items():
        bucket = (stamp-anchor_ms)//step*step+anchor_ms
        groups.setdefault(bucket, []).append(row)
    result = []
    for bucket, children in sorted(groups.items()):
        children.sort(key=lambda row: row[0])
        end = min(bucket+step, (now-anchor_ms)//source_step*source_step+anchor_ms+source_step) if include_open else bucket+step
        expected = list(range(bucket,end,source_step))
        if (not include_open and bucket+step > now) or [row[0] for row in children] != expected:
            continue
        result.append([bucket,children[0][1],max(r[2] for r in children),min(r[3] for r in children),
                       children[-1][4],sum(r[5] for r in children),sum(r[6] for r in children)])
    return result
