"""获取指定交易日证券名单；baostock为requirements-feeds.txt中的可选公开数据源。"""
import argparse
from datetime import date
import json
from pathlib import Path
import signal
import socket


def fetch(day,out):
    date.fromisoformat(day)
    import baostock as bs
    socket.setdefaulttimeout(12)
    signal.alarm(60)
    login=bs.login()
    if login.error_code!='0':raise RuntimeError(login.error_msg)
    try:
        query=bs.query_all_stock(day=day)
        if query.error_code!='0':raise RuntimeError(query.error_msg)
        rows=[]
        while query.error_code=='0' and query.next():rows.append(dict(zip(query.fields,query.get_row_data())))
        if query.error_code!='0' or not rows:raise RuntimeError('指定日期没有完整证券名单，不使用其他日期代替')
        payload=dict(as_of=day,source=f'BaoStock query_all_stock(day={day})',stocks=rows)
        out.parent.mkdir(parents=True,exist_ok=True)
        temporary=out.with_suffix('.json.tmp');temporary.write_text(json.dumps(payload,ensure_ascii=False));temporary.replace(out)
        print('证券记录',len(rows),'日期',day,flush=True)
    finally:
        bs.logout();signal.alarm(0)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();fetch(a.date,a.output)
