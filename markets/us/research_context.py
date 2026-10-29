"""Shared settings for offline US strategy research."""
from pathlib import Path
from zoneinfo import ZoneInfo

BJ = ZoneInfo('Asia/Shanghai')
NY = ZoneInfo('America/New_York')
SYMBOLS = ('SNDK', 'SKHY', 'MU', 'NVDA')
DEFAULT_DB = Path(__file__).resolve().parent / '.cache' / 'session-bars.sqlite3'
