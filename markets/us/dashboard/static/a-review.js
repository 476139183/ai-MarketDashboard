(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
  const fmt = value => num(value) === null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits:2});
  const pct = value => num(value) === null ? '—' : `${Number(value)>0?'+':''}${Number(value).toFixed(2)}%`;
  const direction = value => num(value) === null ? 'flat' : Number(value)>0?'up':Number(value)<0?'down':'flat';
  let timer = null, controller = null, serial = 0;
  el('workspace-date').textContent = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'short'}).format(new Date());
  function indexCards(rows) {
    return rows.length ? rows.map(r => `<article class="index-card"><div><strong>${esc(r.name || r.code)}</strong><span>${esc(r.code)}</span></div><b>${fmt(r.price)}</b><em class="${direction(r.change_pct)}">${pct(r.change_pct)}</em><small>${esc(r.asof || '行情时间未提供')}</small></article>`).join('') : '<p class="small-note">指数行情暂不可用。</p>';
  }
  function sectors(rows) {
    const scale = Math.max(1,...rows.map(r=>Math.abs(num(r.change_pct)||0)));
    return rows.length ? rows.map((r,i)=>`<div class="sector-row"><span class="sector-rank">${i+1}</span><div><strong>${esc(r.name||r.code)}</strong><div class="sector-track"><i class="${direction(r.change_pct)}" style="width:${Math.min(100,Math.abs(num(r.change_pct)||0)/scale*100)}%"></i></div></div><b class="${direction(r.change_pct)}">${pct(r.change_pct)}</b></div>`).join('') : '<p class="small-note">板块行情暂不可用。</p>';
  }
  function paint(data) {
    const m=data.metrics||{}, breadth=data.breadth||{}, quality=data.quality||{};
    el('as-of').textContent=data.as_of||'未确认';el('tone').textContent=data.market_tone||'待确认';el('hint').textContent=data.next_day_hint||'等待数据补齐。';
    el('review-phase').textContent=data.session?.label||'行情状态待核对';el('review-coverage').textContent=quality.coverage_label||'覆盖待确认';
    const hs=(data.indices||[]).find(r=>r.code==='000300');
    el('metric-index').textContent=pct(hs?.change_pct);el('metric-index').className=direction(hs?.change_pct);el('metric-index-note').textContent=hs?`收于 ${fmt(hs.price)} 点`:'指数行情缺失';
    el('metric-breadth').textContent=num(m.advance_ratio)===null?'—':`${fmt(m.advance_ratio)}%`;el('metric-breadth-note').textContent=`有效样本 ${fmt(m.breadth_total)} 家`;
    el('metric-amount-label').textContent=m.turnover_scope||'全A有效报价成交额';el('metric-amount').textContent=num(m.turnover)===null?'—':`${fmt(m.turnover/1e8)} 亿`;el('metric-limits').textContent=`${fmt(m.limit_up)} / ${fmt(m.limit_down)}`;
    el('indices').innerHTML=indexCards(data.indices||[]);el('top').innerHTML=sectors(data.top_sectors||[]);el('bottom').innerHTML=sectors(data.bottom_sectors||[]);
    el('breadth-scope').textContent=breadth.scope||'缺少有效涨跌家数，不能推断全市场。';
    const counts=[breadth.up,breadth.flat,breadth.down], total=counts.every(v=>num(v)!==null&&Number(v)>=0)?counts.reduce((s,v)=>s+Number(v),0):0;
    el('breadth-bar').innerHTML=total?counts.map((v,i)=>`<span class="breadth-${['up','flat','down'][i]}" style="width:${Number(v)/total*100}%"></span>`).join(''):'';
    el('breadth-counts').innerHTML=counts.map((v,i)=>`<div><span>${['上涨','平盘','下跌'][i]}</span><strong class="${['up','flat','down'][i]}">${fmt(v)}</strong></div>`).join('');
    const bins=data.stock_snapshot?.distribution||[], max=Math.max(1,...bins.map(r=>num(r.count)||0));
    el('review-distribution').innerHTML=bins.length?bins.map(r=>`<div class="distribution-bin"><span>${fmt(r.count)}</span><div><i class="breadth-${['up','down'].includes(r.direction)?r.direction:'flat'}" style="height:${Math.max(2,(num(r.count)||0)/max*100)}%"></i></div><small>${esc(r.label)}</small></div>`).join(''):'<p class="empty-distribution">暂未取得完整全A分布<br><small>部分样本不代替全市场分布</small></p>';
    el('review-insights').innerHTML=(data.insights||[]).map(r=>`<article><span>${esc(r.title)}</span><p>${esc(r.text)}</p></article>`).join('')||'<p class="small-note">数据不足，暂不生成市场判断。</p>';
    el('review-missing').textContent=quality.missing?.length?'暂缺：'+quality.missing.join(' / '):'当前关键字段已取得；请结合各项覆盖范围使用。';
    const snap=data.stock_snapshot||{};el('review-date-note').textContent=(data.session?.date_note||'')+(snap.expected?`；快照收到 ${fmt(snap.received)} / ${fmt(snap.expected)} 个代码，${fmt(snap.missing_quotes)} 个报价无效或缺失，未计入涨跌样本。`:'');el('limits').textContent=(data.limitations||[]).join('；');
    const busy=data.status==='running';el('btn-refresh').disabled=busy;
    el('status').textContent=busy?'正在采集新一轮复盘，当前显示上次快照…':`采集于 ${(data.generated_at||'未知').replace('T',' ')}${data.collection_old?' · 采集时间较早':''}${data.stale?' · 行情日期需核验':''}${data.error?' · 最近刷新失败：'+data.error:''}`;
    el('service-dot').classList.toggle('connected',true);el('service-label').textContent=busy?'复盘采集中':'复盘已读取';
    return busy;
  }
  async function load(refresh=false) {
    if(timer)clearTimeout(timer);const id=++serial;if(controller)controller.abort();controller=new AbortController();const ctl=controller;const timeout=setTimeout(()=>ctl.abort(),20000);el('btn-refresh').disabled=true;
    try{const response=await fetch('/api/a-review'+(refresh?'?refresh=1':''),{cache:'no-store',signal:ctl.signal});const data=await response.json();if(id!==serial)return;if(!response.ok)throw Error(data.error||'复盘读取失败');if(paint(data))timer=setTimeout(()=>load(),2500);}
    catch(error){if(id===serial){el('status').textContent='复盘读取失败：'+error.message+'。已有内容保留为上次快照。';el('btn-refresh').disabled=false;el('service-dot').classList.remove('connected');}}
    finally{clearTimeout(timeout);}
  }
  async function news(){try{const response=await fetch('/api/news?market=cn&days=7&page_size=4',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'资讯暂不可用');el('review-news').innerHTML=(data.items||[]).slice(0,4).map(r=>{let href=null;try{const u=new URL(r.url);if(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password)href=u.href;}catch(_){}return `<article><span>${esc(r.source_name||r.source||'来源未提供')}</span><h3>${href?`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(r.title)} ↗</a>`:esc(r.title)}</h3><small>发布 · ${esc(r.published_at||'发布时间未提供')}</small></article>`;}).join('')||'<p class="small-note">资讯池暂无A股记录。<a href="/news?market=cn">添加来源并采集 ↗</a></p>';}
    catch(error){el('review-news').textContent='资讯暂未取得，可在资讯页检查来源与采集状态。';}}
  el('btn-refresh').addEventListener('click',()=>load(true));load();news();
})();
