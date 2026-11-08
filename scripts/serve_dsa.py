"""Run the pinned upstream service with a scoped, public-only news DNS fallback."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    # start-dsa.sh sets cwd to the isolated upstream checkout; leave its files untouched.
    sys.path.insert(0, str(Path.cwd()))
    mode = os.environ.get('DSA_NEWS_DNS_MODE', 'auto').strip().lower()
    if mode not in ('auto', 'system'):
        parser.error('DSA_NEWS_DNS_MODE must be auto or system')
    if mode == 'auto':
        from news_dns import install
        install()
    import uvicorn
    uvicorn.run('server:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
