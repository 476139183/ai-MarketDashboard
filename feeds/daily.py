"""多市场日线级联：按优先级尝试，可选第二源交叉核对收盘价。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import cn, us

MARKET_MAP = {
    'cn': (cn.PROVIDERS, cn.DEFAULT_ORDER),
    'a': (cn.PROVIDERS, cn.DEFAULT_ORDER),
    'us': (us.PROVIDERS, us.DEFAULT_ORDER),
}

ENV_ORDER = {
    'cn': 'CN_DAILY_SOURCES',
    'a': 'CN_DAILY_SOURCES',
    'us': 'US_DAILY_SOURCES',
}


def _parse_order(market: str, override: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    providers, default = MARKET_MAP[market]
    if override:
        names = [x.strip() for x in override if x and x.strip()]
    else:
        raw = os.environ.get(ENV_ORDER[market], '').strip()
        names = [x.strip() for x in raw.split(',') if x.strip()] if raw else list(default)
    unknown = [n for n in names if n not in providers]
    if unknown:
        raise ValueError(f'{market} 未知数据源: {", ".join(unknown)}')
    return tuple(names)


def _dsa_ready() -> bool:
    return bool(os.environ.get('DSA_BASE_URL', '').strip())


def _make_dsa_client():
    # 延迟导入，避免 feeds 强依赖看板路径
    try:
        from dsa_client import DSAClient  # type: ignore
        return DSAClient.from_env()
    except Exception:
        import sys
        from pathlib import Path
        dash = Path(__file__).resolve().parents[1] / 'markets' / 'us' / 'dashboard'
        if str(dash) not in sys.path:
            sys.path.insert(0, str(dash))
        from dsa_client import DSAClient  # type: ignore
        return DSAClient.from_env()


def _call(provider, symbol: str, days: int, name: str):
    if name == 'dsa':
        if not _dsa_ready():
            raise RuntimeError('未配置 DSA_BASE_URL')
        return provider(symbol, days=days, client=_make_dsa_client())
    return provider(symbol, days=days)


def list_sources(market: str = 'cn') -> List[str]:
    market = market.lower()
    return list(_parse_order(market))


def source_status(market: str = 'cn') -> List[Dict[str, Any]]:
    """探测各源是否可用（不拉行情）：包/Token/环境。"""
    market = market.lower()
    providers, _ = MARKET_MAP[market]
    out = []
    for name in _parse_order(market):
        info: Dict[str, Any] = {'source': name, 'ready': True, 'note': ''}
        if name == 'dsa':
            info['ready'] = _dsa_ready()
            info['note'] = 'DSA_BASE_URL' if info['ready'] else '缺 DSA_BASE_URL'
        elif name == 'tushare':
            info['ready'] = bool(os.environ.get('TUSHARE_TOKEN', '').strip())
            try:
                import tushare  # noqa: F401
            except ImportError:
                info['ready'] = False
                info['note'] = '未安装 tushare 或缺 Token'
            else:
                info['note'] = 'Token 已配置' if info['ready'] else '缺 TUSHARE_TOKEN'
        elif name == 'akshare':
            try:
                import akshare  # noqa: F401
            except ImportError:
                info['ready'] = False
                info['note'] = '未安装 akshare'
        elif name == 'baostock':
            try:
                import baostock  # noqa: F401
            except ImportError:
                info['ready'] = False
                info['note'] = '未安装 baostock'
        elif name == 'yfinance':
            try:
                import yfinance  # noqa: F401
            except ImportError:
                info['ready'] = False
                info['note'] = '未安装 yfinance'
        elif name == 'alphavantage':
            info['ready'] = bool(os.environ.get('ALPHAVANTAGE_API_KEY', '').strip())
            info['note'] = 'Key 已配置' if info['ready'] else '缺 ALPHAVANTAGE_API_KEY'
        elif name == 'finnhub':
            info['ready'] = bool(os.environ.get('FINNHUB_API_KEY', '').strip())
            info['note'] = 'Key 已配置' if info['ready'] else '缺 FINNHUB_API_KEY'
        elif name in ('tencent', 'sina', 'yahoo'):
            info['note'] = '公开 HTTP'
        if name not in providers:
            info['ready'] = False
            info['note'] = '未注册'
        out.append(info)
    return out


def _crosscheck(primary: List[dict], secondary: List[dict], *, tol: float = 0.02) -> Optional[str]:
    if not primary or not secondary:
        return None
    a, b = primary[-1], secondary[-1]
    if a['date'] != b['date']:
        return f"交叉核对日期不一致：主源 {a['date']} vs 次源 {b['date']}"
    if a['close'] <= 0:
        return None
    diff = abs(a['close'] - b['close']) / a['close']
    if diff > tol:
        return (f"交叉核对收盘偏离 {diff:.2%}："
                f"主源 {a['close']} vs 次源 {b['close']}（{a['date']}）")
    return None


def fetch_daily(
    symbol: str,
    *,
    market: str = 'cn',
    days: int = 180,
    sources: Optional[Sequence[str]] = None,
    crosscheck: Optional[bool] = None,
    min_bars: int = 20,
) -> Dict[str, Any]:
    """
    级联拉取日线。

    返回:
      bars, source, attempts[{source,ok,error,label}], warnings, market
    """
    market = market.lower().strip()
    if market not in MARKET_MAP:
        raise ValueError(f'不支持的市场: {market}')
    providers, _ = MARKET_MAP[market]
    order = _parse_order(market, sources)
    attempts: List[Dict[str, Any]] = []
    bars = None
    source = None
    label = None
    warnings: List[str] = []

    for name in order:
        provider = providers[name]
        try:
            got, got_label = _call(provider, symbol, days, name)
            if len(got) < min_bars:
                raise ValueError(f'仅 {len(got)} 根，少于 min_bars={min_bars}')
            attempts.append({'source': name, 'ok': True, 'error': None, 'label': got_label, 'bars': len(got)})
            if bars is None:
                bars, source, label = got, name, got_label
                do_check = crosscheck if crosscheck is not None else (
                    os.environ.get('FEEDS_CROSSCHECK', '').strip().lower() in ('1', 'true', 'yes')
                )
                if do_check:
                    continue  # 再拉一个成功源做核对
                break
            # 第二成功源：交叉核对后结束
            note = _crosscheck(bars, got)
            if note:
                warnings.append(note)
            else:
                warnings.append(f'交叉核对通过（相对 {got_label}）')
            break
        except Exception as exc:
            attempts.append({'source': name, 'ok': False, 'error': str(exc), 'label': None, 'bars': 0})

    if bars is None:
        errors = '；'.join(f"{a['source']}: {a['error']}" for a in attempts) or '无可用源'
        raise RuntimeError(f'{market}:{symbol} 日线全部失败 — {errors}')

    return {
        'symbol': symbol,
        'market': market,
        'bars': bars,
        'source': source,
        'source_label': label,
        'attempts': attempts,
        'warnings': warnings,
        'as_of': bars[-1]['date'],
        'count': len(bars),
    }
