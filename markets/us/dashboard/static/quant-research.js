(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';
  const ids=['qr-market','qr-engine','qr-strategy','qr-symbol','qr-cost','qr-sizing'];
  let metadata={},result=null,running=false,timer;
  async function api(url,body){
    const r=await fetch(url,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const d=await r.json();if(!r.ok)throw Error(d.error||'研究请求失败');return d;
  }
  function controls(){
    const futures=el('qr-market').value==='futures',tq=el('qr-engine').value==='tqsdk';
    if(tq){el('qr-sizing').value='fixed';el('qr-cost').value='1';}
    el('qr-symbol').disabled=running||!futures;el('qr-sizing').disabled=running||!futures||tq;el('qr-cost').disabled=running||tq;
    const state=metadata.engines?.[el('qr-engine').value];
    el('qr-run').disabled=running||!state?.installed||(tq&&(!state.credentials||!futures))||(!tq&&metadata.samples&&metadata.samples[el('qr-market').value]!==true);
    el('qr-engines').textContent=`RQAlpha ${metadata.engines?.rqalpha?.version||'未安装'} · TqSdk ${metadata.engines?.tqsdk?.version||'未安装'}${metadata.engines?.tqsdk?.credentials?'（已配置账号）':'（待配置账号）'}`;
  }
  function paint(r){
    result=r;el('qr-export').disabled=false;
    const s=r.summary;
    if(!s){el('qr-results').textContent=JSON.stringify(r.statistics||r,null,2);return;}
    el('qr-results').innerHTML=`<h2>${r.market==='a_share'?'A股':'国内期货'} · ${esc(r.config?.strategy)} · ${esc(r.engine)} ${esc(r.engine_version)}</h2>
      <p>${esc(r.data?.start)} 至 ${esc(r.data?.end)} · 独立虚拟本金 ${num(s.initial_capital)} CNY</p>
      <div class="contract-table-wrap"><table class="contract-table"><thead><tr><th>累计收益</th><th>几何折算月收益</th><th>最大权益回撤</th><th>已平仓 / 持仓</th><th>已平仓胜率</th><th>费用</th></tr></thead><tbody><tr><td>${num(s.return_pct)}%</td><td>${num(s.monthly_equivalent_pct)}%</td><td>${num(s.max_drawdown_pct)}%</td><td>${s.closed_trades} / ${r.open_positions.length}</td><td>${Number.isFinite(s.win_rate_pct)?num(s.win_rate_pct)+'%':'无已平仓，无法计算'}</td><td>${num(s.fees)} CNY</td></tr></tbody></table></div>
      <p>框架拒单 ${s.rejected_orders} 次 · 保证金条件拒绝 ${r.diagnostics?.margin_skips??0} 次 · 风险预算不足 ${r.diagnostics?.risk_skips??0} 次。收益包含未平仓估值，折算月收益不是每月收益承诺。</p>
      <h3>框架实际成交（最近100条）</h3><div class="contract-table-wrap"><table class="contract-table"><thead><tr><th>标的</th><th>时间</th><th>方向 / 开平</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>${(r.fills||[]).slice(-100).map(f=>`<tr><td>${esc(f.symbol)}</td><td>${esc(f.datetime)}</td><td>${esc(f.side)} / ${esc(f.position_effect)}</td><td>${f.quantity}</td><td>${num(f.price)}</td><td>${num(f.fees)}</td></tr>`).join('')||'<tr><td colspan="6">没有成交；请查看候选、资金与数据约束。</td></tr>'}</tbody></table></div>
      <h3>本次假设与限制</h3><ul>${(r.assumptions||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
  }
  async function poll(id){
    try{
      const d=await api('/api/quant-research?id='+encodeURIComponent(id));
      if(d.status==='running'){el('qr-status').textContent=d.progress;timer=setTimeout(()=>poll(id),1500);return;}
      if(d.status==='error')throw Error(d.error);
      paint(d.result);el('qr-status').textContent='研究完成；运行账户和策略未改变。';
    }catch(e){el('qr-status').textContent=e.message;}
    running=false;ids.forEach(id=>{el(id).disabled=false;});controls();
  }
  el('qr-run').addEventListener('click',async()=>{
    if(running)return;
    const tq=el('qr-engine').value==='tqsdk',futures=el('qr-market').value==='futures';
    const body={engine:el('qr-engine').value,market:el('qr-market').value,strategy:el('qr-strategy').value,symbol:el('qr-symbol').value,
      cost_scale:tq?1:Number(el('qr-cost').value),sizing:futures&&!tq?el('qr-sizing').value:'fixed'};
    running=true;result=null;el('qr-results').innerHTML='';el('qr-export').disabled=true;ids.forEach(id=>{el(id).disabled=true;});controls();el('qr-status').textContent='正在启动独立引擎…';
    try{const d=await api('/api/quant-research',body);await poll(d.id);}catch(e){running=false;ids.forEach(id=>{el(id).disabled=false;});controls();el('qr-status').textContent=e.message;}
  });
  ids.forEach(id=>el(id).addEventListener('change',()=>{
    if(running)return;
    if(id==='qr-market'){
      const futures=el('qr-market').value==='futures';el('qr-strategy').innerHTML=futures?'<option value="sma">双均线20/60</option><option value="donchian">唐奇安20/10</option>':'<option value="setups_v2">A股分型v2</option><option value="legacy">旧版重返MA20</option><option value="expanded">扩展信号对照（非运行策略）</option>';
      el('qr-strategy').value=futures?'sma':'setups_v2';
      el('qr-sizing').innerHTML=futures?'<option value="fixed">固定一手</option><option value="risk">2ATR计划风险≤权益0.5%</option>':'<option value="fixed">每票2万元，最多3只</option>';
      el('qr-sizing').value='fixed';
    }
    result=null;el('qr-results').innerHTML='';el('qr-export').disabled=true;controls();el('qr-status').textContent='参数已变更，请运行新的独立研究。';
  }));
  el('qr-export').addEventListener('click',()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='quant-research.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  api('/api/quant-research').then(d=>{metadata=d.metadata||{};controls();if(d.result)paint(d.result);el('qr-status').textContent=metadata.samples&&metadata.samples[el('qr-market').value]!==true?'所选市场暂无本地样本，请先采集或选择已有归档的市场。':'本地样本已就绪；点击运行才开始研究，不控制模拟账户。';}).catch(e=>{el('qr-status').textContent=e.message;});
})();
