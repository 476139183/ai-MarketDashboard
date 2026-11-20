"""A股走强形态研究；仅使用指定日及以前的数据，不发布交易计划。"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import gzip
import json
from math import isfinite
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

BJ=ZoneInfo('Asia/Shanghai')
ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'.cache/a-strength-radar'
LATEST=CACHE/'latest.json'
VERSION='a-strength-research-v3'
TREND_VERSION='a-trend-structure-v1'
LABELS={'base_recovery':'底部修复（高风险）','early_turn':'初步转强','steady_rise':'连续走强','volume_breakout':'温和放量突破','pullback_restart':'回踩再启动'}


def trend_context(rows,metrics,limit):
    """Research state, calculated after the caller validates the historical prefix."""
    from stock_setups import cent
    result=dict(version=TREND_VERSION,active=False,state='not_trending',execution_enabled=False)
    c=[float(r['close']) for r in rows];m=metrics;last=rows[-1]
    if not (m['ma5']>m['ma10']>m['ma20'] and m['ma20_slope']>0 and c[-1]>m['ma20'] and m['return20']>2):return result
    result.update(active=True,state='wait_confirmation',reason='趋势仍在；等待缩量回踩后的收复或放量突破确认')
    atr=sum(max(float(b['high'])-float(b['low']),abs(float(b['high'])-float(a['close'])),abs(float(b['low'])-float(a['close']))) for a,b in zip(rows[-15:-1],rows[-14:]))/14
    if atr<=0:return result
    result.update(atr14=atr,distance_ma5_atr=(c[-1]-m['ma5'])/atr)
    if last['high']==last['low'] or m['change_pct']>=limit*.9:
        result.update(state='wait_pullback',reason='一字或近涨停，保留趋势跟踪；不假定可追入');return result
    if c[-1]>m['ma5']+1.5*atr:
        result.update(state='wait_pullback',reason='距离MA5超过1.5ATR，保留趋势，等待回踩');return result
    touch=any(float(rows[i]['low'])<=sum(c[i-4:i+1])/5+.5*atr and
              float(rows[i]['volume'])<=sum(float(r['volume']) for r in rows[i-5:i])/5*.9
              for i in range(len(rows)-3,len(rows)-1))
    strength=(c[-1]-float(last['low']))/(float(last['high'])-float(last['low']))
    reclaim=touch and c[-1]>float(rows[-2]['high']) and c[-1]>=float(last['open']) and strength>=.6 and .8<=m['volume_ratio']<=2.5
    breakout=c[-1]>max(float(r['high']) for r in rows[-21:-1]) and strength>=.6 and 1.2<=m['volume_ratio']<=3
    if not (reclaim or breakout):return result
    stop=cent(max(min(float(r['low']) for r in rows[-3:]),m['ma10'])-.25*atr)
    lower,upper=cent(c[-1]-.5*atr,True),cent(min(c[-1]+.25*atr,m['ma5']+1.5*atr))
    if not 0<stop<lower<upper or upper-lower<.009999 or (upper-stop)/upper>.08:
        result.update(reason='趋势确认但价格区间或计划风险不合格');return result
    result.update(state='entry_confirmed',reason='回踩后收复前日高点' if reclaim else '放量突破此前20日高点',
                  rule='pullback_reclaim' if reclaim else 'volume_breakout',lower=lower,upper=upper,stop=stop,
                  target=None,risk_fraction=.0025,exit_rule='收盘失守MA10后观察后续新报价退出；T+1')
    return result


def evaluate(stock, rows, signal_date):
    def reject(stage, reason):return dict(stage=stage,reason=reason,patterns=[],metrics={},execution_enabled=False)
    name=stock.get('name','')
    if not name or 'ST' in name.upper() or '退' in name:return reject('security','风险警示、退市名称或名称缺失')
    rows=[r for r in rows if r.get('date','')<=signal_date]
    if len(rows)<60 or rows[-1]['date']!=signal_date:return reject('data','缺少信号日或不足60根日线')
    try:
        if any(a['date']>=b['date'] for a,b in zip(rows,rows[1:])):return reject('data','日线重复或乱序')
        for r in rows:
            o,h,l,c,v=[float(r[k]) for k in ('open','high','low','close','volume')]
            if not all(isfinite(x) for x in (o,h,l,c,v)) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:return reject('data','OHLCV非法或缺失')
        closes=[float(r['close']) for r in rows];volumes=[float(r['volume']) for r in rows]
    except (KeyError,ValueError,TypeError):return reject('data','OHLCV不可用')
    if volumes[-1]<=0:return reject('suspended','信号日没有有效成交量')
    ma=lambda n,offset=0:sum(closes[len(closes)-offset-n:len(closes)-offset])/n
    last=rows[-1];price=closes[-1];ma5,ma10,ma20,ma60=[ma(n) for n in (5,10,20,60)]
    v20=sum(volumes[-21:-1])/20
    amount20=sum(float(r['close'])*float(r['volume']) for r in rows[-20:])/20
    if amount20<5e7 or v20<=0:return reject('liquidity','近20日估算日均成交额不足5000万元')
    ret=lambda n:(price/closes[-n-1]-1)*100
    ret3,ret5,ret20=ret(3),ret(5),ret(20);bias=(price/ma20-1)*100;vr=volumes[-1]/v20
    change=(price/closes[-2]-1)*100
    # 只提示此前已发生的过热，不为涨停排队或公司行动假设可交易价格。
    limit=30 if stock.get('board')=='北交所' else 20 if stock.get('board') in ('创业板','科创板') else 10
    extended=ret5>15 or bias>12 or change>=limit*.9
    if any(abs(closes[i]/closes[i-1]-1)>.35 for i in range(len(closes)-25,len(closes))):return reject('data','近期价格跳变，未复权口径需核验')
    locked=float(last['high'])==float(last['low'])
    up=sum(closes[i]>closes[i-1] for i in range(len(closes)-5,len(closes)))
    high10=max(float(r['high']) for r in rows[-11:-1]);high20=max(float(r['high']) for r in rows[-21:-1])
    ma20_slope=(ma20/ma(20,5)-1)*100
    strength=(price-float(last['low']))/(float(last['high'])-float(last['low'])) if not locked else 0
    base=price>=ma20 and ma20_slope>=-.2
    touch=any(float(rows[i]['low'])<=sum(closes[i-9:i+1])/10*1.015 for i in range(len(rows)-5,len(rows)-1))
    patterns=[]
    # 修复与趋势分型：短均线刚交叉时，不提前宣称长均线已修复。
    cross_recent=ma5>ma10 and ma(5,3)<=ma(10,3)
    up3=sum(closes[i]>closes[i-1] for i in range(len(closes)-3,len(closes)))
    if ma20_slope<0 and cross_recent and price>ma5 and -12<=bias<=5 and 1<=ret3<=12 and up3>=2 and .8<=vr<=3:
        patterns.append('base_recovery')
    if base and ma5>ma10 and ma5>ma(5,3) and .5<=ret3<=6 and ret5<=8 and high10*.985<=price<=high10*1.02 and .9<=vr<=1.8:
        patterns.append('early_turn')
    if base and ma20_slope>0 and ma5>ma10>ma20 and 2<=ret5<=12 and up>=3 and change>=-2 and .65<=vr<=2.5:
        patterns.append('steady_rise')
    if base and price>high20 and .5<=change<=7 and 1.2<=vr<=3 and strength>=.55:
        patterns.append('volume_breakout')
    if base and ret20>2 and touch and price>ma5 and price>closes[-2] and price>=float(last['open']) and 1.05<=vr<=2.5 and strength>=.55:
        patterns.append('pullback_restart')
    metrics=dict(change_pct=round(change,3),return3=round(ret3,3),return5=round(ret5,3),return20=round(ret20,3),
        up_days5=up,volume_ratio=round(vr,3),bias20=round(bias,3),ma5=round(ma5,4),ma10=round(ma10,4),ma20=round(ma20,4),ma60=round(ma60,4),
        ma20_slope=round(ma20_slope,3),avg_turnover20=round(amount20),distance_high10=round((price/high10-1)*100,3))
    context=trend_context(rows,metrics,limit)
    if locked:return dict(stage='locked',reason='一字线，执行条件不可核验',patterns=[],metrics=metrics,close=price,score=0,execution_enabled=False,trend_context=context,risk_note='一字线仅保留历史跟踪，不假定可以排队成交')
    if extended:return dict(stage='extended',reason='涨幅、MA20乖离或近涨停风险过高；等待重新形成结构',patterns=[],metrics=metrics,close=price,score=0,execution_enabled=False,trend_context=context,risk_note=context.get('reason','过热跟踪，不能当作当前新机会'))
    score=round(20*len(patterns)+up*3+max(0,6-abs(bias-3))*2+min(2,vr)*4,2)
    return dict(stage='matched' if patterns else 'shape',reason=' / '.join(LABELS[p] for p in patterns) or '未满足固定走强形态',patterns=patterns,
        metrics=metrics,close=price,score=score,execution_enabled=False,trend_context=context,
        risk_note='底部修复：MA20仍向下，不能当作已成趋势，回落风险较高' if 'base_recovery' in patterns else '近5日上涨偏快，留意回踩' if ret5>8 else '与市场指数及事件分别核对，不代表后续必涨')


def screen(stocks,histories,signal_date):
    results=[]
    for stock in stocks:
        result=evaluate(stock,histories.get(stock['code'],[]),signal_date)
        results.append(dict(**stock,**result))
    counts=Counter(r['stage'] for r in results);items=[r for r in results if r['stage']=='matched']
    tracking=[]
    for result in results:
        if result['stage'] not in ('shape','extended','locked'):continue
        rows=[r for r in histories[result['code']] if r['date']<=signal_date]
        # 仅回看最近5个完整日线，历史信号不能冒充今日命中。
        if (result.get('trend_context') or {}).get('active'):
            tracking.append(dict(result,tracking=True,tracking_basis='trend_structure'))
        elif any(evaluate(result,rows[:i+1],rows[i]['date'])['stage']=='matched' for i in range(max(59,len(rows)-6),len(rows)-1)):
            tracking.append(dict(result,tracking=True,tracking_basis='prior_pattern'))
    for item in items+tracking:
        rows=[r for r in histories[item['code']] if r['date']<=signal_date]
        recent=rows[-20:];window=recent[0]['date'];events=replay_stock(item,rows,start=window)
        dates={e['date'] for e in events};continuous=0
        for row in reversed(recent):
            if row['date'] not in dates:break
            continuous+=1
        item.update(first_signal_date=events[0]['date'] if events else None,latest_signal_date=events[-1]['date'] if events else None,tracking_patterns=events[-1]['patterns'] if events else [],matched_sessions_last20=len(events),continuing_sessions=continuous,first_signal_scope='最近20根日线内首现')
        if item.get('tracking_basis')=='trend_structure':
            structures=[rows[i]['date'] for i in range(max(59,len(rows)-20),len(rows)) if evaluate(item,rows[:i+1],rows[i]['date']).get('trend_context',{}).get('active')]
            item['first_structure_date']=structures[0] if structures else None
            item['latest_structure_date']=structures[-1] if structures else None
    items.sort(key=lambda r:(-r['score'],r['code']))
    tracking.sort(key=lambda r:(r.get('latest_structure_date') or r.get('latest_signal_date') or '',r['code']),reverse=True)
    ready=len(results)-counts['data']
    return dict(kind='a_share_strength_research',rule_version=VERSION,signal_date=signal_date,execution_enabled=False,validation_status='research_only',
        generated_at=datetime.now(BJ).isoformat(timespec='seconds'),items=items,tracking_items=tracking,stages=dict(counts),
        pattern_counts={k:sum(k in r['patterns'] for r in items) for k in LABELS},
        coverage=dict(total=len(stocks),ready=ready,missing=counts['data'],complete=bool(stocks) and not counts['data']),
        limitations=['仅筛选已经发生的量价形态，不保证提前预测上涨','证券名单为当前快照，不是历史全市场成分',
            '日线未复权，成交额为收盘价乘股数估算；真实资金流与涨停队列未接入','评分仅是固定规则匹配排序，不是胜率；不触发模拟交易'])


def replay_stock(stock,rows,start=None):
    """逐日截断回放：命中发生日与后续涨跌分开，不改规则或账户。"""
    events=[];previous=set()
    for i in range(59,len(rows)):
        day=rows[i]['date'];result=evaluate(stock,rows[:i+1],day);current=set(result['patterns'])
        if start and day<start:previous=current;continue
        if current:
            item=dict(date=day,patterns=result['patterns'],new_patterns=sorted(current-previous),close=result['close'],metrics=result['metrics'])
            # 完整后续窗口才计算；不声称盘中成交或策略收益。
            item['forward5_close_pct']=round((float(rows[i+5]['close'])/result['close']-1)*100,3) if i+5<len(rows) else None
            events.append(item)
        previous=current
    return events


class StrengthService:
    def __init__(self):
        self.lock=threading.Lock();self.state=dict(status='idle',completed=0,total=0,error=None)
    def snapshot(self):
        try:report=json.loads(LATEST.read_text())
        except (OSError,ValueError):report=dict(items=[],coverage={},signal_date=None,execution_enabled=False)
        from hot_backtest import as_of_date
        from datetime import timedelta
        now=datetime.now(BJ);expected=datetime.fromisoformat(as_of_date(now)).date()
        while expected.weekday()>4:expected-=timedelta(days=1)
        report['stale']=report.get('signal_date')!=expected.isoformat()
        try:
            validation=json.loads((CACHE/'validation.json').read_text())
            if validation.get('rule_version')==report.get('rule_version'):report['validation']=validation
        except (OSError,ValueError):pass
        with self.lock:return dict(report,**self.state)
    def start(self):
        with self.lock:
            if self.state['status']=='running':return False
            self.state.update(status='running',completed=0,total=0,error=None)
        threading.Thread(target=self._job,daemon=True).start();return True
    def _job(self):
        try:
            report=collect(self.progress)
            publish(report)
        except Exception as exc:
            with self.lock:self.state['error']=str(exc)[:180]
        finally:
            with self.lock:self.state['status']='idle'
    def progress(self,completed,total):
        with self.lock:self.state.update(completed=completed,total=total)


def publish(report):
    CACHE.mkdir(parents=True,exist_ok=True);reports=CACHE/'reports';reports.mkdir(exist_ok=True)
    payload=json.dumps(report,ensure_ascii=False,allow_nan=False)
    daily=reports/(report['signal_date']+'-'+report['rule_version']+'.json');tmp=daily.with_suffix('.tmp');tmp.write_text(payload);tmp.replace(daily)
    tmp=LATEST.with_suffix('.tmp');tmp.write_text(payload);tmp.replace(LATEST)


def collect(progress=lambda done,total:None):
    from market_scan import fetch_universe,fetch_recent
    from radar import _hs300
    from hot_backtest import as_of_date
    now=datetime.now(BJ);end=as_of_date(now);benchmark=_hs300(end)
    if not benchmark:raise ValueError('无法核验最近完整指数交易日，保留上次名单')
    day=benchmark[-1]['date']
    if (now.date()-datetime.fromisoformat(day).date()).days>7:raise ValueError('指数日期超过7天，保留旧名单并等待来源恢复')
    raw_count,stocks,excluded=fetch_universe();progress(0,len(stocks));path=CACHE/day;path.mkdir(parents=True,exist_ok=True)
    (path/'universe.json').write_text(json.dumps(dict(collected_at=now.isoformat(),total=raw_count,stocks=stocks,excluded=excluded),ensure_ascii=False))
    histories={};failures={}
    # 只按信号日存档复用，不把过期窗口当成本日数据。
    local=ROOT/'markets/a/.cache/full-market'/f'{day}-daily.json.gz'
    if local.exists():
        try:histories=json.load(gzip.open(local,'rt'))
        except (OSError,ValueError):pass
    def one(stock):
        cache=path/(stock['code']+'.json')
        try:
            rows=histories.get(stock['code'])
            if not rows and cache.exists():rows=json.loads(cache.read_text())
            if not rows or rows[-1]['date']!=day or len(rows)<60:rows=fetch_recent(stock,day,count=120)
            rows=[r for r in rows if r['date']<=day]
            if not rows or rows[-1]['date']!=day:raise ValueError('信号日日线未就绪')
            cache.write_text(json.dumps(rows));return rows,None
        except Exception as exc:return [],str(exc)[:140]
    ready={}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs={pool.submit(one,s):s for s in stocks}
        for i,future in enumerate(as_completed(jobs),1):
            stock=jobs[future];rows,error=future.result();ready[stock['code']]=rows
            if error:failures[stock['code']]=error
            progress(i,len(stocks))
    report=screen(stocks,ready,day);report.update(universe_total=raw_count,security_excluded=len(excluded),failures=failures,source='新浪全A证券快照及未复权日线')
    if not report['coverage']['ready']:raise ValueError('没有取得可用日线，保留上次名单')
    return report

SERVICE=StrengthService()
