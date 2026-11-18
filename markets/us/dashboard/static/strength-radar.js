(()=>{
 'use strict';const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const labels={base_recovery:'底部修复（高风险）',early_turn:'初步转强',steady_rise:'连续走强',volume_breakout:'温和放量突破',pullback_restart:'回踩再启动'};
 const stages={security:'风险证券',data:'日线未就绪',suspended:'无成交量',liquidity:'流动性不足',extended:'涨幅或乖离过大',locked:'一字线',shape:'未达形态',matched:'命中'};
 const num=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
 const pct=v=>v==null?'—':`${Number(v)>0?'+':''}${num(v)}%`;
 let report={items:[]},pattern='all',page=1,timer=null,seq=0;
 function render(){
  const query=el('strength-search').value.trim().toLowerCase(),sort=el('strength-sort').value;
  const source=pattern==='tracking'?(report.tracking_items||[]):pattern==='all'&&query?[...(report.items||[]),...(report.tracking_items||[])]:report.items||[];
  let rows=source.filter(r=>(pattern==='all'||pattern==='tracking'||(r.patterns||[]).includes(pattern))&&(!query||String(r.name).toLowerCase().includes(query)||String(r.code).includes(query)));
  rows.sort((a,b)=>sort==='early'?String(b.first_signal_date||'').localeCompare(String(a.first_signal_date||''))||Number(b.score)-Number(a.score):Number(sort==='return5'?b.metrics?.return5:sort==='volume'?b.metrics?.volume_ratio:b.score)-Number(sort==='return5'?a.metrics?.return5:sort==='volume'?a.metrics?.volume_ratio:a.score));
  const pages=Math.max(1,Math.ceil(rows.length/20));page=Math.min(page,pages);
  el('strength-filter-note').textContent=`筛选匹配 ${rows.length} 只 · 每页20只 · 匹配度只表示规则排序，不是上涨概率${pattern==='tracking'||query?' · 历史跟踪不计今日命中':''}`;
  el('strength-list').innerHTML=rows.slice((page-1)*20,page*20).map(r=>{
   const m=r.metrics||{};return `<article class="strength-card"><div class="strength-card-top"><a href="/research?symbol=${encodeURIComponent(r.code)}"><strong>${esc(r.name)}</strong><span>${esc(r.code)} · 看指标 ↗</span></a><div class="strength-price"><strong>${num(r.close)}</strong><span>${pct(m.change_pct)} · 收盘</span></div></div><div class="strength-badges">${r.tracking?`<span class="tracking-tag">${r.stage==='locked'?'一字线跟踪 · 成交未核验':r.stage==='extended'?'过热跟踪':'未再命中 · 历史跟踪'}</span>`:''}${(r.tracking?r.tracking_patterns||[]:r.patterns||[]).map(k=>`<span>${esc(labels[k]||k)}</span>`).join('')}<small>匹配度 ${num(r.score)}</small></div><div class="strength-card-metrics"><div><span>近3日 / 5日</span><strong>${pct(m.return3)} / ${pct(m.return5)}</strong></div><div><span>近5日上涨</span><strong>${num(m.up_days5)} 天</strong></div><div><span>相对成交量</span><strong>${num(m.volume_ratio)} ×</strong></div><div><span>MA20乖离</span><strong>${pct(m.bias20)}</strong></div></div><p class="strength-first">近20根日线回看首现 ${esc(r.first_signal_date||'待计算')} · 连续命中 ${num(r.continuing_sessions)} 个完整日线</p><p class="strength-risk">${esc(r.risk_note||'独立研究候选，需结合价格结构核对')}</p></article>`;
  }).join('')||'<p class="radar-empty">当前范围没有匹配个股；请同时核对扫描日期、覆盖数量和未就绪项。</p>';
  el('strength-page').textContent=`${page} / ${pages} 页`;el('strength-prev').disabled=page<=1;el('strength-next').disabled=page>=pages;
 }
 function paint(data){report=data;el('strength-date').textContent=data.signal_date||'尚未扫描';const coverage=data.coverage||{};
  el('strength-coverage').textContent=`${num(coverage.ready)} / ${num(coverage.total)}`;el('strength-total').textContent=num((data.items||[]).length);el('strength-missing').textContent=num(coverage.missing);el('strength-tracking-count').textContent=num((data.tracking_items||[]).length);
  const validation=data.validation;el('strength-validation').textContent=validation?`历史回看 ${validation.start}～${validation.as_of}：命中后5根上涨比例 ${num(validation.positive_pct)}%，同日期加权对照 ${num(validation.baseline_positive_pct)}%；样本重叠，不能作为未来胜率。`:'研究规则，尚未完成独立样本外验证；请结合价格结构与事件核对。';
  el('strength-refresh').disabled=data.status==='running';
  el('strength-status').textContent=data.status==='running'?`正在扫描 ${num(data.completed)} / ${num(data.total)}，以下保留上次快照`:(data.signal_date?`采集于 ${(data.generated_at||'未知').replace('T',' ')}${data.stale?' · 行情日期需要更新':''}${coverage.complete===false?' · 覆盖不完整':''}${data.error?' · 最近刷新失败：'+data.error:''}`:'尚无走强名单，点击「扫描全市场」显式采集。'+(data.error||''));
  el('strength-exclusions').textContent=`证券快照 ${num(data.universe_total)} 只，名称风险剔除 ${num(data.security_excluded)} 只；逐项状态：`+Object.entries(data.stages||{}).map(([k,v])=>`${stages[k]||k} ${v}`).join(' / ');
  el('strength-limitations').textContent=(data.limitations||[]).join('；');render();
 }
 async function load(start=false){const id=++seq;if(timer)clearTimeout(timer);el('strength-refresh').disabled=true;
  try{const response=await fetch('/api/a-strength',{cache:'no-store',...(start?{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}:{})});const data=await response.json();if(id!==seq)return;if(!response.ok)throw Error(data.error||'读取失败');paint(data);if(data.status==='running')timer=setTimeout(()=>load(),3500);}
  catch(error){if(id===seq){el('strength-status').textContent='走强名单读取失败：'+error.message+'。已有内容仅为上次快照。';el('strength-refresh').disabled=false;}}
 }
 el('strength-refresh').addEventListener('click',()=>load(true));
 el('strength-filters').addEventListener('click',event=>{const button=event.target.closest('[data-pattern]');if(!button)return;pattern=button.dataset.pattern;page=1;for(const node of document.querySelectorAll('[data-pattern]'))node.classList.toggle('selected',node.dataset.pattern===pattern);render();});
 el('strength-search').addEventListener('input',()=>{page=1;render();});el('strength-sort').addEventListener('change',()=>{page=1;render();});
 el('strength-prev').addEventListener('click',()=>{if(page>1){page--;render();}});el('strength-next').addEventListener('click',()=>{page++;render();});load();
})();
