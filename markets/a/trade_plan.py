"""次日观察用的确定性作战计划（参考 DSA 纪律，不调用大模型）。"""

BIAS_LIMIT = 5.0


def _ma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def build_trade_plan(bars):
    """根据信号日已收盘日线，生成次日观察点位与检查清单。"""
    if len(bars) < 20:
        return None
    closes = [float(x['close']) for x in bars]
    highs = [float(x['high']) for x in bars]
    volumes = [float(x.get('volume') or 0) for x in bars]
    close = closes[-1]
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    if not all(v and v > 0 for v in (ma5, ma10, ma20)):
        return None
    bias = (close - ma5) / ma5 * 100
    if ma5 > ma10 > ma20:
        alignment, align_status = '多头排列 MA5>MA10>MA20', '满足'
    elif ma5 > ma10:
        alignment, align_status = '弱势多头（MA10未上MA20）', '注意'
    elif ma5 < ma10 < ma20:
        alignment, align_status = '空头排列', '不满足'
    else:
        alignment, align_status = '均线纠缠', '注意'
    prior_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1] or [close])
    vol_ma5 = _ma(volumes, 5) or 0
    vol_ok = volumes[-1] >= vol_ma5 * .8 if vol_ma5 else False
    chase = bias > BIAS_LIMIT
    ideal = round(ma5, 4)
    secondary = round(ma10, 4)
    stop = round(ma20 * .98, 4)
    target = round(max(prior_high, close * 1.05), 4)
    checklist = [
        {'item': '均线多头', 'detail': alignment, 'status': align_status},
        {'item': '不追高', 'detail': f'乖离MA5 {bias:+.2f}%（阈值{BIAS_LIMIT}%）',
         'status': '不满足' if chase else ('注意' if bias > 3 else '满足')},
        {'item': '量能', 'detail': '不低于5日均量八成' if vol_ok else '量能偏弱或不足',
         'status': '满足' if vol_ok else '注意'},
        {'item': '结构空间', 'detail': f'止损 {stop} → 目标 {target}',
         'status': '满足' if target > close > stop else '注意'},
    ]
    ready = align_status == '满足' and not chase and close > stop
    return {
        'ma5': round(ma5, 4), 'ma10': round(ma10, 4), 'ma20': round(ma20, 4),
        'bias_ma5': round(bias, 2), 'alignment': alignment,
        'chase_risk': chase,
        'plan_ready': ready,
        'sniper': {
            'ideal_buy': ideal,
            'secondary_buy': secondary,
            'stop_loss': stop,
            'take_profit': target,
            'note': '理想买≈MA5，次优≈MA10，止损参考MA20下；目标取近20日高点与5%弹性较大者',
        },
        'checklist': checklist,
        'summary': ('次日可按回踩计划观察' if ready else
                    ('乖离偏高，不追高，等回踩' if chase else '趋势未齐，仅观察不优先')),
    }
