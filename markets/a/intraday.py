#!/usr/bin/env python3
"""对本地 15 分钟行情 JSON 进行只读研究。"""
import argparse
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from strategy import classify, intraday_signal

parser = argparse.ArgumentParser(description='A股独立盘中研究：输入完整当日15分钟数据')
parser.add_argument('symbol')
parser.add_argument('bars_json', help='JSON 数组：time（K线结束时间）、high、low、close、volume')
parser.add_argument('--now', help='北京时间，格式 YYYY-MM-DD HH:MM；默认系统本地时间')
parser.add_argument('--upper', type=float, required=True, help='行情源当日涨停价')
parser.add_argument('--lower', type=float, required=True, help='行情源当日跌停价')
parser.add_argument('--accept-overnight', action='store_true', help='确认已考虑 T+1 隔夜风险')
args = parser.parse_args()
symbol, board = classify(args.symbol)
with open(args.bars_json, encoding='utf-8') as f:
    bars = json.load(f)
now = datetime.strptime(args.now, '%Y-%m-%d %H:%M') if args.now else datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
result = intraday_signal(bars, now, args.accept_overnight, args.upper, args.lower)
print(json.dumps({'symbol': symbol, 'board': board, 'time': now.isoformat(), 'result': result}, ensure_ascii=False, indent=2))
