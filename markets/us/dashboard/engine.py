#!/usr/bin/env python3
"""美股盘中信号引擎（勿用于 A股）：关键位 S → E → T1；时间北京。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo

    BJ = ZoneInfo("Asia/Shanghai")
    NY = ZoneInfo("America/New_York")
except Exception:
    BJ = timezone(timedelta(hours=8))
    NY = timezone(timedelta(hours=-4))

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_SYMBOLS = ["SNDK", "MU", "NVDA"]
CACHE_DIR = Path(__file__).resolve().parent / "cache"
# 策略周期仅 15m；底层仍用 Nasdaq 分时点聚合 + 日高/日低，避免漏判突破
INTERVALS = ("15m",)
INTERVAL_MINUTES = {"15m": 15}
ORB_WINDOW_MINUTES = 30
DEFAULT_INTERVAL = "15m"

# 进程内信号时间记忆：symbol → {advice, since_bj}
_SIGNAL_MEM: Dict[str, Dict[str, str]] = {}


def normalize_interval(interval: Optional[str]) -> str:
    """对外只认 15m（兼容旧参数，一律归一）。"""
    return DEFAULT_INTERVAL


def orb_bar_count(interval: str) -> int:
    mins = INTERVAL_MINUTES.get(normalize_interval(interval), 15)
    return max(1, ORB_WINDOW_MINUTES // mins)


def _parse_chart(d: dict) -> Tuple[List[dict], dict]:
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c):
            continue
        rows.append(
            {
                "t": t,
                "o": float(o),
                "h": float(h),
                "l": float(l),
                "c": float(c),
                "v": float(v or 0),
                "dt_ny": datetime.fromtimestamp(t, NY),
                "dt_bj": datetime.fromtimestamp(t, BJ),
            }
        )
    return rows, r.get("meta", {})


def _http_json(url: str, timeout: int = 4, extra_headers: Optional[dict] = None) -> dict:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cache_path(symbol: str, interval: str) -> Path:
    if interval == "1d":
        tag = "1d"
    elif interval in INTERVALS:
        tag = interval
    else:
        tag = interval.replace("/", "_")
    return CACHE_DIR / f"{symbol}_{tag}.json"


def _load_cache(symbol: str, interval: str) -> Optional[Tuple[List[dict], dict]]:
    fp = _cache_path(symbol, interval)
    if not fp.is_file():
        alts = list(CACHE_DIR.glob(f"{symbol}_*.json")) if CACHE_DIR.is_dir() else []
        for a in alts:
            if interval == "1d" and ("_1d" in a.name or a.name.endswith("_1d.json")):
                fp = a
                break
            if interval in INTERVALS and f"_{interval}" in a.name:
                fp = a
                break
        else:
            if not fp.is_file():
                return None
    try:
        with open(fp) as f:
            d = json.load(f)
        return _parse_chart(d)
    except Exception:
        return None


def _save_cache(symbol: str, interval: str, raw: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(symbol, interval).write_text(json.dumps(raw), encoding="utf-8")
    except Exception:
        pass


def fetch_chart(symbol: str, range_: str, interval: str, prefer_cache: bool = False) -> Tuple[List[dict], dict, str]:
    """Returns rows, meta, provenance ('live'|'cache')."""
    if prefer_cache:
        cached = _load_cache(symbol, interval)
        if cached:
            return cached[0], cached[1], "cache"

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval={interval}&includePrePost=true"
    )
    last_err = None
    for u in (url, url.replace("query1", "query2")):
        try:
            d = _http_json(u, timeout=3)
            rows, meta = _parse_chart(d)
            if rows:
                _save_cache(symbol, interval, d)
                return rows, meta, "live"
        except Exception as e:
            last_err = e
            continue
    cached = _load_cache(symbol, interval)
    if cached:
        rows, meta = cached
        return rows, meta, "cache"
    raise RuntimeError(f"Yahoo 失败且无本地缓存: {last_err}")


def _parse_money(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("$", "").replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


# 短缓存：同一次看板刷新多标的/同秒重复请求不打爆 Nasdaq；定时 30s+ 仍会拿新价
_QUOTE_MEM: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_QUOTE_TTL_SEC = 3.0


def fetch_nasdaq_quote(symbol: str, *, force: bool = False) -> Optional[Dict[str, Any]]:
    """盘前/盘后优先读 primaryData（实时），收盘在 secondaryData。"""
    import time as _time

    now = _time.time()
    hit = _QUOTE_MEM.get(symbol)
    if not force and hit and now - hit[0] < _QUOTE_TTL_SEC:
        return hit[1]

    url = f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=stocks"
    try:
        d = _http_json(
            url,
            timeout=5,
            extra_headers={
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
        )
    except Exception:
        return hit[1] if hit else None
    data = (d or {}).get("data") or {}
    primary = data.get("primaryData") or {}
    secondary = data.get("secondaryData") or {}
    status = data.get("marketStatus") or ""
    px = _parse_money(primary.get("lastSalePrice"))
    # 盘前/盘后：secondary 常为昨收；开盘后 secondary 常为 null，不能用现价冒充昨收
    prev = _parse_money((secondary or {}).get("lastSalePrice")) if secondary else None
    net = _parse_money(str(primary.get("netChange") or "").replace("+", ""))
    chg = _parse_money(str(primary.get("percentageChange") or "").replace("%", "").replace("+", ""))
    if px is None and prev is not None:
        px = prev
    # 用净涨跌反推昨收（Open 时最可靠）
    if prev is None and px is not None and net is not None:
        prev = px - net
    # 仍不要把 prev 设成 px，否则涨跌幅永远 0%
    # 日高/日低：分时采样常漏尖峰，结构突破必须用 keyStats（例 1305 vs 分时 1300.75）
    day_hi = day_lo = None
    ks = data.get("keyStats") or {}
    dr = ((ks.get("dayrange") or {}).get("value") or "").strip()
    if " - " in dr:
        a, b = dr.split(" - ", 1)
        day_lo, day_hi = _parse_money(a), _parse_money(b)
    out = {
        "price": px,
        "prev_close": prev,
        "net_change": net,
        "market_status": status,
        "ts": primary.get("lastTradeTimestamp") or (secondary or {}).get("lastTradeTimestamp"),
        "change_pct": chg,
        "day_high": day_hi,
        "day_low": day_lo,
    }
    _QUOTE_MEM[symbol] = (now, out)
    return out


def fetch_yahoo_quote_meta(symbol: str) -> Optional[dict]:
    """轻量拉 1d/1m meta，拿 fulldayPrice（比 3mo 日线快）。"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range=1d&interval=1m&includePrePost=true"
    )
    for u in (url, url.replace("query1", "query2")):
        try:
            d = _http_json(u, timeout=3)
            r = (d.get("chart") or {}).get("result") or []
            if r:
                return r[0].get("meta") or {}
        except Exception:
            continue
    return None


def fetch_nasdaq_intraday_points(symbol: str) -> Tuple[List[dict], dict]:
    """Nasdaq 公开分时（约 1 分钟点）；x 为「美东钟面」按 UTC 编码，需按 NY 重解释。"""
    url = f"https://api.nasdaq.com/api/quote/{symbol}/chart?assetclass=stocks"
    d = _http_json(
        url,
        timeout=6,
        extra_headers={
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    data = (d or {}).get("data") or {}
    points = []
    for p in data.get("chart") or []:
        y = p.get("y")
        x = p.get("x")
        if y is None or x is None:
            continue
        # 例：4:00 AM ET 存成 04:00 UTC → 直接把日历字段当作美东
        fake = datetime.fromtimestamp(x / 1000.0, timezone.utc)
        dt_ny = fake.replace(tzinfo=NY)
        points.append({"t": int(dt_ny.timestamp()), "c": float(y), "dt_ny": dt_ny, "dt_bj": dt_ny.astimezone(BJ)})
    meta = {
        "symbol": data.get("symbol") or symbol,
        "regularMarketPrice": _parse_money(data.get("lastSalePrice")),
        "previousClose": _parse_money(data.get("previousClose")),
        "timeAsOf": data.get("timeAsOf"),
        "source": "Nasdaq chart",
    }
    return points, meta


def aggregate_points_to_bars(points: List[dict], minutes: int = 15) -> List[dict]:
    """把分时点聚成 N 分钟 OHLC（无量则 v=0）。"""
    if not points:
        return []
    buckets: Dict[int, List[float]] = {}
    bucket_dt: Dict[int, Any] = {}
    step = minutes * 60
    for p in points:
        # 按美东对齐到分钟桶
        dt = p["dt_ny"]
        mins = dt.hour * 60 + dt.minute
        aligned = (mins // minutes) * minutes
        key_dt = dt.replace(hour=aligned // 60, minute=aligned % 60, second=0, microsecond=0)
        key = int(key_dt.timestamp())
        buckets.setdefault(key, []).append(float(p["c"]))
        bucket_dt[key] = key_dt
    rows = []
    for key in sorted(buckets):
        xs = buckets[key]
        dt_ny = bucket_dt[key]
        o, h, l, c = xs[0], max(xs), min(xs), xs[-1]
        rows.append(
            {
                "t": key,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": 0.0,
                "dt_ny": dt_ny,
                "dt_bj": dt_ny.astimezone(BJ),
            }
        )
    return rows


def _merge_bar_rows(primary: List[dict], secondary: List[dict]) -> List[dict]:
    """按 t 合并；primary 覆盖同桶（用于 Nasdaq 今日 + Yahoo 多日）。"""
    by_t: Dict[int, dict] = {}
    for b in secondary:
        by_t[int(b["t"])] = b
    for b in primary:
        by_t[int(b["t"])] = b
    return [by_t[k] for k in sorted(by_t)]


def fetch_bars_intraday(
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    prefer_cache: bool = False,
) -> Tuple[List[dict], dict, str]:
    """1m/5m/15m：优先 Nasdaq 分时聚合，其次 Yahoo，最后本地缓存。
    Nasdaq 常只有今日：若不足 ORB+1 根完整参照日，并入 Yahoo 5d 作结构回退。"""
    iv = normalize_interval(interval)
    mins = INTERVAL_MINUTES[iv]
    yahoo_range = "1d" if iv == "1m" else "5d"
    min_bars = orb_bar_count(iv) + 1
    nq_rows: List[dict] = []
    nq_meta: dict = {}
    # 1) Nasdaq（免 Key，盘中可用）
    try:
        pts, meta = fetch_nasdaq_intraday_points(symbol)
        nq_rows = aggregate_points_to_bars(pts, mins)
        nq_meta = meta
    except Exception:
        pass
    # 2) Yahoo（多日结构；与 Nasdaq 合并）
    y_rows: List[dict] = []
    y_meta: dict = {}
    y_src = ""
    try:
        y_rows, y_meta, y_src = fetch_chart(symbol, yahoo_range, iv, prefer_cache=prefer_cache)
    except Exception:
        if iv == "1m":
            try:
                y_rows, y_meta, y_src = fetch_chart(symbol, "5d", iv, prefer_cache=prefer_cache)
            except Exception:
                pass
    if nq_rows and y_rows:
        merged = _merge_bar_rows(nq_rows, y_rows)
        return merged, {**y_meta, **nq_meta, "merged": "nasdaq+yahoo"}, "nasdaq+yahoo"
    if nq_rows and len(nq_rows) >= max(2, orb_bar_count(iv)):
        # 仅 Nasdaq：若跨日不足 min_bars，仍返回（上层可能观望）
        return nq_rows, nq_meta, "nasdaq"
    if y_rows:
        return y_rows, y_meta, y_src or "yahoo"
    # 3) cache only
    cached = _load_cache(symbol, iv)
    if cached:
        return cached[0], cached[1], "cache"
    raise RuntimeError(f"{symbol} {iv}：Nasdaq/Yahoo/缓存均不可用")


def fetch_bars_15m(symbol: str, prefer_cache: bool = False) -> Tuple[List[dict], dict, str]:
    """兼容旧调用。"""
    return fetch_bars_intraday(symbol, "15m", prefer_cache=prefer_cache)


def resolve_display_price(
    symbol: str,
    dmeta: dict,
    session: dict,
) -> Tuple[float, Optional[float], Optional[float], str, str, Optional[str]]:
    """返回 (现价, 前收, 涨跌幅%, 价格标签, 来源说明, 报价时间原文)。"""
    phase = session.get("phase") or ""
    reg = dmeta.get("regularMarketPrice")
    fullday = dmeta.get("fulldayPrice")
    prev = dmeta.get("previousClose") or dmeta.get("chartPreviousClose")
    nq = fetch_nasdaq_quote(symbol, force=True)

    def _chg(px: float, pclose: Optional[float], hinted: Optional[float] = None) -> Optional[float]:
        if hinted is not None:
            return float(hinted)
        if pclose and pclose != 0 and abs(pclose - px) > 1e-9:
            return (px / pclose - 1.0) * 100.0
        return None

    # 只要 Nasdaq 有价，一律优先（盘前/盘中/盘后），避免卡死在昨收
    if nq and nq.get("price") is not None:
        st = (nq.get("market_status") or "").lower()
        if "pre" in st:
            tag = "盘前"
        elif "after" in st or "post" in st:
            tag = "盘后"
        elif phase in ("盘前", "盘前极早/隔夜"):
            tag = "盘前"
        elif phase == "盘后":
            tag = "盘后"
        elif phase == "常规盘中":
            tag = "盘中"
        else:
            tag = nq.get("market_status") or "报价"
        px = float(nq["price"])
        pclose = nq.get("prev_close")
        if pclose is None and prev is not None:
            pclose = float(prev)
        elif pclose is not None:
            pclose = float(pclose)
        # 开盘后 Nasdaq 自带 percentageChange，优先用
        chg = _chg(px, pclose, nq.get("change_pct"))
        return (
            px,
            pclose,
            chg,
            tag,
            f"Nasdaq {nq.get('market_status') or phase}",
            nq.get("ts"),
        )

    if phase in ("盘前", "盘前极早/隔夜", "盘后"):
        yq = fetch_yahoo_quote_meta(symbol) or {}
        fullday = yq.get("fulldayPrice") or fullday
        prev = yq.get("previousClose") or yq.get("chartPreviousClose") or prev
        if fullday is not None:
            px = float(fullday)
            pclose = float(prev) if prev else None
            return px, pclose, _chg(px, pclose), "盘前/盘后", "Yahoo fulldayPrice", None
        try:
            rows, meta, _ = fetch_chart(symbol, "1d", "1m", prefer_cache=False)
            if rows:
                px = float(rows[-1]["c"])
                pclose = float(prev or meta.get("previousClose") or 0) or None
                return px, pclose, _chg(px, pclose), "盘前分时", "Yahoo 1m", None
        except Exception:
            pass

    if reg is not None:
        px = float(reg)
        pclose = float(prev) if prev else None
        return px, pclose, _chg(px, pclose), "常规收盘", "Yahoo regularMarketPrice", None
    if fullday is not None:
        px = float(fullday)
        pclose = float(prev) if prev else None
        return px, pclose, _chg(px, pclose), "全日价", "Yahoo fulldayPrice", None
    raise RuntimeError("无可用报价")

def atr14(rows: List[dict]) -> Optional[float]:
    if len(rows) < 15:
        return None
    trs = []
    for i in range(1, len(rows)):
        pc = rows[i - 1]["c"]
        tr = max(rows[i]["h"] - rows[i]["l"], abs(rows[i]["h"] - pc), abs(rows[i]["l"] - pc))
        trs.append(tr)
    return sum(trs[-14:]) / 14


def sma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def regular_session(rows: List[dict]) -> List[dict]:
    out = []
    for b in rows:
        if b["dt_ny"].weekday() >= 5:
            continue
        mins = b["dt_ny"].hour * 60 + b["dt_ny"].minute
        if 9 * 60 + 30 <= mins < 16 * 60:
            out.append(b)
    return out


def d_max(E: float, atr: float, high_vol: bool) -> float:
    coef = 0.5 if high_vol else 0.6
    return min(0.07 * E, coef * atr)


def make_t1(E: float, atr_unit: float, side: str, struct: Optional[float]) -> float:
    if side == "short":
        atr_t = E - 0.6 * atr_unit
        if struct is not None and E - 1.0 * atr_unit <= struct <= E - 0.35 * atr_unit:
            T1 = struct
        else:
            T1 = atr_t
        return min(max(T1, E - 1.0 * atr_unit), E - 0.35 * atr_unit)
    atr_t = E + 0.6 * atr_unit
    if struct is not None and E + 0.35 * atr_unit <= struct <= E + 1.0 * atr_unit:
        T1 = struct
    else:
        T1 = atr_t
    return max(min(T1, E + 1.0 * atr_unit), E + 0.35 * atr_unit)


def pick_t1(
    *,
    side: str,
    E: float,
    atr_i: float,
    E_min: float,
    E_max: float,
    day_o: float,
    bars: List[dict],
    px: float,
    in_e: bool,
) -> Tuple[float, str]:
    """止盈优先关键结构，且相对「入场价 E」计算；未到价时禁止把现价当成 T1。"""
    if side == "short":
        lo, hi = E - 1.0 * atr_i, E - 0.35 * atr_i
        # 只收「明显在入场带下方」的结构，避免把当前砸出来的现价低点当计划止盈
        cands: List[Tuple[str, float]] = []
        if lo <= day_o <= hi and day_o < E_min - 1e-6:
            cands.append(("当日开盘", float(day_o)))
        for b in bars[:-1]:  # 不含未完成最后一根
            lv = float(b["l"])
            if lo <= lv <= hi and lv < E_min - 1e-6:
                cands.append(("分时摆动低", lv))
        # 路径低点：须低于入场下沿，且若尚未入场则不能≈现价
        path_l = min(float(b["l"]) for b in bars) if bars else px
        path_l = min(path_l, px)
        if lo <= path_l <= hi and path_l < E_min - 1e-6:
            if in_e or abs(path_l - px) > max(0.001 * px, 0.05 * atr_i):
                cands.append(("日内低点", path_l))
        if cands:
            # 做空：取最靠近 E 的下方结构（价最高者）
            name, lvl = max(cands, key=lambda x: x[1])
            t1 = make_t1(E, atr_i, "short", lvl)
            return t1, name
        t1 = make_t1(E, atr_i, "short", None)
        return t1, "0.6×atr_i（无更近结构）"

    lo, hi = E + 0.35 * atr_i, E + 1.0 * atr_i
    cands = []
    if lo <= day_o <= hi and day_o > E_max + 1e-6:
        cands.append(("当日开盘", float(day_o)))
    for b in bars[:-1]:
        hv = float(b["h"])
        if lo <= hv <= hi and hv > E_max + 1e-6:
            cands.append(("分时摆动高", hv))
    path_h = max(float(b["h"]) for b in bars) if bars else px
    path_h = max(path_h, px)
    if lo <= path_h <= hi and path_h > E_max + 1e-6:
        if in_e or abs(path_h - px) > max(0.001 * px, 0.05 * atr_i):
            cands.append(("日内高点", path_h))
    if cands:
        name, lvl = min(cands, key=lambda x: x[1])
        t1 = make_t1(E, atr_i, "long", lvl)
        return t1, name
    t1 = make_t1(E, atr_i, "long", None)
    return t1, "0.6×atr_i（无更近结构）"


def r1(E: float, S: float, T1: float, side: str) -> Optional[float]:
    risk = abs(E - S)
    reward = abs(T1 - E)
    if risk <= 0:
        return None
    if side == "long" and not (S < E < T1):
        return None
    if side == "short" and not (T1 < E < S):
        return None
    return reward / risk


TARGET_RR = 1.5


def _rr_note(target_rr: float, *, ge: bool = False) -> str:
    s = f"{target_rr:.1f}".rstrip("0").rstrip(".")
    return f"≥{s}:1" if ge else f"盈亏比{s}:1"


def ensure_rr_target(
    *,
    side: str,
    E: float,
    S: float,
    T1: float,
    T1_key: str,
    target_rr: float = TARGET_RR,
) -> Tuple[float, str]:
    """止盈至少做到 target_rr:1（默认≥1.5）；结构位已更远则保留并标注。"""
    risk = abs(E - S)
    if risk <= 1e-12:
        return T1, T1_key
    if side == "long":
        ideal = E + target_rr * risk
        if T1 + 1e-9 < ideal:
            return round(ideal, 4), _rr_note(target_rr)
        cur = r1(E, S, T1, "long")
        note = T1_key or "结构止盈"
        if cur is not None and cur + 1e-9 >= target_rr:
            return T1, f"{note}·{_rr_note(target_rr, ge=True)}"
        return T1, note
    ideal = E - target_rr * risk
    if T1 - 1e-9 > ideal:
        return round(ideal, 4), _rr_note(target_rr)
    cur = r1(E, S, T1, "short")
    note = T1_key or "结构止盈"
    if cur is not None and cur + 1e-9 >= target_rr:
        return T1, f"{note}·{_rr_note(target_rr, ge=True)}"
    return T1, note


def session_status(now_bj: datetime) -> Dict[str, Any]:
    now_ny = now_bj.astimezone(NY)
    wd = now_ny.weekday()
    mins = now_ny.hour * 60 + now_ny.minute
    if wd >= 5:
        phase = "周末休市"
    elif mins < 4 * 60:
        phase = "盘前极早/隔夜"
    elif mins < 9 * 60 + 30:
        phase = "盘前"
    elif mins < 16 * 60:
        phase = "常规盘中"
    elif mins < 20 * 60:
        phase = "盘后"
    else:
        phase = "休市"
    return {
        "beijing": now_bj.strftime("%Y-%m-%d %H:%M:%S"),
        "ny": now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "phase": phase,
        "is_rth": phase == "常规盘中",
    }


def _min_gap(px: float, atr_i: float) -> float:
    """入场与止损的最小间距，避免 NVDA 那种止损贴着入场带。"""
    return max(0.35 * atr_i, 0.005 * px, px * 0.004)


def _ema_series(closes: List[float], period: int) -> List[float]:
    if not closes:
        return []
    k = 2.0 / (period + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(c * k + out[-1] * (1 - k))
    return out


def short_term_trend(bars: List[dict]) -> Dict[str, Any]:
    """
    短线趋势（1m/5m/15m 共用）：EMA5/EMA13 + 近端高低结构 + 价相对快线。
    返回 bias: up | down | flat。
    """
    closes = [float(b["c"]) for b in bars if b.get("c") is not None]
    highs = [float(b["h"]) for b in bars if b.get("h") is not None]
    lows = [float(b["l"]) for b in bars if b.get("l") is not None]
    if len(closes) < 5:
        return {"bias": "flat", "ema5": None, "ema13": None, "score": 0, "label": "样本不足"}

    ema5 = _ema_series(closes, 5)
    ema13 = _ema_series(closes, 13 if len(closes) >= 8 else max(5, len(closes) // 2))
    e5, e13 = ema5[-1], ema13[-1]
    e5_prev = ema5[-4] if len(ema5) >= 4 else ema5[0]
    px = closes[-1]

    score = 0
    # 1) 快慢线位置
    if e5 > e13 * 1.0005:
        score += 1
    elif e5 < e13 * 0.9995:
        score -= 1
    # 2) 快线斜率
    if e5 > e5_prev * 1.0003:
        score += 1
    elif e5 < e5_prev * 0.9997:
        score -= 1
    # 3) 价在快线上下
    if px > e5:
        score += 1
    elif px < e5:
        score -= 1
    # 4) 近 4 根结构：抬高低点 / 降低高点
    if len(lows) >= 4 and len(highs) >= 4:
        hl = lows[-1] > lows[-3] and lows[-2] >= lows[-4] * 0.999
        lh = highs[-1] < highs[-3] and highs[-2] <= highs[-4] * 1.001
        if hl and not lh:
            score += 1
        elif lh and not hl:
            score -= 1

    # |score|≥2 认定趋势（兼顾笔数与顺势）
    if score >= 2:
        bias = "up"
        label = "短期偏多"
    elif score <= -2:
        bias = "down"
        label = "短期偏空"
    else:
        bias = "flat"
        label = "短期震荡"
    return {
        "bias": bias,
        "ema5": round(e5, 4),
        "ema13": round(e13, 4),
        "score": score,
        "label": label,
    }


def _strength_label(n: int) -> str:
    return {1: "弱", 2: "偏弱", 3: "中", 4: "偏强", 5: "强"}.get(n, "中")


def _risk_label(pct: Optional[float]) -> str:
    if pct is None:
        return "—"
    if pct < 1.0:
        return "低"
    if pct < 2.5:
        return "中"
    return "高"


def _score_strength(
    *,
    sc: int,
    side: str,
    px: float,
    orb_l: float,
    orb_h: float,
    R: Optional[float],
) -> int:
    """强度 1–5：|score|、是否站稳 ORB 一侧、R1。"""
    n = 1
    abs_sc = abs(sc)
    if abs_sc >= 2:
        n += 1
    if abs_sc >= 3:
        n += 1
    if side == "short" and px < orb_l:
        n += 1
    elif side == "long" and px > orb_h:
        n += 1
    elif side == "short" and px < (orb_l + orb_h) / 2:
        n += 0
    if R is not None and R >= TARGET_RR:
        n += 1
    elif R is not None and R >= 1.2:
        n += 1
    return max(1, min(5, n))


def remember_advice(symbol: str, advice: str, now_bj: str) -> Dict[str, Any]:
    """进程内记录建议变化；同向不刷新本次时间。"""
    sym = symbol.strip().upper()
    prev = _SIGNAL_MEM.get(sym)
    if prev and prev.get("advice") == advice:
        return {
            "signal_since_bj": prev["since_bj"],
            "last_signal_advice": None,
            "last_signal_bj": None,
            "last_signal": "本次",
        }
    out = {
        "signal_since_bj": now_bj,
        "last_signal_advice": prev.get("advice") if prev else None,
        "last_signal_bj": prev.get("since_bj") if prev else None,
        "last_signal": (
            f"{prev['advice']} · {prev['since_bj']}" if prev else "本次"
        ),
    }
    _SIGNAL_MEM[sym] = {"advice": advice, "since_bj": now_bj}
    return out


def compute_intraday_signal(
    *,
    px: float,
    atr: float,
    orb_l: float,
    orb_h: float,
    vwap: float,
    day_o: float,
    path_l: float,
    path_h: float,
    bars: List[dict],
    daily_bias: str = "—",
) -> Dict[str, Any]:
    """
    交易卡片口径：先定趋势 → 建议做多/做空 → 回踩/反抽入场 → 参考价/止损/止盈。

    1) 趋势：短线 score + VWAP/ORB；冲高回落（破上沿后跌回 VWAP 下）可定下跌
    2) 下跌→建议做空；上涨→建议做多；震荡→观望
    3) 入场带锚定 ORB 上/下半区（反抽空/回踩多），禁止 VWAP 下移后的杀跌到价、禁止追涨
    4) 到价可执行；盈亏比默认 ≥1.5；平多/平空：ORB 对侧失效
    """
    atr_i = max(atr * 0.35, px * 0.008)
    gap = _min_gap(px, atr_i)
    buf = min(0.15 * atr, 0.015 * max(orb_h, orb_l, px))
    chase_buf = max(0.25 * atr_i, 0.004 * px)
    st_trend = short_term_trend(bars)
    sc = int(st_trend.get("score") or 0)
    trend_tag = st_trend.get("label") or "短期—"
    daily_tag = daily_bias if daily_bias and daily_bias != "—" else ""

    broke_down = path_l < orb_l - 1e-9
    broke_up = path_h > orb_h + 1e-9
    orb_mid = (orb_l + orb_h) / 2.0
    orb_span = max(orb_h - orb_l, gap)
    below_mid = px <= min(vwap, orb_mid)
    above_mid = px >= max(vwap, orb_mid)

    # —— 先趋势（SNDK 回放验证：不过度放宽冲高转弱）——
    if broke_up and px < vwap and sc <= 0:
        trend, structure = "下跌", "空头"
    elif broke_down and px > vwap and sc >= 0:
        trend, structure = "上涨", "多头"
    elif sc <= -2 and below_mid:
        trend, structure = "下跌", "空头"
    elif sc >= 2 and above_mid:
        trend, structure = "上涨", "多头"
    else:
        trend, structure = "震荡", "震荡"

    short_invalid = bool(broke_down and px > orb_h)
    long_invalid = bool(broke_up and px < orb_l)

    advice = "观望"
    signal = "观望"
    reason = ""
    setup = "震荡观察"
    side = None
    S = S_key = None
    T1 = T1_key = None
    E_band = None
    R1v = None
    stress = None
    entry_ready = False
    entry_label = None
    at_entry = False
    ref_price = None
    stop_note = None
    strength = 1
    strength_label = "弱"
    risk_pct = None
    risk_label = "—"

    if trend == "下跌":
        advice = "做空"
        signal = "做空"
        setup = "趋势空·反抽"
        side = "short"
        entry_label = "做空入场价"
        S = orb_h + buf
        stop_note = f"ORB上沿 {orb_h:.2f} + 缓冲"
        S_key = stop_note
        # 反抽带锚定 ORB 上半区（不随砸盘 VWAP 下移变成杀跌带）
        band_hi = float(orb_h - gap)
        band_lo = float(max(orb_mid, orb_h - 0.40 * orb_span))
        if vwap >= orb_mid:
            band_lo = max(band_lo, min(float(vwap), band_hi - gap))
        if band_hi < band_lo:
            band_lo, band_hi = orb_mid, orb_h - gap
        S = max(S, band_hi + gap)
        E_band = [round(band_lo, 4), round(band_hi, 4)]
        E = (band_lo + band_hi) / 2.0
        near_low = px <= path_l + chase_buf or px <= orb_l + chase_buf
        in_upper = px >= orb_mid - 1e-9
        at_entry = band_lo <= px <= band_hi and not near_low and in_upper
        chasing = px < band_lo or not in_upper
        T1, T1_key = pick_t1(
            side="short",
            E=E,
            atr_i=atr_i,
            E_min=band_lo,
            E_max=band_hi,
            day_o=day_o,
            bars=bars,
            px=px,
            in_e=at_entry,
        )
        ref_price = round(px if at_entry else E, 4)
        T1, T1_key = ensure_rr_target(
            side="short", E=float(ref_price), S=float(S), T1=float(T1), T1_key=T1_key
        )
        R1v = r1(ref_price, S, T1, "short")
        stress = 5 * abs(ref_price - S) / ref_price * 100 if ref_price else None
        risk_pct = abs(ref_price - S) / ref_price * 100 if ref_price else None
        strength = _score_strength(
            sc=sc, side="short", px=px, orb_l=orb_l, orb_h=orb_h, R=R1v
        )
        if daily_tag == "日线偏多":
            strength = max(1, strength - 1)
        strength_label = _strength_label(strength)
        risk_label = _risk_label(risk_pct)
        entry_ready = at_entry
        r_note = (
            f"；盈亏比{R1v:.2f}:1"
            if R1v is not None
            else ""
        )
        if near_low or (not in_upper and px < band_lo):
            reason = (
                f"下跌趋势，不杀跌；等反抽至ORB上半区[{band_lo:.2f},{band_hi:.2f}]{r_note}"
            )
            entry_ready = False
            at_entry = False
        elif at_entry:
            reason = f"下跌趋势·反抽到价，可按点位做空（{trend_tag}{('·'+daily_tag) if daily_tag else ''}）{r_note}"
        elif chasing:
            reason = f"下跌趋势，未到反抽带；等[{band_lo:.2f},{band_hi:.2f}]{r_note}"
        else:
            reason = f"下跌趋势，等反抽入场带[{band_lo:.2f},{band_hi:.2f}]（{trend_tag}）{r_note}"

    elif trend == "上涨":
        advice = "做多"
        signal = "做多"
        setup = "趋势多·回踩"
        side = "long"
        entry_label = "做多入场价"
        S = orb_l - buf
        stop_note = f"ORB下沿 {orb_l:.2f} − 缓冲"
        S_key = stop_note
        # 回踩带锚定 ORB 下半区（不追涨）
        band_lo = float(orb_l + chase_buf)
        band_hi = float(min(orb_mid, orb_l + 0.40 * orb_span))
        if vwap <= orb_mid:
            band_hi = min(band_hi, max(float(vwap), band_lo + gap))
        if band_hi < band_lo:
            band_lo, band_hi = orb_l + chase_buf, orb_mid
        band_lo = max(band_lo, S + gap)
        if band_hi < band_lo:
            band_hi = band_lo + gap * 0.5
        E_band = [round(band_lo, 4), round(band_hi, 4)]
        E = (band_lo + band_hi) / 2.0
        near_high = px >= path_h - chase_buf or px >= orb_h - chase_buf
        in_lower = px <= orb_mid + 1e-9
        at_entry = band_lo <= px <= band_hi and not near_high and in_lower
        chasing = px > band_hi or not in_lower
        T1, T1_key = pick_t1(
            side="long",
            E=E,
            atr_i=atr_i,
            E_min=band_lo,
            E_max=band_hi,
            day_o=day_o,
            bars=bars,
            px=px,
            in_e=at_entry,
        )
        ref_price = round(px if at_entry else E, 4)
        T1, T1_key = ensure_rr_target(
            side="long", E=float(ref_price), S=float(S), T1=float(T1), T1_key=T1_key
        )
        R1v = r1(ref_price, S, T1, "long")
        stress = 5 * abs(ref_price - S) / ref_price * 100 if ref_price else None
        risk_pct = abs(ref_price - S) / ref_price * 100 if ref_price else None
        strength = _score_strength(
            sc=sc, side="long", px=px, orb_l=orb_l, orb_h=orb_h, R=R1v
        )
        if daily_tag == "日线偏空":
            strength = max(1, strength - 1)
        strength_label = _strength_label(strength)
        risk_label = _risk_label(risk_pct)
        entry_ready = at_entry
        r_note = (
            f"；盈亏比{R1v:.2f}:1"
            if R1v is not None
            else ""
        )
        if near_high or (not in_lower and px > band_hi):
            reason = (
                f"上涨趋势，不追涨；等回踩至ORB下半区[{band_lo:.2f},{band_hi:.2f}]{r_note}"
            )
            entry_ready = False
            at_entry = False
        elif at_entry:
            reason = f"上涨趋势·回踩到价，可按点位做多（{trend_tag}{('·'+daily_tag) if daily_tag else ''}）{r_note}"
        elif chasing:
            reason = f"上涨趋势，未到回踩带；等[{band_lo:.2f},{band_hi:.2f}]{r_note}"
        else:
            reason = f"上涨趋势，等回踩入场带[{band_lo:.2f},{band_hi:.2f}]（{trend_tag}）{r_note}"

    else:
        stop_note = "待趋势明确后定"
        target_note = None
        reason = f"震荡（{trend_tag}），暂无开仓建议；等短线|score|≥2 且站稳 VWAP/ORB 一侧"
        if broke_up and px <= vwap:
            reason = (
                f"曾破ORB上沿后跌回VWAP下→震荡（{trend_tag}），等方向再定"
            )
        elif broke_down and px >= vwap:
            reason = (
                f"曾破ORB下沿后抽回VWAP上→震荡（{trend_tag}），等方向再定"
            )

    # ORB 对侧失效 → 平仓提示（覆盖观望，不覆盖同向开仓建议）
    if signal not in ("做多", "做空") and short_invalid:
        advice = signal = "平空"
        entry_ready = False
        at_entry = False
        side = "cover_short"
        entry_label = "空单失效·先平"
        setup = "ORB失效平空"
        S = round(orb_h + buf, 4)
        stop_note = S_key = f"ORB上沿 {orb_h:.2f}"
        ref_price = round(px, 4)
        reason = f"空头结构失效：价已站上ORB上沿 {orb_h:.2f}，先平空。" + (reason or "")
    elif signal not in ("做多", "做空") and long_invalid:
        advice = signal = "平多"
        entry_ready = False
        at_entry = False
        side = "cover_long"
        entry_label = "多单失效·先平"
        setup = "ORB失效平多"
        S = round(orb_l - buf, 4)
        stop_note = S_key = f"ORB下沿 {orb_l:.2f}"
        ref_price = round(px, 4)
        reason = f"多头结构失效：价已跌破ORB下沿 {orb_l:.2f}，先平多。" + (reason or "")

    entry_low = E_band[0] if E_band else None
    entry_high = E_band[1] if E_band else None
    entry_mid = (
        round((entry_low + entry_high) / 2, 4)
        if entry_low is not None and entry_high is not None
        else None
    )
    if ref_price is None and entry_mid is not None:
        ref_price = entry_mid

    stop = round(S, 4) if S is not None else None
    target = round(T1, 4) if T1 is not None else None
    if stop is not None and not stop_note:
        stop_note = S_key
    if target is not None:
        target_note = T1_key
    else:
        target_note = None

    actionable = signal in ("做多", "做空") and entry_ready
    if signal in ("平空", "平多"):
        action_label = "先平仓"
    elif actionable:
        action_label = "可按点位执行"
    elif signal in ("做多", "做空"):
        action_label = "等回踩/反抽"
    else:
        action_label = "当前观望"

    return {
        "trend": trend,
        "advice": advice,
        "signal": signal,
        "reason": reason,
        "structure": structure,
        "setup": setup,
        "actionable": actionable,
        "action_label": action_label,
        "strength": strength if signal in ("做多", "做空") else None,
        "strength_label": strength_label if signal in ("做多", "做空") else None,
        "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
        "risk_label": risk_label if risk_pct is not None else None,
        "ref_price": ref_price,
        "stop": stop,
        "stop_note": stop_note if stop is not None else None,
        "target": target,
        "target_note": target_note if target is not None else None,
        "at_entry": at_entry,
        "S": stop,
        "S_key": S_key,
        "E_band": E_band,
        "entry_label": entry_label,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_mid": entry_mid,
        "entry_ready": entry_ready,
        "T1": target,
        "T1_key": T1_key,
        "R1": round(R1v, 2) if R1v is not None else None,
        "stress_5x_pct": round(stress, 2) if stress is not None else None,
        "atr_i": round(atr_i, 4),
        "side": side,
        "short_invalid": short_invalid,
        "long_invalid": long_invalid,
        "broke_up": broke_up,
        "broke_down": broke_down,
        "short_trend": st_trend,
        "orb": {"low": round(orb_l, 4), "high": round(orb_h, 4)},
        "vwap": round(vwap, 4),
        "watch_only": not actionable and signal not in ("平空", "平多"),
    }


def analyze_symbol(symbol: str, interval: str = DEFAULT_INTERVAL) -> Dict[str, Any]:
    import os

    iv = normalize_interval(interval)
    orb_n = orb_bar_count(iv)
    min_bars = orb_n + 1
    now_bj = datetime.now(BJ)
    sess_info = session_status(now_bj)
    force_cache = os.environ.get("SIGNAL_CACHE", "").strip() in ("1", "true", "yes")
    prefer_daily_cache = force_cache or os.environ.get("SIGNAL_LIVE", "").strip() not in (
        "1",
        "true",
        "yes",
    )
    daily: List[dict] = []
    dmeta: dict = {}
    src_d = "none"
    try:
        daily, dmeta, src_d = fetch_chart(
            symbol, "3mo", "1d", prefer_cache=prefer_daily_cache
        )
    except Exception:
        daily, dmeta, src_d = [], {}, "yahoo_fail"

    bars_iv, meta_iv, src_m = fetch_bars_intraday(
        symbol, iv, prefer_cache=force_cache or prefer_daily_cache
    )
    if src_m == "nasdaq" and meta_iv.get("previousClose") and not dmeta.get("previousClose"):
        dmeta = {**dmeta, "previousClose": meta_iv["previousClose"]}
    if meta_iv.get("regularMarketPrice") and not dmeta.get("regularMarketPrice"):
        dmeta = {**dmeta, "regularMarketPrice": meta_iv["regularMarketPrice"]}

    atr = atr14(daily) if daily else None
    closes = [x["c"] for x in daily] if daily else []
    ma20 = sma(closes, 20) if closes else None
    last = daily[-1] if daily else None
    # Yahoo 日线不可用时：用分时振幅粗估 ATR，保证票池仍可扫
    if atr is None and bars_iv:
        sess_tmp = regular_session(bars_iv)
        sample = sess_tmp[-24:] if len(sess_tmp) >= 5 else sess_tmp
        if sample:
            hs = [float(b["h"]) for b in sample]
            ls = [float(b["l"]) for b in sample]
            px0 = float(sample[-1]["c"])
            atr = max(max(hs) - min(ls), px0 * 0.02)

    try:
        price, prev, chg_pct, price_tag, price_src, quote_ts = resolve_display_price(
            symbol, dmeta, sess_info
        )
    except Exception:
        nq = fetch_nasdaq_quote(symbol) or {}
        price = float(
            nq.get("price")
            or dmeta.get("regularMarketPrice")
            or meta_iv.get("regularMarketPrice")
            or (last["c"] if last else (bars_iv[-1]["c"] if bars_iv else 0))
        )
        prev = nq.get("prev_close") or dmeta.get("previousClose") or dmeta.get("chartPreviousClose")
        if prev is None and len(daily) > 1:
            prev = daily[-2]["c"]
        prev = float(prev) if prev is not None else None
        chg_pct = ((price / prev - 1) * 100) if prev and abs(prev - price) > 1e-9 else None
        price_tag, price_src, quote_ts = "回退", "nasdaq/cache", None

    if prev is None:
        prev = dmeta.get("previousClose") or dmeta.get("chartPreviousClose")
        if prev is None and len(daily) > 1:
            prev = daily[-2]["c"]
        prev = float(prev) if prev is not None else None
    if prev is not None and abs(float(prev) - float(price)) < 1e-9:
        if len(daily) > 1 and abs(daily[-2]["c"] - float(price)) > 1e-9:
            prev = float(daily[-2]["c"])
        else:
            prev = None
    if chg_pct is None and prev:
        chg_pct = (float(price) / float(prev) - 1) * 100

    high_vol = bool(atr and price and atr / price > 0.06)
    sess = regular_session(bars_iv)
    by_day: Dict[Any, List[dict]] = {}
    for b in sess:
        by_day.setdefault(b["dt_ny"].date(), []).append(b)

    # 只用今日 RTH 算入场点；未满 ORB+1 根绝不借用旧日结构
    ny_date = now_bj.astimezone(NY).date()
    today_n = len(by_day.get(ny_date, []))
    today_bars = by_day.get(ny_date, [])
    struct_scope = f"今日RTH·{iv}"
    forming = today_n < min_bars
    if forming:
        if sess_info.get("is_rth"):
            struct_scope = f"趋势形成中·今日仅{today_n}根<{min_bars}"
        elif sess_info.get("phase") and "盘前" in str(sess_info.get("phase")):
            struct_scope = f"盘前·等待今日ORB（已有{today_n}根）"
        else:
            struct_scope = f"非完整今日RTH·{today_n}根"

    live_quote = "Nasdaq" in price_src or "fullday" in price_src or "1m" in price_src
    provenance = "live" if live_quote else ("live" if src_d == "live" else "cache")
    src_label = f"{price_src} · {price_tag} · {iv}/{src_m}"
    quote_time_bj = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    base = {
        "symbol": symbol,
        "name": dmeta.get("longName") or dmeta.get("shortName") or symbol,
        "price": round(price, 4),
        "prev_close": round(prev, 4) if prev else None,
        "change_pct": round(chg_pct, 2) if chg_pct is not None else None,
        "price_tag": price_tag,
        "interval": iv,
        "orb_bars": orb_n,
        "atr14": round(atr, 4) if atr else None,
        "ma20": round(ma20, 4) if ma20 else None,
        "high_vol": high_vol,
        "source": src_label,
        "data_mode": provenance,
        "quote_time_bj": quote_time_bj,
        "quote_ts": quote_ts,
        "struct_scope": struct_scope,
        "session": sess_info,
    }

    if not atr or forming or not today_bars:
        mem = remember_advice(symbol, "观望", quote_time_bj)
        return {
            **base,
            **mem,
            "trend": "震荡",
            "advice": "观望",
            "signal": "观望",
            "reason": (
                f"趋势形成中：今日 {iv} 需至少 {min_bars} 根才给定入场/止损"
                if forming or not today_bars
                else f"{iv} ATR 不足"
            ),
            "action_label": "趋势形成中",
            "actionable": False,
            "strength": None,
            "strength_label": None,
            "risk_pct": None,
            "risk_label": None,
            "ref_price": round(price, 4) if price else None,
            "stop": None,
            "stop_note": None,
            "target": None,
            "target_note": None,
            "at_entry": False,
            "S": None,
            "S_key": None,
            "E_band": None,
            "T1": None,
            "R1": None,
            "structure": "未定",
            "orb": None,
            "vwap": None,
            "watch_only": True,
        }

    orb_bars = today_bars[:orb_n]
    orb_h = max(x["h"] for x in orb_bars)
    orb_l = min(x["l"] for x in orb_bars)
    day_o = today_bars[0]["o"]
    num = den = 0.0
    for b in today_bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        if b["v"] > 0:
            num += tp * b["v"]
            den += b["v"]
    vwap = num / den if den else today_bars[-1]["c"]
    last_b = today_bars[-1]
    px = float(price)
    sess_l = min(x["l"] for x in today_bars)
    sess_h = max(x["h"] for x in today_bars)
    # 分时聚合常漏尖峰；用 Nasdaq 日高/日低校正路径极值
    nq_ext = fetch_nasdaq_quote(symbol) or {}
    day_hi = nq_ext.get("day_high")
    day_lo = nq_ext.get("day_low")
    path_l = min(sess_l, px, day_lo if day_lo is not None else px)
    path_h = max(sess_h, px, day_hi if day_hi is not None else px)

    daily_bias = "—"
    if ma20:
        daily_bias = "日线偏空" if price < ma20 else "日线偏多"

    sig = compute_intraday_signal(
        px=px,
        atr=float(atr),
        orb_l=float(orb_l),
        orb_h=float(orb_h),
        vwap=float(vwap),
        day_o=float(day_o),
        path_l=float(path_l),
        path_h=float(path_h),
        bars=today_bars,
        daily_bias=daily_bias,
    )

    reason = sig["reason"]
    if reason:
        reason = f"[{iv}] {reason}"

    advice = sig.get("advice") or sig.get("signal") or "观望"
    mem = remember_advice(symbol, advice, quote_time_bj)

    return {
        **base,
        **sig,
        **mem,
        "reason": reason,
        "daily_bias": daily_bias,
        "bar_time_bj": last_b["dt_bj"].strftime("%Y-%m-%d %H:%M"),
        "day_high": round(path_h, 4),
        "day_low": round(path_l, 4),
        "broke_up": path_h > orb_h + 1e-9,
        "broke_down": path_l < orb_l - 1e-9,
    }


def _signal_bias(item: Dict[str, Any]) -> str:
    """多周期一致性用：做多/空优先，否则看结构。"""
    sig = item.get("signal") or ""
    if sig == "做多":
        return "long"
    if sig == "做空":
        return "short"
    st = item.get("structure") or ""
    if "多" in st:
        return "long"
    if "空" in st:
        return "short"
    return "flat"


def _consensus(tf_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """一致性优先看 15m+5m；1m 仅参考确认，不单独定调。"""
    biases = {iv: _signal_bias(tf_map.get(iv) or {}) for iv in INTERVALS}
    b15, b5, b1 = biases.get("15m", "flat"), biases.get("5m", "flat"), biases.get("1m", "flat")
    if b15 == b5 == "long":
        label = "15+5 多头一致" + (" ·1m同向" if b1 == "long" else (" ·1m未跟" if b1 == "short" else ""))
        bias = "long"
    elif b15 == b5 == "short":
        label = "15+5 空头一致" + (" ·1m同向" if b1 == "short" else (" ·1m未跟" if b1 == "long" else ""))
        bias = "short"
    elif b15 in ("long", "short") and b5 in ("long", "short") and b15 != b5:
        label, bias = "15/5 分歧", "mixed"
    elif b15 in ("long", "short") and b5 == "flat":
        label, bias = f"仅15m{'多' if b15 == 'long' else '空'}", b15
    elif b5 in ("long", "short") and b15 == "flat":
        label, bias = f"仅5m{'多' if b5 == 'long' else '空'}", b5
    else:
        label, bias = "未对齐/观望", "flat"
    return {
        "bias": bias,
        "label": label.strip(),
        "by_interval": biases,
        "priority": ("15m", "5m"),
        "confirm": "1m",
    }


def analyze_many(
    symbols: Optional[List[str]] = None,
    interval: str = DEFAULT_INTERVAL,
    multi: bool = False,
) -> Dict[str, Any]:
    """仅 15m 策略；multi 参数保留兼容，一律不跑 1m/5m。"""
    symbols = symbols or DEFAULT_SYMBOLS
    iv = normalize_interval(interval)
    now_bj = datetime.now(BJ)
    items = []
    errors = []
    for s in symbols:
        sym = s.strip().upper()
        try:
            items.append(analyze_symbol(sym, interval=iv))
        except Exception as e:
            errors.append({"symbol": s, "error": str(e)})
            items.append(
                {
                    "symbol": sym,
                    "signal": "观望",
                    "reason": f"取行情失败：{e}",
                    "price": None,
                    "interval": iv,
                    "error": True,
                }
            )
    return {
        "generated_bj": now_bj.strftime("%Y-%m-%d %H:%M:%S"),
        "interval": iv,
        "multi": False,
        "session": session_status(now_bj),
        "source_note": "【美股】15m 趋势→回踩/反抽入场→参考价/止损/止盈。",
        "rules_note": "下跌做空/上涨做多；ORB上半区反抽空、下半区回踩多；≥1.5R；禁杀跌追涨；非AI",
        "items": items,
        "errors": errors,
    }


if __name__ == "__main__":
    print(json.dumps(analyze_many(["SNDK"]), ensure_ascii=False, indent=2))
