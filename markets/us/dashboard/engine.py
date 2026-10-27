#!/usr/bin/env python3
"""美股盘中信号引擎（勿用于 A股）：关键位 S → E → T1，5x 压测；时间北京。"""

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
# 短线周期：1m / 5m / 15m（ORB 统一按开盘约 30 分钟）
INTERVALS = ("1m", "5m", "15m")
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15}
ORB_WINDOW_MINUTES = 30
DEFAULT_INTERVAL = "15m"


def normalize_interval(interval: Optional[str]) -> str:
    iv = (interval or DEFAULT_INTERVAL).strip().lower()
    if iv in ("1", "1min", "1m"):
        return "1m"
    if iv in ("5", "5min", "5m"):
        return "5m"
    if iv in ("15", "15min", "15m"):
        return "15m"
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
    out = {
        "price": px,
        "prev_close": prev,
        "net_change": net,
        "market_status": status,
        "ts": primary.get("lastTradeTimestamp") or (secondary or {}).get("lastTradeTimestamp"),
        "change_pct": chg,
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


def fetch_bars_intraday(
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    prefer_cache: bool = False,
) -> Tuple[List[dict], dict, str]:
    """1m/5m/15m：优先 Nasdaq 分时聚合，其次 Yahoo，最后本地缓存。"""
    iv = normalize_interval(interval)
    mins = INTERVAL_MINUTES[iv]
    yahoo_range = "1d" if iv == "1m" else "5d"
    # 1) Nasdaq（免 Key，盘中可用）
    try:
        pts, meta = fetch_nasdaq_intraday_points(symbol)
        rows = aggregate_points_to_bars(pts, mins)
        need = max(2, orb_bar_count(iv))
        if len(rows) >= need:
            return rows, meta, "nasdaq"
    except Exception:
        pass
    # 2) Yahoo
    try:
        return fetch_chart(symbol, yahoo_range, iv, prefer_cache=prefer_cache)
    except Exception:
        if iv == "1m":
            try:
                return fetch_chart(symbol, "5d", iv, prefer_cache=prefer_cache)
            except Exception:
                pass
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


def _risk_ok(E: float, S: float, T1: float, side: str) -> Tuple[Optional[float], Optional[float], bool, str]:
    """新开条件：价序正确且 R1≥0.8（目标更远）。5x 仅作参考，不再作为硬闸。"""
    R = r1(E, S, T1, side)
    st = 5 * abs(E - S) / E * 100 if E else None
    notes = []
    if R is not None and R < 0.8:
        notes.append(f"R1={R:.2f}<0.8")
    if st is not None and st >= 35:
        notes.append(f"参考5x≈{st:.1f}%（非硬闸）")
    ok = R is not None and R >= 0.8
    if side == "long" and not (S < E < T1):
        ok = False
        notes.append("价序S<E<T1不成立")
    if side == "short" and not (T1 < E < S):
        ok = False
        notes.append("价序T1<E<S不成立")
    return R, st, ok, ("；".join(notes) if notes else "")


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
) -> Dict[str, Any]:
    """
    ORB + VWAP + 短期趋势（1m/5m/15m 同一套逻辑）：
    1) 短线趋势定可否开仓（只做顺势）
    2) VWAP 定偏向；ORB 同向突破为主；趋势强时可做回踩/反抽
    3) 止损在 ORB 对侧；平仓看 ORB 失效
    4) 5x 仅参考，非开仓硬闸
    """
    atr_i = max(atr * 0.35, px * 0.008)
    gap = _min_gap(px, atr_i)
    buf = min(0.12 * atr, 0.012 * px, 0.55 * atr_i)
    orb_mid = (orb_l + orb_h) / 2
    orb_w = max(orb_h - orb_l, gap)
    st_trend = short_term_trend(bars)
    trend_up = st_trend["bias"] == "up"
    trend_down = st_trend["bias"] == "down"
    trend_flat = st_trend["bias"] == "flat"

    broke_down = path_l < orb_l - 1e-9
    broke_up = path_h > orb_h + 1e-9
    drive_down = (day_o - path_l) >= 0.25 * atr_i and path_l <= min(day_o, orb_mid)
    drive_up = (path_h - day_o) >= 0.25 * atr_i and path_h >= max(day_o, orb_mid)
    bearish = broke_down or drive_down
    bullish = broke_up or drive_up
    above_vwap = px > vwap
    below_vwap = px < vwap

    if trend_up and above_vwap:
        structure = "多头"
    elif trend_down and below_vwap:
        structure = "空头"
    elif above_vwap and (broke_up or px > orb_h):
        structure = "多头"
    elif below_vwap and (broke_down or px < orb_l):
        structure = "空头"
    elif above_vwap:
        structure = "偏多"
    elif below_vwap:
        structure = "偏空"
    else:
        structure = "震荡"

    # 结构级失效：空单要收复 ORB 上沿；多单要跌破 ORB 下沿（防 VWAP 毛刺打平后踏空）
    short_invalid = bool(bearish and px > orb_h)
    long_invalid = bool(bullish and px < orb_l)

    signal = "观望"
    reason = f"无合格 setup（{st_trend['label']}）：等短期趋势清晰后的 ORB 同向突破/回踩"
    S = S_key = None
    E_band = [round(orb_l, 4), round(orb_h, 4)]
    T1 = T1_key = None
    R1v = stress = None
    side = None
    entry_ready = False
    setup = None

    def _accept(
        long: bool,
        E_lo: float,
        E_hi: float,
        S_: float,
        Sk: str,
        T1_: float,
        Tk: str,
        why: str,
        st_label: str,
        setup_name: str,
    ) -> bool:
        nonlocal signal, reason, S, S_key, E_band, T1, T1_key, R1v, stress, side, entry_ready, structure, setup
        # 短线趋势闸（对称）：多 score≥2，空 score≤-2；胜率可降，优先跟趋势
        sc = st_trend["score"]
        ok_trend = (sc >= 2) if long else (sc <= -2)
        if not ok_trend:
            signal = "观望"
            setup = setup_name
            structure = st_label
            E_band = [round(E_lo, 4), round(E_hi, 4)]
            S, S_key, T1, T1_key = S_, Sk, T1_, Tk
            side = "long" if long else "short"
            reason = f"{why}；但{st_trend['label']} score={sc}，需与方向同向(|score|≥2)，不开"
            entry_ready = False
            return False
        if E_hi < E_lo:
            E_lo, E_hi = E_hi, E_lo
        # 止损须在带外且距离足够
        if long:
            S_ = min(S_, E_lo - gap * 0.5)
        else:
            S_ = max(S_, E_hi + gap * 0.5)
        E = min(max(px, E_lo), E_hi)
        side_ = "long" if long else "short"
        # 波段目标拉远：单笔吃更大空间（约 1.2~3.0×atr_i）
        if long:
            T1_ = max(T1_, E + 1.2 * atr_i)
            T1_ = min(T1_, E + 3.0 * atr_i)
        else:
            T1_ = min(T1_, E - 1.2 * atr_i)
            T1_ = max(T1_, E - 3.0 * atr_i)
        R, st, ok, note = _risk_ok(E, S_, T1_, side_)
        E_band = [round(E_lo, 4), round(E_hi, 4)]
        S, S_key, T1, T1_key, R1v, stress = S_, Sk, T1_, Tk, R, st
        structure = st_label
        setup = setup_name
        side = side_
        in_band = E_lo <= px <= E_hi
        trend_tag = st_trend["label"]
        if not in_band:
            signal = "观望"
            where = "未到入场带" if ((long and px < E_lo) or ((not long) and px > E_hi)) else "已离开入场带"
            risk = f"；另：{note}" if note else ""
            reason = f"{why}（{trend_tag}）；{where}[{E_lo:.2f},{E_hi:.2f}]（勿追）S={S_:.2f} T1={T1_:.2f}{risk}"
            return False
        if ok:
            signal = "做多" if long else "做空"
            entry_ready = True
            reason = f"{why}（{trend_tag}）；入场[{E_lo:.2f},{E_hi:.2f}] S={S_:.2f} T1={T1_:.2f}"
            return True
        signal = "观望"
        reason = f"{why}（{trend_tag}）；在带内但风控未过（{note}），不开仓"
        return False

    # A1. 短线偏多 → ORB 上破开多
    if setup is None and above_vwap and broke_up and px >= orb_h and px <= orb_h + 0.75 * atr_i:
        S_ = orb_l - buf
        E_lo = max(orb_h - 0.15 * orb_w, min(vwap, orb_h))
        E_hi = orb_h + 0.55 * atr_i
        T1_ = orb_h + max(1.2 * orb_w, 2.0 * atr_i)
        _accept(True, E_lo, E_hi, S_, f"ORB下沿失效 {orb_l:.2f}", T1_, "ORB量度",
                "短线偏多·ORB上破", "多头", "ORB突破多")

    # A2. 短线偏空 → ORB 下破开空
    if setup is None and below_vwap and broke_down and px <= orb_l and px >= orb_l - 0.75 * atr_i:
        S_ = orb_h + buf
        E_hi = min(vwap, orb_l + 0.2 * orb_w)
        E_lo = orb_l - 0.55 * atr_i
        T1_ = orb_l - max(1.2 * orb_w, 2.0 * atr_i)
        _accept(False, E_lo, E_hi, S_, f"ORB上沿失效 {orb_h:.2f}", T1_, "ORB量度/前低",
                "短线偏空·ORB下破", "空头", "ORB突破空")

    # B. 趋势续作：|score|≥3 才回踩/反抽（突破用 ≥2；续作更严一点）
    strong_up = trend_up and st_trend["score"] >= 3
    strong_down = trend_down and st_trend["score"] <= -3
    if setup is None and strong_up and above_vwap and bullish and vwap <= px <= orb_h + 0.3 * atr_i:
        S_ = orb_l - buf
        E_lo = max(vwap, orb_mid - 0.1 * atr_i)
        E_hi = min(orb_h + 0.25 * atr_i, vwap + 0.75 * atr_i)
        T1_ = max(path_h, orb_h) + max(orb_w, 1.8 * atr_i)
        _accept(True, E_lo, E_hi, S_, f"ORB下沿失效 {orb_l:.2f}", T1_, "趋势延伸",
                "短线偏多·VWAP回踩续多", "多头", "趋势回踩多")
    elif setup is None and strong_down and below_vwap and bearish and orb_l - 0.3 * atr_i <= px <= vwap:
        S_ = orb_h + buf
        E_hi = min(vwap, orb_mid + 0.1 * atr_i)
        E_lo = max(orb_l - 0.25 * atr_i, vwap - 0.75 * atr_i)
        T1_ = min(path_l, orb_l) - max(orb_w, 1.8 * atr_i)
        _accept(False, E_lo, E_hi, S_, f"ORB上沿失效 {orb_h:.2f}", T1_, "趋势延伸",
                "短线偏空·VWAP反抽续空", "空头", "趋势反抽空")
    elif setup is None and trend_flat and (bullish or bearish or broke_up or broke_down):
        signal = "观望"
        setup = "趋势未明"
        reason = f"{st_trend['label']}：无短线趋势，不逆势猜方向"
        entry_ready = False
    elif setup is None and ((trend_up and not strong_up) or (trend_down and not strong_down)):
        signal = "观望"
        setup = "弱趋势观察"
        reason = f"{st_trend['label']} score={st_trend['score']}：可等 ORB 同向突破；回踩/反抽需 |score|≥3"
        entry_ready = False

    # 平仓：结构级失效才喊平（空破 ORB 上 / 多破 ORB 下）
    if signal not in ("做多", "做空") and short_invalid:
        signal = "平空"
        entry_ready = False
        side = "cover_short"
        S = round(orb_h + buf, 4)
        S_key = f"ORB上沿 {orb_h:.2f}"
        reason = f"空头结构失效：价已站上ORB上沿 {orb_h:.2f}，先平空。" + (reason or "")
        setup = setup or "ORB失效平空"
    elif signal not in ("做多", "做空") and long_invalid:
        signal = "平多"
        entry_ready = False
        side = "cover_long"
        S = round(orb_l - buf, 4)
        S_key = f"ORB下沿 {orb_l:.2f}"
        reason = f"多头结构失效：价已跌破ORB下沿 {orb_l:.2f}，先平多。" + (reason or "")
        setup = setup or "ORB失效平多"

    entry_low = E_band[0] if E_band else None
    entry_high = E_band[1] if E_band else None
    entry_mid = round((entry_low + entry_high) / 2, 4) if entry_low is not None and entry_high is not None else None
    if signal == "平空":
        entry_label = "空单失效·先平"
    elif signal == "平多":
        entry_label = "多单失效·先平"
    elif signal == "做多" or (side == "long" and setup):
        entry_label = "做多入场价"
    elif signal == "做空" or (side == "short" and setup):
        entry_label = "做空入场价"
    else:
        entry_label = None

    return {
        "signal": signal,
        "reason": reason,
        "structure": structure,
        "setup": setup,
        "S": round(S, 4) if S is not None else None,
        "S_key": S_key,
        "E_band": E_band,
        "entry_label": entry_label,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_mid": entry_mid,
        "entry_ready": entry_ready,
        "T1": round(T1, 4) if T1 is not None else None,
        "T1_key": T1_key,
        "R1": round(R1v, 2) if R1v is not None else None,
        "stress_5x_pct": round(stress, 2) if stress is not None else None,
        "side": side,
        "short_invalid": short_invalid,
        "long_invalid": long_invalid,
        "short_trend": st_trend,
        "orb": {"low": round(orb_l, 4), "high": round(orb_h, 4)},
        "vwap": round(vwap, 4),
        "watch_only": signal in ("观望", "平空", "平多") and not entry_ready,
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
    daily, dmeta, src_d = fetch_chart(symbol, "3mo", "1d", prefer_cache=prefer_daily_cache)
    bars_iv, meta_iv, src_m = fetch_bars_intraday(
        symbol, iv, prefer_cache=force_cache or prefer_daily_cache
    )
    if src_m == "nasdaq" and meta_iv.get("previousClose") and not dmeta.get("previousClose"):
        dmeta = {**dmeta, "previousClose": meta_iv["previousClose"]}

    atr = atr14(daily)
    closes = [x["c"] for x in daily]
    ma20 = sma(closes, 20)
    last = daily[-1] if daily else None

    try:
        price, prev, chg_pct, price_tag, price_src, quote_ts = resolve_display_price(
            symbol, dmeta, sess_info
        )
    except Exception:
        price = float(dmeta.get("regularMarketPrice") or (last["c"] if last else 0))
        prev = dmeta.get("previousClose") or dmeta.get("chartPreviousClose")
        if prev is None and len(daily) > 1:
            prev = daily[-2]["c"]
        prev = float(prev) if prev is not None else None
        chg_pct = ((price / prev - 1) * 100) if prev and abs(prev - price) > 1e-9 else None
        price_tag, price_src, quote_ts = "回退", "cache/yahoo", None

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
    days = sorted(by_day)

    # 盘中优先今日 RTH；ORB 用开盘约 30 分钟（随周期换算 bar 数）
    ny_date = now_bj.astimezone(NY).date()
    ref_day = None
    struct_scope = f"今日RTH·{iv}"
    if sess_info.get("is_rth") and ny_date in by_day and len(by_day[ny_date]) >= orb_n:
        ref_day = ny_date
    else:
        for d in reversed(days):
            if len(by_day[d]) >= min_bars:
                ref_day = d
                if d != ny_date:
                    struct_scope = f"参照 {d} RTH·{iv}"
                elif not sess_info.get("is_rth"):
                    struct_scope = f"盘前相对 {d} RTH·{iv}"
                else:
                    struct_scope = f"今日RTH·{iv}"
                break
    today_bars = by_day.get(ref_day, []) if ref_day else []

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

    if not atr or not today_bars or len(today_bars) < min_bars:
        return {
            **base,
            "signal": "观望",
            "reason": f"{iv} 结构不足或 ATR 不足（需至少 {min_bars} 根；休市/数据少时常见）",
            "S": None,
            "S_key": None,
            "E_band": None,
            "T1": None,
            "R1": None,
            "stress_5x_pct": None,
            "structure": "未定",
            "orb": None,
            "vwap": None,
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
    path_l = min(sess_l, px)
    path_h = max(sess_h, px)

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
    )

    daily_bias = "—"
    if ma20:
        daily_bias = "日线偏空" if price < ma20 else "日线偏多"

    reason = sig["reason"]
    if reason:
        reason = f"[{iv}] {reason}"
    if "今日RTH" not in struct_scope and reason:
        reason = f"[{struct_scope} · 现价{px:.2f}] {reason}"

    return {
        **base,
        **sig,
        "reason": reason,
        "daily_bias": daily_bias,
        "bar_time_bj": last_b["dt_bj"].strftime("%Y-%m-%d %H:%M"),
    }


def analyze_many(
    symbols: Optional[List[str]] = None,
    interval: str = DEFAULT_INTERVAL,
) -> Dict[str, Any]:
    symbols = symbols or DEFAULT_SYMBOLS
    iv = normalize_interval(interval)
    now_bj = datetime.now(BJ)
    items = []
    errors = []
    for s in symbols:
        try:
            items.append(analyze_symbol(s.strip().upper(), interval=iv))
        except Exception as e:
            errors.append({"symbol": s, "error": str(e)})
            items.append(
                {
                    "symbol": s.upper(),
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
        "session": session_status(now_bj),
        "source_note": "【美股引擎】现价 Nasdaq→Yahoo；1m/5m/15m ORB≈开盘30分钟。主源见 markets/us/docs/market-data-sources.md；A股勿用本引擎",
        "rules_note": f"周期{iv}·短线趋势双向；ORB+回踩/反抽；T1远；R1≥0.8；5x仅参考；非AI",
        "items": items,
        "errors": errors,
    }


if __name__ == "__main__":
    print(json.dumps(analyze_many(["SNDK"]), ensure_ascii=False, indent=2))
