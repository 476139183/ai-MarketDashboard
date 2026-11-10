(() => {
 'use strict';
 const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=v=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';
 let result=null,timer,busy=false;
 function paint(data){
  result=data;el('ar-export').disabled=false;
  el('ar-results').innerHTML=`<p>${esc(data.start)} 至 ${esc(data.end)} · ${data.symbols.length}票 · 15分钟开盘采样情景研究</p><div class="contract-table-wrap"><table class="contract-table"><thead><tr><th>方案</th><th>累计收益</th><th>几何折算月收益</th><th>已平仓胜率</th><th>平仓 / 未平仓</th><th>最大收盘回撤</th><th>费用</th></tr></thead><tbody>${data.results.map(r=>`<tr><td>${esc(r.label)}<small>大盘通过${r.diagnostics?.market_pass_days??'—'}日 · 候选${r.diagnostics?.candidate_signals??'—'} · 计划通过${r.diagnostics?.ready_plans??'—'}</small></td><td>${num(r.summary.return_pct)}%</td><td>${num(r.summary.monthly_equivalent_pct)}%</td><td>${Number.isFinite(r.summary.win_rate_pct)?num(r.summary.win_rate_pct)+'%':'无已平仓，无法计算'}</td><td>${r.summary.closed_trades} / ${r.open_positions.length}</td><td>${num(r.summary.max_drawdown_pct)}%</td><td>${num(r.summary.fees)} CNY</td></tr>`).join('')}</tbody></table></div><h3>逐月收益（首尾月份可能不完整）</h3><div class="contract-table-wrap"><table class="contract-table"><thead><tr><th>方案</th><th>月份</th><th>账户权益收益</th></tr></thead><tbody>${data.results.flatMap(r=>r.monthly.map(m=>`<tr><td>${esc(r.label)}</td><td>${esc(m.month)}</td><td>${num(m.return_pct)}%</td></tr>`)).join('')}</tbody></table></div><p>几何折算月收益以实际日历跨度折算，不是每月收益承诺；候选口径对照只用于研究，不改变运行账户。</p><ul>${data.limitations.map(s=>`<li>${esc(s)}</li>`).join('')}</ul>`;
 }
 async function api(url,body){
  const ctl=new AbortController(),timeout=setTimeout(()=>ctl.abort(),15000);
  try{const r=await fetch(url,{cache:'no-store',signal:ctl.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok)throw Error(d.error||'历史复盘读取失败');return d;}finally{clearTimeout(timeout);}
 }
 async function poll(id){
  try{
   const data=await api('/api/a-paper/research?id='+encodeURIComponent(id));
   if(data.status==='running'){el('ar-status').textContent=data.progress||'历史样本复盘中…';timer=setTimeout(()=>poll(id),2000);return;}
   if(data.status==='failed')throw Error(data.error||'历史复盘失败');
   if(data.result){paint(data.result);el('ar-status').textContent='历史样本复盘完成；各方案为同样本对照，未修改模拟账户。';}
  }catch(error){el('ar-status').textContent='历史复盘失败：'+error.message;}
  busy=false;el('ar-run').disabled=false;
 }
 el('ar-run').addEventListener('click',async()=>{
  if(busy)return;busy=true;el('ar-run').disabled=true;clearTimeout(timer);el('ar-status').textContent='正在准备历史样本…';
  try{const data=await api('/api/a-paper/research',{months:3,sample_size:46,config:{per_stock_budget:Number(el('ar-budget').value),max_positions:Number(el('ar-positions').value),exit_ma:Number(el('ar-exit-ma').value)}});await poll(data.id);}catch(error){el('ar-status').textContent=error.message;busy=false;el('ar-run').disabled=false;}
 });
 el('ap-export').addEventListener('click',async()=>{
  try{const data=await api('/api/a-paper/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='a-share-forward-records.json';a.click();URL.revokeObjectURL(url);}catch(error){el('ar-status').textContent='导出失败：'+error.message;}
 });
 el('ar-export').addEventListener('click',()=>{
  if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='a-share-plan-research.json';link.click();URL.revokeObjectURL(url);
 });
 api('/api/a-paper/research').then(data=>{if(data.result){paint(data.result);el('ar-status').textContent='已读取上次复盘结果。调整复盘参数后可重新对照，运行账户不变。';}}).catch(error=>{el('ar-status').textContent='尚无可读历史复盘：'+error.message;});
})();
