#!/usr/bin/env python3
"""公开美股历史日线/分钟样本：独立入库、可复核下载与质量审计。"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from statistics import mean, median
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / '.cache/public-history.sqlite3'
CACHE = ROOT / '.cache/public-history'
HF_REPO = 'mito0o852/OHLCV-1m'
HF_SOURCE = 'HF public OHLCV-1m'
FINMIND_SOURCE = 'FinMind USStockPrice'
NY, BJ = ZoneInfo('America/New_York'), ZoneInfo('Asia/Shanghai')


def symbol(value):
    code = value.strip().upper()
    if not re.fullmatch(r'[A-Z]{1,5}(?:[.-][A-Z])?', code):
        raise ValueError('公开历史源仅支持美股代码')
    return code


def get_json(url, *, headers=None):
    request = Request(url, headers={'User-Agent': 'MarketStudio-history/1.0', **(headers or {})})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('历史接口响应超过大小限制')
        return json.loads(raw)
    except HTTPError as error:
        status = error.code
        error.close()
        raise ValueError(f'历史数据服务 HTTP {status}；请稍后重试或核对服务权限') from None
    except (URLError, TimeoutError):
        raise ValueError('历史数据服务连接失败或超时') from None


def prices(row):
    result = {key: float(row[key]) for key in ('open', 'high', 'low', 'close', 'volume')}
    o, h, l, c, v = (result[key] for key in ('open', 'high', 'low', 'close', 'volume'))
    if not all(math.isfinite(x) for x in (o, h, l, c, v)) or min(o, h, l, c) <= 0 or v < 0:
        raise ValueError('历史 OHLCV 含无效价格或成交量')
    if not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError('历史 OHLC 价序不成立')
    return result


def finmind_daily(code, start, end):
    code = symbol(code)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError('起始日期不能晚于结束日期')
    query = urlencode({'dataset': 'USStockPrice', 'data_id': code,
                       'start_date': first.isoformat(), 'end_date': last.isoformat()})
    token = os.environ.get('FINMIND_TOKEN', '').strip()
    payload = get_json('https://api.finmindtrade.com/api/v4/data?' + query,
                       headers={'Authorization': 'Bearer ' + token} if token else {})
    if not isinstance(payload, dict) or payload.get('status') != 200 or not isinstance(payload.get('data'), list):
        raise ValueError('FinMind 未成功返回日线；请检查限流或服务权限')
    result, seen = [], set()
    for row in payload['data']:
        day = date.fromisoformat(row['date'])
        if row.get('stock_id') != code or not first <= day <= last or day in seen:
            raise ValueError('FinMind 标的、日期范围或重复日线不符合请求')
        seen.add(day)
        values = prices({key: row[key.capitalize()] for key in ('open', 'high', 'low', 'close', 'volume')})
        adjusted = float(row['Adj_Close']) if row.get('Adj_Close') is not None else None
        if adjusted is not None and (not math.isfinite(adjusted) or adjusted <= 0):
            raise ValueError('FinMind 复权收盘价无效')
        result.append(dict(date=day.isoformat(), **values, adjusted_close=adjusted))
    if not result:
        raise ValueError('FinMind 在所选日期范围没有日线，不补造样本')
    return sorted(result, key=lambda row: row['date'])


def connect(db):
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT, timestamp INTEGER, trading_date TEXT, session TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            source TEXT, collected_at TEXT, PRIMARY KEY(symbol,timestamp,source));
        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
            volume REAL, adjusted_close REAL, source TEXT, collected_at TEXT,
            PRIMARY KEY(symbol,date,source));
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY, source TEXT, created_bj TEXT, metadata TEXT);
    ''')
    keys = [row[1] for row in conn.execute('PRAGMA table_info(bars)') if row[5]]
    if keys != ['symbol', 'timestamp', 'source']:
        conn.close()
        raise ValueError('请使用独立的公开样本数据库，不能写入实时报价数据库')
    return conn


def record_import(conn, source, metadata):
    conn.execute('INSERT INTO imports(source,created_bj,metadata) VALUES(?,?,?)',
                 (source, datetime.now(BJ).isoformat(), json.dumps(metadata, ensure_ascii=False)))


def store_daily(db, code, rows, source=FINMIND_SOURCE):
    code = symbol(code)
    now = datetime.now(BJ).isoformat()
    with closing(connect(db)) as conn, conn:
        seen = set()
        for row in rows:
            day = date.fromisoformat(row['date']).isoformat()
            if day in seen:
                raise ValueError('重复日线')
            seen.add(day)
            b = prices(row)
            conn.execute('INSERT OR REPLACE INTO daily_bars VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (code, day, b['open'], b['high'], b['low'], b['close'], b['volume'],
                          row.get('adjusted_close'), source, now))
        record_import(conn, source, {'symbol': code, 'rows': len(seen),
                      'adjustment': 'OHLC保留供应商口径；Adj_Close单独保存，不混合拼接'})
    return len(seen)


def minute_stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('分钟时间必须明确时区，不能猜测秒/毫秒或本地时区')
    stamp = value.timestamp()
    if stamp % 60:
        raise ValueError('分钟时间未对齐整分钟')
    return int(stamp)


def import_minutes(db, rows, *, symbols, source=HF_SOURCE, month, metadata=None):
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month):
        raise ValueError('月份格式须为 YYYY-MM')
    selected, seen, stored = {symbol(code) for code in symbols}, set(), 0
    counts = defaultdict(int)
    off_session = 0
    now = datetime.now(BJ).isoformat()
    with closing(connect(db)) as conn, conn:
        for row in rows:
            code = row.get('ticker')
            if code not in selected:
                continue
            stamp = minute_stamp(row['timestamp'])
            local = datetime.fromtimestamp(stamp, NY)
            if month not in (local.strftime('%Y-%m'), datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m')):
                raise ValueError('分钟日期不属于文件月份')
            if (code, stamp) in seen:
                raise ValueError('同一文件出现重复股票/分钟')
            seen.add((code, stamp))
            b = prices(row)
            minute = local.hour * 60 + local.minute
            outside = local.weekday() >= 5 or not 240 <= minute < 1200
            session = 'off_session' if outside else 'pre' if minute < 570 else 'regular' if minute < 960 else 'post'
            off_session += int(outside)
            conn.execute('INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (code, stamp, local.date().isoformat(), session,
                          b['open'], b['high'], b['low'], b['close'], b['volume'], source, now))
            stored += 1
            counts[code] += 1
        result = {'month': month, 'symbols': sorted(selected), 'stored': stored,
                  'source': source, 'by_symbol': dict(counts), 'missing_symbols': sorted(selected-set(counts)),
                  'off_session_rows': off_session,
                  'adjustment': '上游复权口径尚未独立核验', **(metadata or {})}
        record_import(conn, source, result)
    return result


def hf_catalog():
    base = 'https://huggingface.co/api/datasets/' + HF_REPO
    info = get_json(base)
    revision = info.get('sha', '')
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('公开数据仓库未返回固定版本号')
    rows = get_json(base + '/tree/' + revision + '/data?limit=1000')
    if not isinstance(rows, list):
        raise ValueError('公开数据目录格式不正确')
    return revision, [row for row in rows if row.get('type') == 'file'
                      and re.fullmatch(r'data/ohlcv_\d{4}-(?:0[1-9]|1[0-2])\.parquet', row.get('path', ''))]


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download_month(entry, cache, revision, *, max_bytes):
    name = entry.get('path', '')
    if not re.fullmatch(r'data/ohlcv_\d{4}-(?:0[1-9]|1[0-2])\.parquet', name) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('无效的公开月份文件或版本')
    size, digest = int(entry['size']), entry.get('lfs', {}).get('oid', '')
    if not re.fullmatch(r'[a-f0-9]{64}', digest) or size <= 0:
        raise ValueError('公开文件缺少大小或 SHA256 校验信息')
    cache = Path(cache)
    target = cache / (revision + '_' + name.split('/')[-1])
    if target.is_file() and target.stat().st_size == size and file_digest(target) == digest:
        return target
    if size > max_bytes:
        raise ValueError(f'公开文件超过下载预算：需要 {size} 字节，预算 {max_bytes} 字节')
    cache.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.partial')
    url = f'https://huggingface.co/datasets/{HF_REPO}/resolve/{revision}/{name}'
    try:
        with urlopen(Request(url, headers={'User-Agent': 'MarketStudio-history/1.0'}), timeout=60) as response, temporary.open('wb') as stream:
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > min(max_bytes, size):
                    raise ValueError('实际下载量超过预算或文件声明大小')
                stream.write(chunk)
        if received != size or file_digest(temporary) != digest:
            raise ValueError('下载长度或 SHA256 不匹配，已拒绝入库')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def parquet_rows(path, symbols):
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError:
        raise ValueError('分钟文件导入需要 pyarrow；请安装 requirements-research.txt') from None
    columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'ticker']
    with pq.ParquetFile(path) as parquet:
        if any(name not in parquet.schema_arrow.names for name in columns):
            raise ValueError('公开 Parquet 缺少标准 OHLCV/股票/时间字段')
        selected = pa.array([symbol(code) for code in symbols])
        for batch in parquet.iter_batches(batch_size=65536, columns=columns):
            batch = batch.filter(pc.is_in(batch.column('ticker'), value_set=selected))
            yield from batch.to_pylist()


def audit_history(db):
    if not Path(db).is_file():
        return {'daily': [], 'minute': [], 'imports': [], 'available': False}
    with closing(sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        daily = [dict(row) for row in conn.execute('SELECT symbol,source,min(date) first,max(date) last,count(*) rows FROM daily_bars GROUP BY symbol,source')]
        groups = defaultdict(list)
        for row in conn.execute("SELECT * FROM bars WHERE session='regular' ORDER BY symbol,source,timestamp"):
            groups[(row['symbol'], row['source'], row['trading_date'])].append(dict(row))
        summaries = {}
        for (code, source, day), rows in groups.items():
            out = summaries.setdefault((code, source), dict(symbol=code, source=source, first=day, last=day,
                       rows=0, observed_days=0, months=[], complete_regular_days=0, missing_regular_slots=0, zero_volume_rows=0))
            if day[:7] not in out['months']:
                out['months'].append(day[:7])
            start = int(datetime.fromisoformat(day).replace(hour=9, minute=30, tzinfo=NY).timestamp())
            expected = set(range(start, start + 390 * 60, 60))
            actual = {row['timestamp'] for row in rows}
            zero = sum(row['volume'] is None or row['volume'] <= 0 for row in rows)
            out.update(first=min(out['first'], day), last=max(out['last'], day), rows=out['rows'] + len(rows),
                       observed_days=out['observed_days'] + 1,
                       complete_regular_days=out['complete_regular_days'] + int(actual == expected and zero == 0),
                       missing_regular_slots=out['missing_regular_slots'] + len(expected - actual),
                       zero_volume_rows=out['zero_volume_rows'] + zero)
        outside_count = conn.execute("SELECT count(*) FROM bars WHERE session='off_session'").fetchone()[0]
        imports = [{**json.loads(row['metadata']), 'source': row['source'], 'created_bj': row['created_bj']}
                   for row in conn.execute('SELECT * FROM imports ORDER BY id DESC LIMIT 100')]
        for out in summaries.values():
            out['months'].sort()
    return {'available': True, 'daily': daily, 'minute': list(summaries.values()), 'imports': imports,
            'off_session_rows': outside_count,
            'limitations': ['仅months列出的月份有样本，first/last之间并非连续覆盖',
                           '完整常规盘按390个真实分钟核对；半日市保守排除，未填补缺失分钟',
                           '覆盖范围由实际记录推导，不保证范围内每个交易日完整',
                           '公开分钟源复权/交易所量能覆盖未独立验证；仅供离线研究',
                           'FinMind日线与公开分钟线不混合生成可执行信号']}


def study_daily(db, *, horizons=(5, 20)):
    """只检查日线方向与未来价格关系；不冒充盘中成交收益。"""
    from dashboard.market_states import daily_context
    horizons = sorted(set(horizons))
    if not horizons or any(not isinstance(h, int) or not 1 <= h <= 60 for h in horizons):
        raise ValueError('观察窗口必须为 1–60 个交易日')
    with closing(sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        universe = defaultdict(list)
        for row in conn.execute('SELECT * FROM daily_bars ORDER BY symbol,source,date'):
            universe[(row['symbol'], row['source'])].append(dict(row))
    groups = defaultdict(list)
    coverage = []
    for (code, source), rows in universe.items():
        history = []
        coverage.append(dict(symbol=code, source=source, first=rows[0]['date'], last=rows[-1]['date'], rows=len(rows)))
        for i, row in enumerate(rows):
            when = datetime.fromisoformat(row['date']).replace(hour=17, tzinfo=NY)
            history.append(dict(t=int(when.replace(hour=9, minute=30).timestamp()),
                                o=row['open'], h=row['high'], l=row['low'], c=row['close'], v=row['volume']))
            if len(history) < 60:
                continue
            context = daily_context(history[-85:], when, market='us')
            for horizon in horizons:
                if i + horizon >= len(rows):
                    continue
                future = rows[i + horizon]
                change = (future['close'] / row['close'] - 1) * 100
                groups[(code, source, row['date'][:4], context['bias'], horizon)].append(
                    (change, min(row['close'], future['close']) < 1))
    result = []
    for (code, source, year, bias, horizon), values in sorted(groups.items()):
        changes = [value[0] for value in values]
        hit = mean((value > 0 if bias == 'up' else value < 0) for value in changes) * 100 if bias in ('up', 'down') else None
        result.append(dict(symbol=code, source=source, year=year, bias=bias, horizon=horizon,
                      observations=len(values), mean_forward_pct=round(mean(changes), 4),
                      median_forward_pct=round(median(changes), 4), directional_hit_pct=round(hit, 2) if hit is not None else None,
                      low_price_observations=sum(value[1] for value in values)))
    return dict(method='每日收盘后仅用已完成日线判方向，按股票/来源/年份分别统计未来5/20个交易日价格变化；不调参',
                coverage=coverage, groups=result,
                limitations=['日线方向研究不是15m入场成交回测，未计算账户收益或交易成本',
                             '前瞻窗口重叠，观察数不等于独立样本数；当前自选样本存在选择/存活偏差',
                             'OHLC按供应商返回口径，Adj_Close单独保留；低于1美元的历史价格有精度风险',
                             '不得将日线结果当成盘中策略有效或应替换线上参数的依据'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest='command', required=True)
    daily = commands.add_parser('daily', help='导入 FinMind 美股日线')
    daily.add_argument('--symbols', required=True)
    daily.add_argument('--start', required=True)
    daily.add_argument('--end', default=(datetime.now(NY).date() - timedelta(days=1)).isoformat())
    commands.add_parser('catalog', help='列出公开月度文件，不下载数据')
    minute = commands.add_parser('minute', help='下载固定版本月文件并筛选股票')
    minute.add_argument('--symbols', required=True)
    minute.add_argument('--months', required=True, help='YYYY-MM，用逗号分隔')
    minute.add_argument('--max-download-mb', type=int, default=256)
    minute.add_argument('--cache', type=Path, default=CACHE)
    commands.add_parser('audit', help='检查本地样本实际覆盖与完整交易日')
    study = commands.add_parser('daily-study', help='用已入库日线检查跨年方向，不模拟盘中成交')
    study.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'catalog':
            revision, entries = hf_catalog()
            result = {'repo': HF_REPO, 'revision': revision, 'files': entries}
        elif args.command == 'audit':
            result = audit_history(args.db)
        elif args.command == 'daily-study':
            report = study_daily(args.db)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            result = {'output': str(args.output), 'stocks': len(report['coverage']), 'groups': len(report['groups'])}
        elif args.command == 'daily':
            outcomes = []
            for code in dict.fromkeys(args.symbols.split(',')):
                code = symbol(code)
                rows = finmind_daily(code, args.start, args.end)
                count = store_daily(args.db, code, rows)
                outcomes.append({'symbol': code, 'rows': count, 'first': rows[0]['date'], 'last': rows[-1]['date']})
                print(json.dumps(outcomes[-1], ensure_ascii=False), flush=True)
            result = {'source': FINMIND_SOURCE, 'results': outcomes, 'db': str(args.db)}
        else:
            selected = [symbol(code) for code in args.symbols.split(',')]
            revision, entries = hf_catalog()
            by_month = {entry['path'][11:18]: entry for entry in entries}
            requested = list(dict.fromkeys(args.months.split(',')))
            if not requested or any(month not in by_month for month in requested):
                raise ValueError('所选月份不存在；请先运行 catalog 查看实际可用文件')
            budget, outcomes = args.max_download_mb * 1024 * 1024, []
            # 整批先检查预算，不在下载一半后才发现剩余文件超额。
            needed = sum(by_month[month]['size'] for month in requested)
            if budget <= 0 or needed > budget:
                raise ValueError(f'所选文件合计 {needed / 1024**2:.1f} MB，超过下载预算 {args.max_download_mb} MB')
            for month in requested:
                entry = by_month[month]
                path = download_month(entry, args.cache, revision, max_bytes=budget)
                report = import_minutes(args.db, parquet_rows(path, selected), symbols=selected,
                         month=month, metadata={'repo': HF_REPO, 'revision': revision,
                         'file': entry['path'], 'sha256': entry['lfs']['oid'], 'bytes': entry['size']})
                outcomes.append(report)
                print(json.dumps(report, ensure_ascii=False), flush=True)
            result = {'source': HF_SOURCE, 'results': outcomes, 'db': str(args.db)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'历史样本接入失败：{error}\n')


if __name__ == '__main__':
    main()
