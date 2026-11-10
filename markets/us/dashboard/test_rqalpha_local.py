import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('rqalpha'),'使用.venv-quant运行独立引擎测试')
class RQAlphaLocalTests(unittest.TestCase):
    def test_month_contract_exchange_metadata_is_market_specific(self):
        import rqalpha_local as L
        row=dict(date='2026-09-01',open=100,high=101,low=99,close=100,volume=10)
        p=dict(market='futures',daily={s:[row] for s in ['RB2701','M2701','SC2701']},calendar=['2026-09-01'],start='2026-09-01',end='2026-09-01',config={},minutes={})
        source=L.LocalSource(p)
        self.assertEqual(source.instruments['RB2701'].exchange,'SHFE')
        self.assertEqual(source.instruments['M2701'].exchange,'DCE')
        self.assertEqual(source.instruments['SC2701'].exchange,'INE')
    def test_real_futures_matching_applies_multiplier_and_fees(self):
        import rqalpha_local as L
        from rqalpha.api import subscribe,buy_open,sell_close
        dates=['2026-09-01','2026-09-02','2026-09-03','2026-09-04']
        rows=[dict(date=d,open=p,high=p+1,low=p-1,close=p,volume=10000) for d,p in zip(dates,[100,101,104,103])]
        payload=dict(market='futures',daily={'RB2701':rows},calendar=dates,start=dates[1],end=dates[-1],capital=100000,minutes={},config=dict(multiplier=10,tick_size=1,fee_fixed=3,margin_rate=.15))
        def init(ctx):subscribe('RB2701')
        def handle(ctx,bars):
            if ctx.now.date().isoformat()==dates[1]:buy_open('RB2701',1)
            elif ctx.now.date().isoformat()==dates[2]:sell_close('RB2701',1)
        out=L.run_local(payload,handle,init)
        self.assertEqual(out['summary']['closed_trades'],1)
        self.assertAlmostEqual(out['summary']['end_equity'],100004)
        self.assertAlmostEqual(out['summary']['fees'],6)

    def test_real_stock_matching_enforces_t_plus_one_and_charges_costs(self):
        import rqalpha_local as L
        from rqalpha.api import order_shares,subscribe
        dates=['2026-09-01','2026-09-02','2026-09-03','2026-09-04']
        daily=[dict(date=d,open=10,high=12,low=9,close=11,volume=10000) for d in dates]
        payload=dict(market='a_share',daily={'600000.XSHG':daily},calendar=dates,start=dates[1],end=dates[-1],capital=100000.,minutes={},config={})
        def init(ctx):subscribe('600000.XSHG')
        def handle(ctx,bars):
            if ctx.now.date().isoformat()==dates[1]:
                order_shares('600000.XSHG',100)
                order_shares('600000.XSHG',-100)
            elif ctx.now.date().isoformat()==dates[2]:order_shares('600000.XSHG',-100)
        out=L.run_local(payload,handle,init=init)
        self.assertEqual([r['side'] for r in out['fills']],['BUY','SELL'])
        self.assertNotEqual(out['fills'][0]['datetime'][:10],out['fills'][1]['datetime'][:10])
        self.assertGreater(out['summary']['fees'],0)
        self.assertEqual(out['summary']['closed_trades'],1)
        self.assertGreater(out['summary']['rejected_orders'],0)

    def test_minute_source_does_not_expose_full_daily_high_or_close(self):
        import rqalpha_local as L
        from datetime import datetime
        p=dict(market='a_share',daily={'600000.XSHG':[dict(date='2026-09-01',open=10,high=100,low=1,close=99,volume=10000)]},calendar=['2026-09-01'],start='2026-09-01',end='2026-09-01',minutes={'600000.XSHG':[dict(time='2026-09-01 09:45',open=10,high=90,low=2,close=80,volume=100)]},config={})
        source=L.LocalSource(p)
        ins=list(source.get_instruments())[0]
        bar=source.get_bar(ins,datetime(2026,9,1,9,30),'1m')
        self.assertEqual((bar['open'],bar['close'],bar['high'],bar['low']),(10,10,10,10))


if __name__=='__main__':unittest.main()
