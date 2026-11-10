(() => {
  const PIN_KEY = 'market_studio_tomorrow_obs';
  const WATCH_KEY = 'market_studio_watchlist';
  const el = (id) => document.getElementById(id);
  let radar = null;
  let filter = 'all';
  let pins = {signal_date: null, items: []};

  el('workspace-date').textContent = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', weekday: 'short',
  }).format(new Date());

  function loadPins() {
    try {
      const raw = JSON.parse(localStorage.getItem(PIN_KEY) || 'null');
      if (raw && Array.isArray(raw.items)) pins = raw;
    } catch (_) { pins = {signal_date: null, items: []}; }
  }

  function savePins() {
    try { localStorage.setItem(PIN_KEY, JSON.stringify(pins)); } catch (_) { /* 会话内仍可用 */ }
  }

  function pinCodes() {
    return new Set((pins.items || []).map((x) => x.code));
  }

  function syncResearchWatch(code) {
    try {
      const saved = JSON.parse(localStorage.getItem(WATCH_KEY) || 'null');
      let list = Array.isArray(saved) ? saved.slice() : [];
      if (!list.includes(code)) list = [...list.filter(Boolean).slice(-49), code];
      localStorage.setItem(WATCH_KEY, JSON.stringify(list));
    } catch (_) { /* 研究页自选同步失败不影响盯盘 */ }
  }

  function allRows() {
    if (!radar) return [];
    return [...(radar.strategy_candidates || []), ...(radar.watch_pool || [])];
  }

  function isPinned(code) {
    return pinCodes().has(code);
  }

  function togglePin(row, force) {
    const exists = isPinned(row.code);
    const shouldPin = force == null ? !exists : !!force;
    if (shouldPin && !exists) {
      pins.items = [{
        code: row.code, name: row.name, category: row.category,
        rule_labels: row.rule_labels || [], close: row.close,
      }, ...pins.items.filter((x) => x.code !== row.code)].slice(0, 40);
      pins.signal_date = radar?.signal_date || pins.signal_date;
      syncResearchWatch(row.code);
    } else if (!shouldPin && exists) {
      pins.items = pins.items.filter((x) => x.code !== row.code);
    }
    savePins();
    renderPins();
    renderBoard();
  }

  function openResearch(code) {
    location.href = '/research?symbol=' + encodeURIComponent(code);
  }

  function renderPins() {
    const box = el('pin-list');
    const items = pins.items || [];
    el('pin-count').textContent = String(items.length);
    el('pinned-stat').textContent = String(items.length);
    if (!items.length) {
      box.innerHTML = '<p class="sidebar-hint" style="padding:8px 12px">还没有盯住的票。<br>在右侧点「盯住」。</p>';
      return;
    }
    box.replaceChildren(...items.map((row) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pin-item';
      btn.innerHTML = `<span class="pin-code">${row.code}</span><span><strong>${row.name || row.code}</strong><small>${(row.rule_labels || []).join(' · ') || '明日观察'}</small></span>`;
      btn.addEventListener('click', () => openResearch(row.code));
      return btn;
    }));
  }

  function filteredRows() {
    const rows = allRows();
    if (filter === 'strategy') return rows.filter((x) => x.category === '策略候选');
    if (filter === 'watch') return rows.filter((x) => x.category !== '策略候选');
    if (filter === 'pinned') return rows.filter((x) => isPinned(x.code));
    return rows;
  }

  function changeHtml(pct) {
    if (pct == null || Number.isNaN(Number(pct))) return '<span class="flat">—</span>';
    const n = Number(pct);
    const cls = n > 0 ? 'up' : n < 0 ? 'down' : 'flat';
    const text = `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
    return `<span class="${cls}">${text}</span>`;
  }

  function planBlock(row) {
    const s = row.sniper || row.trade_plan?.sniper;
    if (!s) return '<p class="radar-plan-note">暂无次日点位（日线不足）</p>';
    const checks = (row.checklist || row.trade_plan?.checklist || []).map((c) => {
      const cls = c.status === '满足' ? '' : c.status === '注意' ? 'warn' : 'bad';
      return `<i class="${cls}">${c.status} ${c.item}</i>`;
    }).join('');
    return `
      <div class="radar-plan">
        <div><span>理想买≈MA5</span><strong>${s.ideal_buy}</strong></div>
        <div><span>次优买≈MA10</span><strong>${s.secondary_buy}</strong></div>
        <div><span>止损参考</span><strong>${s.stop_loss}</strong></div>
        <div><span>目标参考</span><strong>${s.take_profit}</strong></div>
      </div>
      <div class="radar-check">${checks}</div>
      <p class="radar-plan-note">${row.summary || row.trade_plan?.summary || ''}</p>`;
  }

  function renderBoard() {
    const list = el('board-list');
    const rows = filteredRows();
    if (!rows.length) {
      list.innerHTML = '<p class="radar-empty">这一栏暂时没有股票。可切换筛选，或点右上角刷新名单。</p>';
      return;
    }
    list.replaceChildren(...rows.map((row, idx) => {
      const pinned = isPinned(row.code);
      const item = document.createElement('article');
      item.className = 'radar-row radar-row-rich';
      const badge = row.category === '策略候选'
        ? '<span class="radar-badge priority">优先</span>'
        : '<span class="radar-badge watch">观察</span>';
      const ready = row.plan_ready ? '<span class="radar-badge priority">计划可观察</span>' : '';
      const tags = (row.rule_labels || []).map((x) => `<i>${x}</i>`).join('');
      item.innerHTML = `
        <div class="radar-row-top">
          <span class="rank">${idx + 1}</span>
          <button type="button" class="radar-stock" data-act="open">
            <strong>${badge}${ready}${row.name || row.code}</strong>
            <small>${row.code}${row.board ? ' · ' + row.board : ''}</small>
          </button>
          <div class="radar-signals">${tags || '<i>信号</i>'}</div>
          <div class="radar-px"><strong>${row.close ?? '—'}</strong>${changeHtml(row.change_pct)}</div>
          <div class="radar-ops">
            <button type="button" class="outline-button ${pinned ? 'pinned' : ''}" data-act="pin">${pinned ? '已盯' : '盯住'}</button>
            <button type="button" class="outline-button" data-act="open">看盘</button>
          </div>
        </div>
        ${planBlock(row)}`;
      item.querySelectorAll('[data-act="open"]').forEach((node) => node.addEventListener('click', () => openResearch(row.code)));
      item.querySelector('[data-act="pin"]').addEventListener('click', () => togglePin(row));
      return item;
    }));
  }

  function paint(data) {
    radar = data;
    el('signal-date').textContent = data.signal_date || '—';
    el('observe-headline').textContent = (data.signal_date ? `基于 ${data.signal_date} 收盘` : '下一交易日') + ' · 明天盯这些';
    el('market-label').textContent = data.market_label || '—';
    el('strategy-count').textContent = String(data.strategy_count ?? 0);
    el('watch-count').textContent = String(data.watch_count ?? 0);
    if (data.signal_date && pins.signal_date && pins.signal_date !== data.signal_date && (pins.items || []).length) {
      // 新信号日到来时保留手动盯住，但提示日期变化
      pins.signal_date = data.signal_date;
      savePins();
    }
    const stale = data.stale ? '（名单偏旧，建议刷新）' : '';
    const versionNote = data.configured_strategy_version === 'a-setups-v2' && data.strategy_version !== 'a-setups-v2'
      ? ' · 分型v2已就绪，下次盘后名单生效；当前显示旧名单' : '';
    const busy = data.status === 'running' ? '正在扫描最近交易日，请稍候…' : '';
    el('radar-status').textContent = busy
      || `更新于 ${data.generated_at || '—'} · 池内 ${data.scanned ?? '—'} / ${data.universe_size ?? '—'} 只${data.strategy_version === 'a-setups-v2' ? ` · 分型确认 ${data.confirmed_count ?? 0} 只（仍需大盘与计划通过）` : ''}${versionNote}${stale}`;
    el('radar-limits').textContent = (data.limitations || []).join(' · ') || el('radar-limits').textContent;
    el('service-dot').classList.toggle('connected', true);
    el('service-label').textContent = data.status === 'running' ? '扫描中' : '名单就绪';
    renderPins();
    renderBoard();
  }

  async function load(refresh = false) {
    el('radar-status').textContent = refresh ? '正在重新扫描…大约 1–2 分钟' : '正在读取名单…';
    el('btn-refresh').disabled = true;
    try {
      const res = await fetch('/api/a-radar' + (refresh ? '?refresh=1' : ''), { cache: 'no-store' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '读取失败');
      paint(data);
      if (data.status === 'running') setTimeout(() => load(false), 4000);
    } catch (err) {
      el('radar-status').textContent = err.message || String(err);
      el('service-dot').classList.remove('connected');
      el('service-label').textContent = '读取失败';
    } finally {
      el('btn-refresh').disabled = false;
    }
  }

  document.querySelectorAll('.radar-filter button').forEach((btn) => {
    btn.addEventListener('click', () => {
      filter = btn.dataset.filter || 'all';
      document.querySelectorAll('.radar-filter button').forEach((node) => {
        const on = node === btn;
        node.classList.toggle('selected', on);
        node.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      renderBoard();
    });
  });

  el('btn-refresh').addEventListener('click', () => load(true));
  el('btn-pin-all').addEventListener('click', () => {
    allRows().forEach((row) => togglePin(row, true));
  });
  el('btn-clear-pins').addEventListener('click', () => {
    pins = {signal_date: radar?.signal_date || null, items: []};
    savePins();
    renderPins();
    renderBoard();
  });

  loadPins();
  renderPins();
  load(false);
})();
