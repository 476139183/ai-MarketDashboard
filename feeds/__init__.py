"""轻量多源日线协作：级联取数 + 可选交叉核对。软依赖可选库，缺包则跳过该源。"""
from .daily import (
    fetch_daily,
    list_sources,
    source_status,
)

__all__ = ['fetch_daily', 'list_sources', 'source_status']
