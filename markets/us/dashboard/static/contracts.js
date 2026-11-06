(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const groups = [...document.querySelectorAll('[data-group]')];
  let items = [], selected = '', group = 'all', busy = false, paused = false;
  let exchange = 'okx', source = 'OKX', requestId = 0, activeController = null;
  let timer = null, failed = false, serverError = '', generated = null, status = 'idle', total = 0;
  let chart = null, series = null, chartKey = '', freshnessKey = '';
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
    const structure = row.structure_fresh && row.candle_close_at === Math.floor(Date.now() / 14400000) * 14400000;
    const valid = fresh && structure && !failed && !serverError;
    return {...row, fresh, valid, direction: valid ? row.direction : 'observe', label: valid ? row.label : '待确认'};
  }

  function renderChart(row) {
    const rows = row?.candles || [];
    el('chart-status').textContent = rows.length ? `${rows.length} 根已收盘 K 线 · 时间显示北京时间` : '暂无完整的合约 K 线';
    if (!el('chart-details').open || !window.LightweightCharts) return;
    const key = `${row.symbol}:${row.candle_close_at}`;
    if (key === chartKey) return;
    const L = window.LightweightCharts;
    if (!chart) {
      chart = L.createChart(el('contract-chart'), {autoSize:true,
        layout:{background:{color:'#182126'},textColor:'#8fa5b2'},
        grid:{vertLines:{color:'#26333b'},horzLines:{color:'#26333b'}},
        timeScale:{timeVisible:true,secondsVisible:false,
          tickMarkFormatter:seconds => new Date(seconds * 1000).toLocaleString('zh-CN', {
            timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})},
        localization:{timeFormatter: seconds => time(seconds * 1000)},
      });
      series = chart.addSeries(L.CandlestickSeries, {upColor:'#65c4aa',downColor:'#ef8b87',
        borderVisible:false,wickUpColor:'#65c4aa',wickDownColor:'#ef8b87'});
    }
    series.applyOptions({priceFormat:{type:'price',precision:row.price < 1 ? 6 : 2,minMove:row.price < 1 ? .000001 : .01}});
    series.setData(rows.map(candle => ({...candle,time:candle.time / 1000})));
    chart.timeScale().fitContent(); chartKey = key;
  }

  function renderDetail(rows) {
    const row = rows.find(item => item.symbol === selected) || rows[0];
    if (!row) return;
    selected = row.symbol;
    el('detail-symbol').textContent = row.symbol;
    el('detail-name').textContent = `${row.name} · ${row.group === 'stock' ? '股票合约' : '加密货币'} · ${row.source} USDT 永续`;
    el('detail-source').textContent = `${row.source} USDT 永续`;
    el('detail-direction').textContent = row.label;
    el('detail-direction').className = `direction-badge ${row.direction}`;
    el('detail-price').textContent = price(row.price);
    el('detail-time').textContent = `报价 ${time(row.quote_at)}${row.fresh ? '' : ' · 已过期 / 缺失'}`;
    const reasons = row.valid ? row.reasons : ['方向暂停：行情过期、结构未齐或连接异常', ...(row.reasons || [])];
    el('detail-reasons').replaceChildren(...reasons.map(text => {
      const li = document.createElement('li'); li.textContent = text; return li;
    }));
    el('detail-invalidation').textContent = row.valid ? row.invalidation : '等待新鲜报价与完整 4 小时结构后重新判断。';
    el('detail-ema20').textContent = price(row.ema20);
    el('detail-ema50').textContent = price(row.ema50);
    el('detail-closed-at').textContent = `结构收盘时间：${time(row.candle_close_at)}`;
    renderChart(row);
  }

  function render() {
    const rows = items.map(view);
    freshnessKey = rows.map(row => `${row.symbol}:${row.valid}`).join(',');
    const counts = {bullish:0,bearish:0,observe:0};
    for (const row of rows) counts[row.direction]++;
    for (const key of Object.keys(counts)) el(`${key}-count`).textContent = rows.length ? counts[key] : '—';
    const query = el('contract-search').value.trim().toLowerCase();
    const direction = el('direction-filter').value;
    const filtered = rows.filter(row => (group === 'all' || row.group === group)
      && (direction === 'all' || row.direction === direction)
      && `${row.symbol} ${row.name}`.toLowerCase().includes(query));
    el('contract-rows').replaceChildren(...filtered.map(row => {
      const tr = document.createElement('tr');
      tr.className = row.symbol === selected ? 'selected' : '';
      const changeClass = row.change_pct > 0 ? 'bullish' : row.change_pct < 0 ? 'bearish' : '';
      tr.innerHTML = `<td><button type="button">${escape(row.symbol)}</button><small>${escape(row.name)}</small></td>
        <td><span class="direction-badge ${row.direction}">${escape(row.label)}</span><small>4 小时结构</small></td>
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
    el('board-loading').textContent = failed || serverError ? '合约行情暂不可用，请稍后重试。' : '首次读取合约与 4 小时 K 线，稍候…';
    groups.forEach(button => {
      button.classList.toggle('selected', button.dataset.group === group);
      button.setAttribute('aria-pressed', String(button.dataset.group === group));
    });
    renderDetail(rows);
    el('source-name').textContent = `${source} · USDT 永续`;
    el('source-docs').href = exchange === 'okx' ? 'https://www.okx.com/docs-v5/en/' : 'https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market';
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
      const response = await fetch(`/api/contracts?exchange=${exchange}`, {signal:controller.signal,cache:'no-store'});
      if (!response.ok) throw Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (current !== requestId) return;
      if (!Array.isArray(payload.items)) throw Error('无效的行情响应');
      items = payload.items; generated = payload.generated_at; status = payload.status; total = payload.total ?? items.length;
      serverError = payload.error || ''; failed = false;
      source = payload.source || (exchange === 'okx' ? 'OKX' : 'Bitget');
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
  el('refresh-interval').addEventListener('change', schedule);
  el('refresh-now').addEventListener('click', load);
  el('exchange-source').value = exchange;
  el('exchange-source').addEventListener('change', () => {
    exchange = el('exchange-source').value; source = exchange === 'okx' ? 'OKX' : 'Bitget';
    items = []; selected = ''; chartKey = ''; generated = null; total = 0; failed = false; serverError = '';
    if (series) series.setData([]);
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
