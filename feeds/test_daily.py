import unittest
from datetime import date, timedelta
from unittest.mock import patch

from feeds import daily
from feeds.schema import normalize_bars


def _bars(n=30, close0=100.0):
    start = date(2025, 10, 1)
    out = []
    i = 0
    while len(out) < n:
        d = start + timedelta(days=i)
        i += 1
        if d.weekday() >= 5:
            continue
        c = close0 + len(out) * 0.1
        out.append(dict(date=d.isoformat(), open=c, high=c + 1, low=c - 1, close=c, volume=1000 + len(out)))
    return out


class SchemaTests(unittest.TestCase):
    def test_normalize_dedupes_and_sorts(self):
        bars = normalize_bars([
            dict(date='2026-01-02', open=1, high=2, low=0.5, close=1.5, volume=10),
            dict(date='2026-01-01', open=1, high=2, low=0.5, close=1.2, volume=9),
            dict(date='2026-01-02', open=1, high=3, low=0.5, close=2, volume=11),
        ])
        self.assertEqual([b['date'] for b in bars], ['2026-01-01', '2026-01-02'])
        self.assertEqual(bars[-1]['close'], 2)


class CascadeTests(unittest.TestCase):
    def test_cn_falls_through_to_second_source(self):
        primary = _bars(25)
        secondary = [dict(**b) for b in primary]
        last = dict(secondary[-1])
        last['close'] = last['close'] * 1.05
        secondary[-1] = last

        def boom(*_a, **_k):
            raise RuntimeError('down')

        providers = {
            'dsa': boom,
            'tencent': lambda code, days=180: (primary, 'tencent_qfq'),
            'akshare': lambda code, days=180: (secondary, 'akshare_qfq'),
            'baostock': boom,
            'tushare': boom,
            'sina': boom,
        }
        env = {'CN_DAILY_SOURCES': 'dsa,tencent,akshare', 'FEEDS_CROSSCHECK': '1'}
        with patch.dict(daily.MARKET_MAP, {'cn': (providers, ('dsa', 'tencent', 'akshare'))}), \
             patch.dict('os.environ', env, clear=False):
            result = daily.fetch_daily('600519', market='cn', days=20, min_bars=10)
        self.assertEqual(result['source'], 'tencent')
        self.assertTrue(any(not a['ok'] for a in result['attempts']))
        self.assertTrue(any('偏离' in w for w in result['warnings']))

    def test_skips_uninstalled_optional_and_raises_when_all_fail(self):
        providers = {
            'dsa': lambda *a, **k: (_ for _ in ()).throw(RuntimeError('dsa down')),
            'yahoo': lambda *a, **k: (_ for _ in ()).throw(RuntimeError('yahoo down')),
            'yfinance': lambda *a, **k: (_ for _ in ()).throw(RuntimeError('未安装 yfinance')),
            'alphavantage': lambda *a, **k: (_ for _ in ()).throw(RuntimeError('缺 Key')),
            'finnhub': lambda *a, **k: (_ for _ in ()).throw(RuntimeError('缺 Key')),
        }
        with patch.dict(daily.MARKET_MAP, {'us': (providers, ('dsa', 'yahoo', 'yfinance'))}), \
             patch.dict('os.environ', {'US_DAILY_SOURCES': 'dsa,yahoo,yfinance'}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                daily.fetch_daily('AAPL', market='us', days=20, min_bars=5)
        self.assertIn('全部失败', str(ctx.exception))

    def test_source_status_reports_missing_token(self):
        with patch.dict('os.environ', {'TUSHARE_TOKEN': '', 'CN_DAILY_SOURCES': 'tushare,tencent'}, clear=False):
            rows = {r['source']: r for r in daily.source_status('cn')}
        self.assertIn('tushare', rows)
        self.assertFalse(rows['tushare']['ready'])
        self.assertTrue(rows['tencent']['ready'])


if __name__ == '__main__':
    unittest.main()
