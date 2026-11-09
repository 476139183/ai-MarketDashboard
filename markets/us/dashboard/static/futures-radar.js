(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(value,digits=2)=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'—';
  const stamp=value=>Number.isFinite(value)?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供';
  const fresh=row=>Number.isFinite(row.quote_at)&&Date.now()-row.quote_at<=120000&&Date.now()-row.quote_at>=-5000;
  let items=[],timer,controller,version=0,loading=false;
  function paint(){
    const group=el('fr-group').value,trend=el('fr-trend').value,sort=el('fr-sort').value;
    const metric=row=>sort==='volume'?row.analysis?.volume_ratio:sort==='oi'?row.analysis?.oi_change:row.change_pct;
    const rows=items.filter(r=>(group==='all'||r.group===group)&&(trend==='all'||(r.analysis?.trend||'insufficient')===trend));
    rows.sort((a,b)=>{const x=metric(a),y=metric(b);return Number.isFinite(x)&&Number.isFinite(y)?y-x:Number.isFinite(x)?-1:Number.isFinite(y)?1:0;});
    el('fr-total').textContent=items.length||'—';
    el('fr-strong').textContent=items.length?items.filter(r=>r.analysis?.trend==='strong').length:'—';
    el('fr-weak').textContent=items.length?items.filter(r=>r.analysis?.trend==='weak').length:'—';
    el('fr-fresh').textContent=items.length?items.filter(fresh).length:'—';
    el('fr-visible').textContent=`${rows.length} / ${items.length} 个品种`;
    el('fr-rows').innerHTML=rows.length?rows.map(r=>{
      const a=r.analysis||{},color=a.trend==='strong'?'bullish':a.trend==='weak'?'bearish':'';
      const href='/futures?'+new URLSearchParams({symbol:r.symbol,period:el('fr-period').value});
      const status=[r.error,r.analysis_error?`结构读取失败：${r.analysis_error}`:null,!fresh(r)?'休市或延迟 · 报价非实时':null].filter(Boolean).join('；')||'源报价在120秒内';
      return `<tr><td><a class="paper-link" href="${esc(href)}">${esc(r.name)} · ${esc(r.symbol)} ↗</a><small>${esc(r.group)} · ${esc(r.exchange)} · 主力连续</small></td><td>${num(r.price)}<small>${esc(r.price_unit)} · ${num(r.change_pct)}%</small></td><td class="${color}">${esc(a.label||'结构不可用')}<small>${num(a.count,0)} 根完整K线</small></td><td>${num(a.volume_ratio)} 倍<small>持仓变化 ${num(a.oi_change,0)} 手</small></td><td>${stamp(r.quote_at)}</td><td>${stamp(a.asof)}</td><td class="${r.error||r.analysis_error||!fresh(r)?'stale':'bullish'}">${esc(status)}</td></tr>`;
    }).join(''):`<tr><td colspan="7">${items.length?'当前筛选下没有匹配品种':'等待商品行情与完整K线'}</td></tr>`;
  }
  async function load(reset=false){
    if(loading&&!reset)return;
    clearTimeout(timer);
    if(controller)controller.abort();
    controller=new AbortController();const ctl=controller,id=++version;
    if(reset){items=[];paint();el('fr-status').textContent='正在读取所选周期，首次采集需要一些时间…';}
    loading=true;el('fr-refresh').disabled=true;
    const timeout=setTimeout(()=>ctl.abort(),12000);let delay=30000;
    try{
      const period=el('fr-period').value;
      const response=await fetch('/api/futures/radar?'+new URLSearchParams({period}),{cache:'no-store',signal:ctl.signal});
      const data=await response.json();
      if(id!==version)return;
      if(!response.ok)throw Error(data.error||'期货雷达不可用');
      if(data.items)items=data.items;
      paint();
      const failures=items.filter(r=>r.error||r.analysis_error).length;
      el('fr-status').textContent=data.error?`${data.error} · 保留上次快照 · 采集 ${stamp(data.fetched_at)}`:data.status==='loading'?`正在采集${period==='day'?'日线':period+'分钟'}结构…${items.length?' 展示上次快照':''}`:`${items.length} 个品种 · 新浪财经 · 采集 ${stamp(data.fetched_at)}${failures?' · '+failures+' 个品种数据不完整':''}${data.stale?' · 缓存快照':''}`;
      if(data.status==='loading')delay=1500;
    }catch(error){if(id===version)el('fr-status').textContent='雷达读取失败：'+error.message+' · 已有数据仅为上次快照';}
    finally{clearTimeout(timeout);if(id===version){loading=false;el('fr-refresh').disabled=false;timer=setTimeout(load,delay);}}
  }
  for(const id of ['fr-group','fr-trend','fr-sort'])el(id).addEventListener('change',paint);
  el('fr-period').addEventListener('change',()=>load(true));
  el('fr-refresh').addEventListener('click',()=>load());
  load();
})();
