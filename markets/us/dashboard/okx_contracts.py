"""OKX public SWAP data, normalized for the closed-bar contract radar."""
from concurrent.futures import ThreadPoolExecutor
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from contract_radar import ContractRadar, WATCHLIST, number


def fetch_public(path, params):
    request = Request('https://www.okx.com/api/v5/' + path + '?' + urlencode(params),
                      headers={'User-Agent': 'MarketStudio/1.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=8) as response:
        data = json.load(response)
    if data.get('code') != '0' or not isinstance(data.get('data'), list):
        raise ValueError('OKX: ' + str(data.get('msg', '无效响应')))
    return data['data']


class OKXFeed:
    def __init__(self, fetch=None):
        self.fetch = fetch or fetch_public
        self.metrics = {}

    def optional(self, path, params):
        try:
            return self.fetch(path, params)
        except (OSError, ValueError):
            return []

    def funding(self, instrument):
        cached = self.metrics.get(instrument)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        rows = self.optional('public/funding-rate', {'instId': instrument})
        rate = rows[0].get('fundingRate') if rows else None
        self.metrics[instrument] = (time.monotonic(), rate)
        return rate

    def __call__(self, path, params):
        if path == 'candles':
            instrument = params['symbol'][:-4] + '-USDT-SWAP'
            rows = self.fetch('market/candles', {'instId': instrument, 'bar': '4H', 'limit': '120'})
            # OKX volume[5] is contracts, volume[6] is base currency; confirm[8] is authoritative.
            return [row[:5] + row[6:8] for row in rows if len(row) >= 9 and row[8] == '1']
        if path != 'tickers':
            raise ValueError('不支持的公开行情接口')
        quotes = self.fetch('market/tickers', {'instType': 'SWAP'})
        instruments = {row['instId']: row for row in self.fetch('public/instruments', {'instType': 'SWAP'})
                       if row.get('state') == 'live' and row.get('settleCcy') == 'USDT'
                       and row.get('ctType') == 'linear'}
        marks = {row['instId']: row for row in self.optional('public/mark-price', {'instType': 'SWAP'})}
        interests = {row['instId']: row for row in self.optional('public/open-interest', {'instType': 'SWAP'})}
        wanted = {spec[0] for spec in WATCHLIST}
        selected = [row for row in quotes if row.get('instId') in instruments
                    and row['instId'].replace('-USDT-SWAP', 'USDT') in wanted]

        def normalize(row):
            instrument = row['instId']
            metadata = instruments[instrument]
            last, opening = number(row.get('last'), positive=True), number(row.get('open24h'), positive=True)
            volume = number(row.get('volCcy24h'))
            return dict(symbol=instrument.replace('-USDT-SWAP', 'USDT'), instId=instrument,
                        lastPr=row.get('last'), ts=row.get('ts'), bidPr=row.get('bidPx'), askPr=row.get('askPx'),
                        change24h=(last - opening) / opening if last and opening else None,
                        fundingRate=self.funding(instrument),
                        holdingAmount=interests.get(instrument, {}).get('oiCcy'),
                        markPrice=marks.get(instrument, {}).get('markPx'),
                        usdtVolume=volume * last if volume is not None and last else None,
                        volume_estimated=True, ctVal=metadata.get('ctVal'),
                        lotSz=metadata.get('lotSz'), minSz=metadata.get('minSz'))

        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(normalize, selected))


SERVICE = ContractRadar(OKXFeed(), source='OKX')
