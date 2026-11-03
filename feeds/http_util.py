from __future__ import annotations

from urllib.request import Request, urlopen

UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


def read_text(url: str, *, encoding: str = 'utf-8', timeout: int = 20, referer: str = '') -> str:
    headers = {'User-Agent': UA}
    if referer:
        headers['Referer'] = referer
    with urlopen(Request(url, headers=headers), timeout=timeout) as response:
        return response.read().decode(encoding)


def read_json(url: str, **kwargs):
    import json
    return json.loads(read_text(url, **kwargs))
