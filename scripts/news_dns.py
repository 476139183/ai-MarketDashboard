"""Resolve proxy Fake-IP only within DSA intelligence calls; keep SSRF checks intact."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import ipaddress
import json
import socket
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener

SYSTEM_GETADDRINFO = socket.getaddrinfo
NEWS_CONTEXT = ContextVar('dsa_news_dns', default=False)
FAKE_NETWORK = ipaddress.ip_network('198.18.0.0/15')


@contextmanager
def news_context():
    token = NEWS_CONTEXT.set(True)
    try:
        yield
    finally:
        NEWS_CONTEXT.reset(token)


def for_news(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with news_context():
            return function(*args, **kwargs)
    return wrapped


class NewsDNSResolver:
    def __init__(self):
        self.cache = {}
        self.lock = threading.Lock()

    def lookup(self, host):
        # Fixed HTTPS resolver, certificate verification on. Never send cookies or API keys.
        url = 'https://cloudflare-dns.com/dns-query?' + urlencode({'name': host, 'type': 'A'})
        request = Request(url, headers={'Accept': 'application/dns-json'})
        token = NEWS_CONTEXT.set(False)  # DoH itself must not recursively use this fallback.
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise OSError('News DNS response too large')
            data = json.loads(raw)
            if data.get('Status') != 0:
                raise OSError('News DNS lookup failed')
            return [r['data'] for r in data.get('Answer', []) if r.get('type') == 1]
        except (ValueError, KeyError, TypeError) as exc:
            raise OSError('Invalid news DNS response') from exc
        finally:
            NEWS_CONTEXT.reset(token)

    def public_addresses(self, host):
        with self.lock:
            now = time.monotonic()
            cached = self.cache.get(host)
            if cached and cached[0] > now:
                return list(cached[1])
            addresses = self.lookup(host)
            if not addresses or len(addresses) > 32:
                raise OSError('News DNS returned no public addresses')
            try:
                for value in addresses:
                    ip = ipaddress.ip_address(value)
                    if ip.version != 4 or not ip.is_global or ip.is_multicast:
                        raise ValueError('Not a public IPv4 address')
            except ValueError as exc:
                raise OSError('News DNS returned a blocked address') from exc
            if len(self.cache) >= 512:
                self.cache.clear()
            self.cache[host] = (now + 300, tuple(addresses))
            return list(addresses)

    def getaddrinfo(self, host, port, *args, **kwargs):
        rows = SYSTEM_GETADDRINFO(host, port, *args, **kwargs)
        if not NEWS_CONTEXT.get() or not isinstance(host, (str, bytes)):
            return rows
        name = host.decode('ascii') if isinstance(host, bytes) else host
        name = name.lower().rstrip('.')
        if name in ('localhost', 'localhost.localdomain') or name.endswith(('.local', '.localhost')):
            return rows
        try:
            ipaddress.ip_address(name)
            return rows  # Literal IPs still pass through upstream validation unchanged.
        except ValueError:
            pass
        ips = [ipaddress.ip_address(row[4][0]) for row in rows]
        if not any(ip.version == 4 and ip in FAKE_NETWORK for ip in ips):
            return rows
        # Do not replace a mixed answer containing actual private/local addresses.
        if any(not ip.is_global and not (ip.version == 4 and ip in FAKE_NETWORK) for ip in ips):
            return rows
        result = []
        for address in self.public_addresses(name):
            result.extend(SYSTEM_GETADDRINFO(address, port, *args, **kwargs))
        return result


def install():
    from src.services.intelligence_service import IntelligenceService
    if getattr(IntelligenceService, '_dashboard_news_dns', False):
        return
    resolver = NewsDNSResolver()
    socket.getaddrinfo = resolver.getaddrinfo
    for method in ('_validate_url', '_get_with_validated_dns'):
        setattr(IntelligenceService, method, for_news(getattr(IntelligenceService, method)))
    IntelligenceService._dashboard_news_dns = True
