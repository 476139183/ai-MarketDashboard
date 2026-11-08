const el = id => document.getElementById(id);
const TASK_KEY = 'dsa_last_task';
const WATCH_KEY = 'market_studio_watchlist';
const TASKS_KEY = 'market_studio_ai_tasks';
const NAMES = {NVDA: '英伟达', SNDK: '闪迪', MU: '美光科技', AAPL: '苹果', '600519': '贵州茅台'};
let task = null, pollTimer = null, connected = false, modelReady = false;
let chart = null, requestVersion = 0, selectedDays = 90;
let signalVersion = 0, signalExpiryTimer = null, aiSettings = null;
let searchVersion = 0, searchTimer = null, pendingResearchStock = null;
let activeSymbol = '', taskViewVersion = 0;
const HISTORY_DAYS = 365;
let dailyCache = null, dailyStructure = null;
const dailyStructureControls=globalThis.MarketStructure?new MarketStructure.Controls(el('daily-structure')):null;
const stockTasks = Object.create(null), stockReports = Object.create(null), submitting = new Set();
let chosenAIBackend = '', serverModelReady = false;
try {chosenAIBackend=localStorage.getItem('market_studio_ai_choice')||'';}catch(_){}
if(!['litellm','codex_cli','claude_code_cli'].includes(chosenAIBackend))chosenAIBackend='';
let watchlist = ['NVDA', 'SNDK', 'MU', 'AAPL', '600519'];
const validCode = value => typeof value === 'string' && /^(?:\d{6}|[A-Z]{1,5}(?:[.-][A-Z])?)$/.test(value);
const number = value => value == null ? '—' : Number(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});

async function api(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 65000);
  try {
    const response = await fetch(path, {signal: controller.signal,
      ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败，请稍后重试');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请重试。已提交的分析可通过已有任务继续查询。');
    throw error;
  } finally { clearTimeout(timeout); }
}

function symbol() {
  const code = el('research-symbol').value.trim().toUpperCase();
  if (!validCode(code)) throw new Error('请输入 A 股六位代码或美股代码，如 600519、NVDA');
  return code;
}

function syncAnalyzeButton() {
  const pending = submitting.has(activeSymbol) || ['pending', 'processing', 'cancel_requested'].includes(task?.status);
  el('btn-analyze').disabled = !modelReady || !activeSymbol || pending;
  el('btn-analyze').textContent = pending ? `正在分析 ${activeSymbol}…` : `✧ 生成 ${activeSymbol || ''} 分析报告`;
}

function selectReport(code) {
  if (activeSymbol === code) return;
  activeSymbol = code; ++taskViewVersion; clearTimeout(pollTimer);
  task = stockTasks[code] || null;
  el('report').hidden = true; el('report-placeholder').hidden = false;
  el('btn-resume').hidden = !task;
  el('analysis-status').textContent = `${code} · ${modelReady ? '暂无分析报告，点击生成。' : '请先手动选择并保存 AI 方式。'}`;
  syncAnalyzeButton();
  if (stockReports[code]) {
    renderReport(stockReports[code]);
    el('analysis-status').textContent = `${code} · 已恢复该股的分析报告。`;
  }
  else if (task) pollTask();
}

function rememberTask(current) {
  stockTasks[current.symbol] = current;
  try {
    localStorage.setItem(TASK_KEY, JSON.stringify(current));
    localStorage.setItem(TASKS_KEY, JSON.stringify(stockTasks));
  } catch (_) { /* 本次会话仍可保留各股任务 */ }
}

async function searchStocks(submit = false) {
  clearTimeout(searchTimer);
  if (submit && pendingResearchStock) {
    el('research-symbol').value = pendingResearchStock.symbol;
    return loadDaily();
  }
  const version = ++searchVersion, query = el('research-symbol').value.trim();
  el('search-results').hidden = true;
  el('search-status').hidden = false;
  if (!query) {el('search-status').textContent = '请输入股票名称或代码'; return;}
  el('search-status').textContent = '正在查找股票…';
  if (submit) el('btn-daily').disabled = true;
  try {
    const data = await api('/api/dsa/search?' + new URLSearchParams({q: query}));
    if (version !== searchVersion || el('research-symbol').value.trim() !== query) return;
    const items = data.items || [];
    el('search-status').hidden = !!items.length;
    el('search-status').textContent = items.length ? '' : '未找到匹配股票，请检查名称或直接输入 A 股 / 美股代码';
    el('search-results').replaceChildren(...items.map(item => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'search-result'; button.disabled = !item.supported;
      const name = document.createElement('strong'); name.textContent = item.name;
      const detail = document.createElement('span');
      detail.textContent = `${item.symbol} · ${item.market_label}${item.note ? ' · ' + item.note : ''}`;
      button.appendChild(name); button.appendChild(detail);
      button.addEventListener('click', () => {
        if (!item.supported) return;
        ++searchVersion; pendingResearchStock = item;
        NAMES[item.symbol] = item.name;
        el('research-symbol').value = `${item.name} · ${item.symbol}`;
        el('search-results').hidden = true; el('search-status').hidden = true;
      });
      return button;
    }));
    el('search-results').hidden = !items.length;
  } catch (error) {
    if (version === searchVersion) {
      el('search-status').textContent = error.message;
    }
  } finally {if (submit) el('btn-daily').disabled = !connected;}
}

function renderWatchlist() {
  let selected = '';
  try { selected = symbol(); } catch (_) { /* 输入尚未完成 */ }
  el('research-watchlist').replaceChildren(...watchlist.map(code => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'watch-item' + (selected === code ? ' active' : '');
    button.dataset.symbol = code;
    const icon = document.createElement('span'); icon.className = 'watch-symbol-icon'; icon.textContent = code.slice(0, 2);
    const text = document.createElement('span'); text.className = 'watch-item-text';
    const name = document.createElement('strong'); name.textContent = code;
    const market = document.createElement('small'); market.textContent = NAMES[code] || (/^\d/.test(code) ? 'A 股' : '美股');
    text.appendChild(name); text.appendChild(market);
    const dot = document.createElement('span'); dot.className = 'watch-active-dot'; dot.textContent = selected === code ? '·' : '';
    button.appendChild(icon); button.appendChild(text); button.appendChild(dot);
    button.addEventListener('click', () => {el('research-symbol').value = code; return loadDaily();});
    return button;
  }));
  el('watch-count').textContent = String(watchlist.length);
  el('btn-save-watch').textContent = watchlist.includes(selected) ? '★ 已在自选' : '☆ 加入自选';
}

function toggleWatch() {
  try {
    const code = symbol();
    watchlist = watchlist.includes(code) ? watchlist.filter(item => item !== code) : [...watchlist.slice(-49), code];
    try {localStorage.setItem(WATCH_KEY, JSON.stringify(watchlist));} catch (_) { /* 本次会话仍然可用 */ }
    renderWatchlist();
  } catch (error) {el('connection-status').textContent = error.message;}
}

function movingAverage(rows, period) {
  return rows.flatMap((bar, i) => i < period - 1 ? [] : [{time: bar.date,
    value: rows.slice(i - period + 1, i + 1).reduce((sum, b) => sum + b.close, 0) / period}]);
}

function visibleRows(rows, days) {
  const cutoff = new Date(rows[rows.length - 1].date + 'T00:00:00Z');
  cutoff.setUTCDate(cutoff.getUTCDate() - days);
  const first = cutoff.toISOString().slice(0, 10);
  return rows.filter(row => row.date >= first);
}

function showMetrics(bar, rows) {
  el('metrics-date').textContent = bar.date;
  for (const key of ['open', 'high', 'low']) el('metric-' + key).textContent = number(bar[key]);
  el('metric-volume').textContent = bar.volume == null ? '未提供' : Number(bar.volume).toLocaleString('zh-CN', {notation: 'compact', maximumFractionDigits: 2});
  const visible = rows.filter(row => row.date <= bar.date);
  for (const period of [5, 20]) {
    const avg = movingAverage(visible, period);
    el('metric-ma' + period).textContent = avg.length ? number(avg[avg.length - 1].value) : '—';
  }
}

function drawCandles(rows) {
  if (!globalThis.LightweightCharts) throw new Error('图表组件加载失败，请刷新页面');
  dailyStructure?.destroy(); dailyStructure=null; dailyStructureControls?.clear();
  chart?.remove();
  const L = LightweightCharts;
  const visible = visibleRows(rows, selectedDays);
  const first = visible[0].date;
  chart = L.createChart(el('daily-chart'), {
    autoSize: true, layout: {background: {color: '#ffffff'}, textColor: '#9daaa1', fontSize: 10, fontFamily: '-apple-system, sans-serif'},
    grid: {vertLines: {visible: false}, horzLines: {color: '#f0f4f1'}},
    rightPriceScale: {borderVisible: false, scaleMargins: {top: .1, bottom: .1}},
    timeScale: {borderColor: '#edf2ed', rightOffset: 3, timeVisible: false},
    localization: {locale: 'zh-CN'}, crosshair: {vertLine: {color: '#a7bab0', labelBackgroundColor: '#55816a'}, horzLine: {color: '#a7bab0', labelBackgroundColor: '#55816a'}},
  });
  const candles = chart.addSeries(L.CandlestickSeries, {upColor: '#2a9271', downColor: '#d87973', borderVisible: false, wickUpColor: '#2a9271', wickDownColor: '#d87973'});
  candles.setData(visible.map(b => ({time: b.date, open: b.open, high: b.high, low: b.low, close: b.close})));
  for (const [period, color] of [[5, '#d3a55a'], [20, '#83a2ce']]) {
    chart.addSeries(L.LineSeries, {color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false}).setData(movingAverage(rows, period).filter(row => row.time >= first));
  }
  const volume = chart.addSeries(L.HistogramSeries, {priceFormat: {type: 'volume'}, priceLineVisible: false, lastValueVisible: false}, 1);
  volume.setData(visible.filter(b => b.volume != null).map(b => ({time: b.date, value: b.volume, color: b.close >= b.open ? '#c7e4d6' : '#edcfca'})));
  const panes = chart.panes();
  if (panes[1]) panes[1].setHeight(65);
  if(globalThis.MarketStructure){
    dailyStructure=new MarketStructure.Overlay(chart,candles,L,{dark:false});
    dailyStructureControls.bind(dailyStructure,MarketStructure.dailyRows(rows,activeSymbol),{visibleFrom:first,daily:true});
  }
  chart.timeScale().fitContent();
  chart.subscribeCrosshairMove(event => {
    const current = event.seriesData?.get(candles);
    if (!current) return;
    const time = typeof current.time === 'string' ? current.time : `${current.time.year}-${String(current.time.month).padStart(2, '0')}-${String(current.time.day).padStart(2, '0')}`;
    const bar = rows.find(b => b.date === time);
    if (bar) {showMetrics(bar, rows); el('chart-cursor').textContent = `${time} · 收 ${number(bar.close)}`;}
  });
}

function paintDaily(code, payload) {
  const rows = payload.data;
  drawCandles(rows);
  el('chart-empty').hidden = true;
  const last = rows[rows.length - 1], previous = rows[rows.length - 2];
  const visible = visibleRows(rows, selectedDays);
  el('stock-name').textContent = payload.stock_name || NAMES[code] || code;
  el('stock-price').textContent = number(last.close);
  if (previous) {
    const delta = last.close - previous.close, pct = delta / previous.close * 100;
    el('stock-change').textContent = `${delta >= 0 ? '+' : ''}${delta.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
    el('stock-change').classList.toggle('negative', delta < 0);
  } else el('stock-change').textContent = '无前一日数据';
  el('stock-asof').textContent = `截至 ${last.date} · 日线快照`;
  el('daily-status').textContent = `${payload.source || 'DSA'} · ${visible[0].date} 至 ${last.date} · ${visible.length} 根日线`;
  showMetrics(last, rows);
}

function applyPeriod() {
  if (!dailyCache?.data?.length || dailyCache.symbol !== activeSymbol) return loadDaily();
  const version = ++requestVersion;
  try {
    paintDaily(activeSymbol, dailyCache);
  } catch (error) {
    el('daily-status').textContent = error.message;
  } finally {
    if (version === requestVersion) {el('btn-daily').disabled = !connected; el('chart-empty').classList.toggle('loading', false);}
  }
}

async function loadDaily(force = false) {
  pendingResearchStock = null;
  ++searchVersion; clearTimeout(searchTimer);
  el('search-results').hidden = true; el('search-status').hidden = true;
  const version = ++requestVersion;
  el('btn-daily').disabled = true;
  el('btn-retry').hidden = true;
  try {
    const code = symbol();
    selectReport(code);
    renderWatchlist();
    const cn = /^\d/.test(code);
    el('stock-name').textContent = NAMES[code] || code;
    el('stock-code').textContent = code + ' · 日线行情';
    el('stock-avatar').textContent = code.slice(0, 1);
    el('stock-market').textContent = cn ? 'A 股' : '美股';
    el('overnight-control').hidden = !cn;
    loadSignal(code);
    el('stock-currency').textContent = cn ? 'CNY' : 'USD';
    if (!force && dailyCache?.symbol === code && dailyCache.data?.length) {
      paintDaily(code, dailyCache);
      return;
    }
    el('stock-price').textContent = '—'; el('stock-change').textContent = '读取中'; el('stock-asof').textContent = '正在读取日线快照';
    for (const key of ['open', 'high', 'low', 'volume', 'ma5', 'ma20']) el('metric-' + key).textContent = '—';
    el('chart-empty').hidden = false; el('chart-empty').classList.toggle('loading', true);
    el('chart-empty-title').textContent = '正在读取 ' + code;
    el('chart-empty-note').textContent = '正在从行情服务获取日线，首次读取可能需要一些时间。';
    el('daily-status').textContent = `${code} · 正在读取日线…`;
    const data = await api('/api/dsa/history?' + new URLSearchParams({symbol: code, days: String(HISTORY_DAYS)}));
    if (version !== requestVersion) return;
    if (!data.data?.length) throw new Error('暂无可用日线，请稍后重试');
    dailyCache = {symbol: code, source: data.source || 'DSA', stock_name: data.stock_name, data: data.data};
    paintDaily(code, dailyCache);
  } catch (error) {
    if (version !== requestVersion) return;
    if (force) dailyCache = null;
    el('daily-status').textContent = error.message;
    el('stock-change').textContent = '暂无数据';
    el('stock-asof').textContent = '本次行情读取失败';
    el('chart-empty-title').textContent = '暂时无法读取行情';
    el('chart-empty-note').textContent = error.message;
    el('btn-retry').hidden = false;
  } finally {
    if (version === requestVersion) {el('btn-daily').disabled = !connected; el('chart-empty').classList.toggle('loading', false);}
  }
}

function renderReport(result) {
  const report = result?.report;
  if (!report) {el('analysis-status').textContent = '任务已结束，但未返回报告内容。可重新查询已有任务。'; return;}
  const meta = report.meta || {}, summary = report.summary || {}, details = report.details || {};
  const reportCode = String(meta.stock_code || result.stock_code || task?.symbol || '').toUpperCase();
  if (reportCode !== activeSymbol) {
    el('report').hidden = true;
    el('analysis-status').textContent = `${activeSymbol} · 报告标的与当前选择不一致，请重新查询。`;
    return;
  }
  stockReports[activeSymbol] = result;
  el('report').hidden = false; el('report-placeholder').hidden = true;
  el('report-meta').textContent = `${meta.stock_code || result.stock_code || task.symbol} · ${meta.stock_name || result.stock_name || ''} · ${meta.created_at || result.created_at || '生成时间未知'}${meta.model_used ? ' · '+meta.model_used : ''}`;
  el('report-summary').textContent = summary.analysis_summary || (typeof report === 'string' ? report : '概览未提供，请查看完整报告数据。');
  el('report-advice').textContent = [summary.action_label || summary.operation_advice, summary.trend_prediction, summary.sentiment_score != null ? `情绪评分 ${summary.sentiment_score}` : ''].filter(Boolean).join(' · ');
  const strategy = report.strategy || {};
  el('report-strategy').replaceChildren(...[['理想买入价', strategy.ideal_buy], ['第二买入价', strategy.secondary_buy], ['止损价', strategy.stop_loss], ['止盈价', strategy.take_profit]].filter(([, value]) => value != null).map(([name, value]) => {
    const p = document.createElement('p'); p.textContent = `${name}：${value}`; return p;
  }));
  el('report-news').textContent = details.news_content || details.empty_news_disclosure || '本次报告未提供新闻摘要。';
  el('report-full').textContent = JSON.stringify(report, null, 2);
}

async function pollTask() {
  clearTimeout(pollTimer);
  const current = task;
  const viewVersion = taskViewVersion;
  if (!current) return;
  el('btn-resume').hidden = true;
  try {
    const data = await api('/api/dsa/status?' + new URLSearchParams({task_id: current.task_id}));
    if (task !== current || viewVersion !== taskViewVersion) return;
    current.status = data.status; rememberTask(current);
    const labels = {pending: '等待分析', processing: '正在分析', cancel_requested: '正在取消', completed: '分析完成', failed: '分析失败', cancelled: '已取消'};
    el('analysis-status').textContent = `${current.symbol} · ${labels[data.status] || data.status}${data.progress != null ? ' · ' + data.progress + '%' : ''}${data.error ? ' · ' + data.error : ''}`;
    if (['pending', 'processing', 'cancel_requested'].includes(data.status)) {pollTimer = setTimeout(pollTask, 3000);}
    else {el('btn-resume').hidden = false; if (data.status === 'completed') renderReport(data.result);}
    syncAnalyzeButton();
  } catch (error) {
    if (task !== current || viewVersion !== taskViewVersion) return;
    el('analysis-status').textContent = `${current.symbol} · ${error.message}`;
    syncAnalyzeButton(); el('btn-resume').hidden = false;
  }
}

async function analyze() {
  if (!modelReady || !activeSymbol || submitting.has(activeSymbol)) return;
  const code = activeSymbol;
  submitting.add(code);
  delete stockReports[code];
  el('btn-analyze').disabled = true; clearTimeout(pollTimer); el('btn-resume').hidden = true; el('report').hidden = true;
  try {
    el('analysis-status').textContent = `${code} · 正在提交分析…`;
    const data = await api('/api/dsa/analysis', {symbol: code});
    const current = {symbol: code, task_id: data.task_id, status: data.status || 'pending'};
    rememberTask(current);
    if (activeSymbol === code) {task = current; await pollTask();}
  } catch (error) {if (activeSymbol === code) el('analysis-status').textContent = error.message;}
  finally {submitting.delete(code); syncAnalyzeButton();}
}

async function init() {
  el('workspace-date').textContent = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric'}).format(new Date());
  try {
    const saved = JSON.parse(localStorage.getItem(WATCH_KEY) || 'null');
    if (Array.isArray(saved)) watchlist = [...new Set(saved.filter(validCode))].slice(0, 50);
  } catch (_) { /* 使用默认自选 */ }
  const queryCode = new URLSearchParams(location.search).get('symbol')?.toUpperCase();
  if (validCode(queryCode)) el('research-symbol').value = queryCode;
  try {
    const saved = JSON.parse(localStorage.getItem(TASKS_KEY) || '{}');
    const legacy = JSON.parse(localStorage.getItem(TASK_KEY) || 'null');
    if (legacy?.symbol) saved[legacy.symbol] = legacy;
    for (const [code, current] of Object.entries(saved).slice(-50)) {
      if (validCode(code) && current?.symbol === code && /^[A-Za-z0-9_-]{1,128}$/.test(current.task_id)) stockTasks[code] = current;
    }
  } catch (_) { /* 无可恢复的任务 */ }
  renderWatchlist();
  try {
    const data = await api('/api/dsa/config');
    connected = data.connected === true; serverModelReady=data.model_ready===true;
    modelReady=connected&&serverModelReady&&!!chosenAIBackend&&data.backend===chosenAIBackend;
    el('service-dot').classList.toggle('connected', connected);
    el('service-label').textContent = connected ? '行情服务已连接' : '行情服务未连接';
    el('connection-status').textContent = connected ? '行情服务已连接 · 支持 A 股与美股日线 · 数据获取后显示实际日期' : data.error || '行情服务尚未连接，请启动本地服务后刷新。';
    el('btn-daily').disabled = !connected; el('btn-analyze').disabled = !modelReady;
    el('model-badge').textContent = modelReady ? '已选择' : '请手动选择';
    if (modelReady) el('analysis-status').textContent = '点击生成报告，汇总当前标的的技术走势与市场信息。';
    if (connected) loadAISettings();
    if (connected) await loadDaily();
    else {el('chart-empty-title').textContent = '等待行情服务连接'; el('chart-empty-note').textContent = '启动本地服务后刷新页面，即可读取真实行情。';}
  } catch (error) {el('connection-status').textContent = error.message; el('chart-empty-title').textContent = '暂时无法连接服务'; el('chart-empty-note').textContent = error.message;}
}

el('search-form').addEventListener('submit', event => {event.preventDefault(); return searchStocks(true);});
el('btn-daily').addEventListener('click', event => {event.preventDefault(); return searchStocks(true);});
el('research-symbol').addEventListener('input', () => {
  pendingResearchStock = null;
  ++searchVersion; clearTimeout(searchTimer);
  el('search-results').hidden = true; el('search-status').hidden = true;
  searchTimer = setTimeout(() => searchStocks(), 300);
});
el('btn-retry').addEventListener('click', () => loadDaily(true));
el('btn-save-watch').addEventListener('click', toggleWatch);
el('btn-chart-reset').addEventListener('click', () => chart?.timeScale().fitContent());
el('period-controls').addEventListener('click', event => {
  const button = event.target.closest('button[data-days]');
  if (!button || !connected) return;
  selectedDays = Number(button.dataset.days);
  for (const child of el('period-controls').querySelectorAll('button')) {child.classList.toggle('selected', child === button); child.setAttribute('aria-pressed', String(child === button));}
  return applyPeriod();
});
el('btn-analyze').addEventListener('click', analyze);
el('btn-resume').addEventListener('click', pollTask);

el('history-samples').addEventListener('toggle', async () => {
  if (!el('history-samples').open) return;
  el('history-samples-status').textContent = '正在读取已导入的历史样本…';
  try {
    const data = await api('/api/history-samples');
    const rows = [
      ...(data.daily || []).map(row => `${row.symbol} · 日线 · ${row.first} 至 ${row.last} · ${row.rows.toLocaleString()} 条 · ${row.source}`),
      ...(data.minute || []).map(row => `${row.symbol} · 分钟样本 ${(row.months || []).join('、')} · ${row.rows.toLocaleString()} 条 · 观测 ${row.observed_days} 天，完整常规盘 ${row.complete_regular_days} 天 · ${row.source}`),
    ];
    el('history-samples-list').replaceChildren(...rows.map(text => {
      const p = document.createElement('p'); p.textContent = text; return p;
    }));
    el('history-samples-status').textContent = rows.length
      ? '仅供历史研究。分钟缺口不补造；入场回测还需足够完整交易日热身，日线统计不等于盘中策略收益。'
      : '尚未导入历史样本。';
  } catch (error) {el('history-samples-status').textContent = error.message;}
});

async function loadSignal(code) {
  const version=++signalVersion;
  clearTimeout(signalExpiryTimer);
  el('signal-action').textContent='观望';
  el('signal-daily').textContent='日线待确认';el('signal-state').textContent='盘中待确认';el('signal-volume').textContent='量能待核';
  el('signal-action').classList.toggle('actionable',false);
  el('signal-reason').textContent='正在读取日线量价与盘中结构…';
  for(const key of ['entry','stop','target','rr']) el('signal-'+key).textContent='—';
  el('signal-updated').textContent='';
  el('signal-evidence').replaceChildren();
  el('btn-signal-refresh').disabled=true;
  try {
    code=code||symbol();
    const qs=new URLSearchParams({symbol:code,accept_overnight:el('accept-overnight').checked?'1':'0'});
    const data=await api('/api/market-state?'+qs);
    if(version!==signalVersion) return;
    const daily=data.daily_context||{},state=data.intraday_state||{};
    el('signal-daily').textContent='日线 · '+(daily.label||'待确认');
    el('signal-state').textContent='盘中 · '+(state.label||'待确认');
    el('signal-volume').textContent=state.bar_rvol!=null?'同时段量比 '+number(state.bar_rvol)+'×':'量能待核';
    el('signal-action').textContent=data.actionable?'进场条件满足 · '+data.advice:data.E_band?'预案 · 等待到价':'观望';
    el('signal-action').classList.toggle('actionable',!!data.actionable);
    el('signal-reason').textContent=data.reason||'等待确认';
    el('signal-entry').textContent=data.E_band?data.E_band.map(number).join(' – '):'—';
    el('signal-stop').textContent=number(data.stop);
    el('signal-target').textContent=number(data.target);
    el('signal-rr').textContent=data.worst_R1!=null?number(data.worst_R1)+' : 1':'—';
    const evidence=[...(daily.evidence||[]),...(state.evidence||[]),data.target_note,data.execution_note].filter(Boolean);
    el('signal-evidence').replaceChildren(...evidence.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    el('signal-updated').textContent=`${data.session?.phase||''} · 判断 ${data.checked_at_bj||'—'} · 行情 ${data.quote_time_bj||'时间未知'}`;
    if(data.valid_until_bj){
      const delay=new Date(data.valid_until_bj).getTime()-Date.now();
      signalExpiryTimer=setTimeout(()=>{
        if(version!==signalVersion)return;
        el('signal-action').textContent='观望 · 计划已过期';
        el('signal-action').classList.toggle('actionable',false);
        el('signal-reason').textContent='已超过该计划有效期，请刷新判断。';
        for(const key of ['entry','stop','target','rr'])el('signal-'+key).textContent='—';
      },Math.max(0,delay));
    }
  }catch(error){if(version===signalVersion)el('signal-reason').textContent=error.message;}
  finally{if(version===signalVersion)el('btn-signal-refresh').disabled=false;}
}

function paintAIFields(){
  const selected=el('ai-backend').value;
  const local=['codex_cli','claude_code_cli'].includes(selected);
  el('ai-api-fields').hidden=selected!=='litellm';el('ai-local-note').hidden=!local;
  el('btn-ai-save').disabled=!['litellm','codex_cli','claude_code_cli'].includes(selected);
  modelReady=connected&&serverModelReady&&!!chosenAIBackend&&selected===chosenAIBackend&&aiSettings?.backend===selected;
  syncAnalyzeButton();
  el('model-badge').textContent=modelReady?'已选择':chosenAIBackend?'待核验':'请手动选择';
  if(local&&aiSettings){
    const installed=aiSettings.local_available?.[el('ai-backend').value];
    el('ai-settings-status').textContent=installed?'已检测到该 CLI；首次使用需先在本机完成登录。保存选择后，点击生成才会调用。':'当前未确认该 CLI 可用，需先在服务所在电脑完成安装与登录。';
  }
}

async function loadAISettings(){
  try{
    aiSettings=await api('/api/dsa/ai-settings');
    el('ai-backend').value=chosenAIBackend;
    el('ai-api-url').value=aiSettings.api_base_url||'';
    el('ai-model').value=aiSettings.model||'';
    el('ai-key').placeholder=aiSettings.api_key_set?'已保存密钥，留空保留':'填写 API Key，仅保存在本机服务';
    paintAIFields();
  }catch(error){el('ai-settings-status').textContent=error.message;}
}

async function saveAISettings(){
  el('btn-ai-save').disabled=true;el('ai-settings-status').textContent='正在保存…';
  try{
    aiSettings=await api('/api/dsa/ai-settings',{backend:el('ai-backend').value,
      api_base_url:el('ai-api-url').value,model:el('ai-model').value,api_key:el('ai-key').value});
    el('ai-key').value='';
    const status=await api('/api/dsa/config');serverModelReady=!!status.model_ready;
    chosenAIBackend=el('ai-backend').value;
    try{localStorage.setItem('market_studio_ai_choice',chosenAIBackend);}catch(_){}
    modelReady=serverModelReady&&(status.backend||aiSettings.backend)===chosenAIBackend;
    syncAnalyzeButton();el('model-badge').textContent=modelReady?'已配置':'待配置';
    el('ai-settings-status').textContent=modelReady?'已保存，可手动生成报告。':'已保存，但后端尚不可用，请核对模型配置或本机 CLI 登录状态。';
    el('analysis-status').textContent=modelReady?'已选择 '+(aiSettings.backend==='litellm'?'API':aiSettings.backend)+'，点击生成报告后才会调用。':'AI 配置尚未就绪。';
  }catch(error){el('ai-settings-status').textContent=error.message;}
  finally{el('btn-ai-save').disabled=false;}
}

el('btn-signal-refresh').addEventListener('click',()=>loadSignal());
el('accept-overnight').addEventListener('change',()=>loadSignal());
el('ai-backend').addEventListener('change',paintAIFields);
el('btn-ai-save').addEventListener('click',saveAISettings);
init();
