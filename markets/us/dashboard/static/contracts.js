(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const groups = [...document.querySelectorAll('[data-group]')];
  let items = [], selected = '', group = 'all', busy = false, paused = false;
  let minutes = 240;
  const periodLabel = () => minutes === 240 ? '4 小时' : `${minutes} 分钟`;
  let exchange = 'okx', source = 'OKX', requestId = 0, activeController = null;
  const sources = {okx:'OKX',binance:'Binance',bitget:'Bitget'};
  if (typeof location !== 'undefined') {
    const requested = /(?:\?|&)exchange=(okx|binance|bitget)(?:&|$)/.exec(location.search);
    if (requested) {exchange=requested[1];source=sources[exchange];}
  }
  const docs = {okx:'https://www.okx.com/docs-v5/en/',binance:'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data',bitget:'https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market'};
  let timer = null, failed = false, serverError = '', generated = null, status = 'idle', total = 0;
  let chartKey = '', freshnessKey = '';
  const number = (value, digits = 2) => value == null || !Number.isFinite(value) ? '—'
    : value.toLocaleString('en-US', {maximumFractionDigits: digits, minimumFractionDigits: 0});
  const price = value => number(value, value != null && Math.abs(value) < 1 ? 6 : 2);
  const percent = value => value == null ? '—' : `${value > 0 ? '+' : ''}${number(value, 4)}%`;
  const money = value => value == null ? '—' : Math.abs(value) >= 1e9 ? `${number(value / 1e9)}B`
    : Math.abs(value) >= 1e6 ? `${number(value / 1e6)}M` : `${number(value)}`;
  const time = value => value == null ? '—' : new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false});
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));

  function view(row) {
    const age = Date.now() - row.quote_at;
    const fresh = row.quote_at != null && row.quote_fresh && age >= -5000 && age <= 30000;
    const structure = row.structure_fresh && row.candle_close_at === Math.floor(Date.now() / (minutes * 60000)) * (minutes * 60000);
    const valid = fresh && structure && !failed && !serverError;
    return {...row, fresh, valid, direction: valid ? row.direction : 'observe', label: valid ? row.label : '待确认'};
  }

  const chartView=window.MarketChart?new window.MarketChart(el('contract-chart'),el('chart-legend'),el('contract-structure')):null;
  function renderChart(row) {
    const rows=row?.chart_candles||row?.candles||[];
    const forming=rows.at(-1)?.complete===false;
    el('chart-status').textContent=rows.length?`${rows.length} 根 K 线 · ${forming?'末根形成中，未参与信号':'当前无形成中 K 线'} · 下方量柱：基础币 · 采集约 10 秒更新，非逐笔推送 · 北京时间`:'暂无完整合约 K 线与成交量';
    if(el('chart-details').open&&chartView)chartView.render(row);
  }

  function renderDetail(rows) {
    const row = rows.find(item => item.symbol === selected) || rows[0];
    if (!row) return;
    selected = row.symbol;
    el('detail-symbol').textContent = row.symbol;
    el('detail-name').textContent = `${row.name} · ${row.category_label || (row.group === 'stock' ? '股票合约' : '加密货币')} · ${row.source} USDT 永续`;
    el('detail-source').textContent = `${row.source} USDT 永续`;
    el('detail-direction').textContent = row.label;
    el('detail-direction').className = `direction-badge ${row.direction}`;
    el('detail-price').textContent = price(row.price);
    el('detail-time').textContent = `报价 ${time(row.quote_at)}${row.fresh ? '' : ' · 已过期 / 缺失'}`;
    const reasons = row.valid ? row.reasons : ['方向暂停：行情过期、结构未齐或连接异常', ...(row.reasons || [])];
    el('detail-reasons').replaceChildren(...reasons.map(text => {
      const li = document.createElement('li'); li.textContent = text; return li;
    }));
    el('detail-invalidation').textContent = row.valid ? row.invalidation : `等待新鲜报价与完整 ${periodLabel()}结构后重新判断。`;
    el('detail-ema20').textContent = price(row.ema20);
    el('detail-ema50').textContent = price(row.ema50);
    el('detail-closed-at').textContent = `结构收盘时间：${time(row.candle_close_at)}`;
    renderChart(row);
  }

  function render() {
    el('chart-summary').textContent = `查看该合约 ${periodLabel()} K 线与成交量`;
    el('contract-chart').setAttribute('aria-label', `${periodLabel()} K 线`);
    const rows = items.map(view);
    freshnessKey = rows.map(row => `${row.symbol}:${row.valid}`).join(',');
    const counts = {bullish:0,bearish:0,observe:0};
    for (const row of rows) counts[row.direction]++;
    for (const key of Object.keys(counts)) el(`${key}-count`).textContent = rows.length ? counts[key] : '—';
    const query = el('contract-search').value.trim().toLowerCase();
    const direction = el('direction-filter').value;
    const category = el('category-filter').value;
    const filtered = rows.filter(row => (group === 'all' || row.group === group)
      && (category === 'all' || row.category === category)
      && (direction === 'all' || row.direction === direction)
      && `${row.symbol} ${row.name}`.toLowerCase().includes(query));
    el('contract-rows').replaceChildren(...filtered.map(row => {
      const tr = document.createElement('tr');
      tr.className = row.symbol === selected ? 'selected' : '';
      const changeClass = row.change_pct > 0 ? 'bullish' : row.change_pct < 0 ? 'bearish' : '';
      tr.innerHTML = `<td><button type="button">${escape(row.symbol)}</button><small>${escape(row.name)} · ${escape(row.category_label || '')}</small></td>
        <td><span class="direction-badge ${row.direction}">${escape(row.label)}</span><small>${periodLabel()}结构</small></td>
        <td><strong>${price(row.price)}</strong><small>${escape(row.source)} · ${escape(time(row.quote_at))}</small></td>
        <td class="${changeClass}">${percent(row.change_pct)}</td>
        <td class="level support">${price(row.support)}<small>近 20 根最低价</small></td>
        <td class="level resistance">${price(row.resistance)}<small>近 20 根最高价</small></td>
        <td>${money(row.open_interest_usdt)}<small>USDT · 持仓量 × 标记价</small></td>
        <td>${percent(row.funding_pct)}<small>当前周期 / 非年化</small></td>
        <td>${money(row.volume_usdt)}<small>USDT${row.volume_estimated ? ' · 基础币量 × 现价估算' : ''}</small></td>
        <td class="${row.valid ? '' : 'stale'}">${row.error ? '数据不足' : row.valid ? '已更新' : '待确认'}<small>${escape(row.error || (!row.fresh ? '报价过期 / 缺失' : !row.structure_fresh ? '结构待确认' : failed || serverError ? '连接异常' : '结构已确认'))}</small></td>`;
      tr.addEventListener('click', () => {selected = row.symbol; render();});
      return tr;
    }));
    el('empty-results').hidden = filtered.length > 0 || items.length === 0;
    el('board-loading').hidden = items.length > 0;
    el('board-loading').textContent = failed || serverError ? '合约行情暂不可用，请稍后重试。' : `首次读取合约与 ${periodLabel()} K 线，稍候…`;
    groups.forEach(button => {
      button.classList.toggle('selected', button.dataset.group === group);
      button.setAttribute('aria-pressed', String(button.dataset.group === group));
    });
    renderDetail(rows);
    el('source-name').textContent = `${source} · USDT 永续`;
    el('source-docs').href = docs[exchange];
    el('paper-link').href = `/paper?exchange=${exchange === 'binance' ? 'binance' : 'okx'}`;
    el('paper-link').textContent = exchange === 'binance' ? '币安自动模拟 ↗' : '欧易自动模拟 ↗';
    el('workspace-date').textContent = `北京 ${time(Date.now())}`;
    const fresh = rows.filter(row => row.fresh).length;
    if (!failed) {
      el('connection-status').textContent = serverError ? `行情连接异常：${serverError}`
        : `${status === 'running' ? '正在更新' : '行情快照'} · ${fresh}/${total} 个报价在 30 秒内 · 采集 ${time(generated)}${paused ? ' · 自动刷新已暂停' : ''}`;
      el('connection-status').className = serverError || (rows.length > 0 && !fresh) ? 'error' : '';
    }
  }

  function schedule() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (!paused) timer = setTimeout(load, status === 'running' ? 1000 : Number(el('refresh-interval').value) * 1000);
  }

  async function load(force = false) {
    if (busy && force !== true) return;
    const current = ++requestId;
    if (activeController) activeController.abort();
    busy = true; el('refresh-now').disabled = true;
    if (timer !== null) clearTimeout(timer);
    timer = null;
    const controller = new AbortController();
    activeController = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(`/api/contracts?exchange=${exchange}&period=${minutes}`, {signal:controller.signal,cache:'no-store'});
      if (!response.ok) throw Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (current !== requestId) return;
      if (!Array.isArray(payload.items)) throw Error('无效的行情响应');
      items = payload.items; generated = payload.generated_at; status = payload.status; total = payload.total ?? items.length;
      serverError = payload.error || ''; failed = false;
      source = payload.source || sources[exchange];
      if (!selected && items.length) selected = items[0].symbol;
      render();
    } catch (error) {
      if (current !== requestId) return;
      failed = true; status = 'idle';
      el('connection-status').textContent = `读取失败：${error.message} · 保留上次快照，方向暂停`;
      el('connection-status').className = 'error'; render();
    } finally {
      clearTimeout(timeout);
      if (current === requestId) {busy = false; el('refresh-now').disabled = false; schedule();}
    }
  }

  groups.forEach(button => button.addEventListener('click', () => {group = button.dataset.group; render();}));
  el('contract-search').addEventListener('input', render);
  el('direction-filter').addEventListener('change', render);
  el('category-filter').value = 'all';
  el('category-filter').addEventListener('change', () => {if (el('category-filter').value !== 'all') group = 'crypto';render();});
  el('refresh-interval').addEventListener('change', schedule);
  el('refresh-now').addEventListener('click', load);
  el('signal-period').value = '240';
  el('signal-period').addEventListener('change', () => {
    minutes = Number(el('signal-period').value);
    items=[]; selected=''; chartKey=''; generated=null; total=0; failed=false; serverError='';
    if(chartView)chartView.clear();
    for(const id of ['detail-price','detail-ema20','detail-ema50','detail-time','detail-closed-at'])el(id).textContent='—';
    el('detail-symbol').textContent='等待行情';el('detail-direction').textContent='待确认';
    el('detail-direction').className='direction-badge';el('detail-reasons').replaceChildren();
    el('detail-invalidation').textContent='等待所选周期的完整结构';
    render();return load(true);
  });
  el('exchange-source').value = exchange;
  el('exchange-source').addEventListener('change', () => {
    exchange = el('exchange-source').value; source = sources[exchange];
    if (exchange === 'binance' && group === 'stock') group = 'crypto';
    items = []; selected = ''; chartKey = ''; generated = null; total = 0; failed = false; serverError = '';
    if(chartView)chartView.clear();
    for (const id of ['detail-price','detail-ema20','detail-ema50','detail-time','detail-closed-at']) el(id).textContent = '—';
    el('detail-symbol').textContent = '等待行情'; el('detail-direction').textContent = '待确认';
    el('detail-direction').className = 'direction-badge';
    el('detail-source').textContent = `${source} USDT 永续`;
    el('detail-name').textContent = '正在切换行情来源';
    el('detail-reasons').replaceChildren(); el('detail-invalidation').textContent = '等待新来源的完整结构';
    render(); return load(true);
  });
  el('pause-refresh').addEventListener('click', () => {
    paused = !paused; el('pause-refresh').textContent = paused ? '继续' : '暂停';
    el('pause-refresh').setAttribute('aria-pressed', String(paused)); schedule(); render();
  });
  el('chart-details').addEventListener('toggle', () => {if (el('chart-details').open) renderDetail(items.map(view));});
  setInterval(() => {
    const key = items.map(view).map(row => `${row.symbol}:${row.valid}`).join(',');
    if (key !== freshnessKey) render();
    el('workspace-date').textContent = `北京 ${time(Date.now())}`;
  }, 1000);
  load();
})();
