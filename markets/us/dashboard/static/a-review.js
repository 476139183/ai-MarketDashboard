(() => {
  const el = (id) => document.getElementById(id);
  el('workspace-date').textContent = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', weekday: 'short',
  }).format(new Date());

  function cards(rows, empty) {
    if (!rows || !rows.length) return `<p class="radar-empty">${empty}</p>`;
    return rows.map((r) => {
      const chg = r.change_pct == null ? '—' : `${r.change_pct > 0 ? '+' : ''}${r.change_pct}%`;
      const cls = r.change_pct > 0 ? 'up' : r.change_pct < 0 ? 'down' : 'flat';
      return `<div class="review-card"><strong>${r.name || r.code}</strong><span>${r.price ?? '—'}</span><span class="${cls}">${chg}</span></div>`;
    }).join('');
  }

  function paint(data) {
    el('as-of').textContent = data.as_of || '—';
    el('tone').textContent = data.market_tone || '—';
    el('hint').textContent = data.next_day_hint || '—';
    el('bullets').innerHTML = (data.summary_bullets || []).map((x) => `<li>${x}</li>`).join('')
      || '<li>暂无摘要</li>';
    el('indices').innerHTML = cards(data.indices, '指数暂不可用');
    el('top').innerHTML = cards(data.top_sectors, '领涨板块暂不可用');
    el('bottom').innerHTML = cards(data.bottom_sectors, '领跌板块暂不可用');
    el('limits').textContent = (data.limitations || []).join(' · ') || el('limits').textContent;
    const busy = data.status === 'running';
    el('status').textContent = busy ? '正在刷新复盘…'
      : `更新于 ${data.generated_at || '—'}${data.stale ? '（偏旧）' : ''}`;
    el('service-dot').classList.toggle('connected', true);
    el('service-label').textContent = busy ? '复盘中' : '复盘就绪';
  }

  async function load(refresh = false) {
    el('btn-refresh').disabled = true;
    try {
      const res = await fetch('/api/a-review' + (refresh ? '?refresh=1' : ''), { cache: 'no-store' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '读取失败');
      paint(data);
      if (data.status === 'running') setTimeout(() => load(false), 2500);
    } catch (err) {
      el('status').textContent = err.message || String(err);
      el('service-dot').classList.remove('connected');
    } finally {
      el('btn-refresh').disabled = false;
    }
  }

  el('btn-refresh').addEventListener('click', () => load(true));
  load(false);
})();
