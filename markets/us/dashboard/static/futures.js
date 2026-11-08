(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(value,digits=2)=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'—';
  const stamp=value=>Number.isFinite(value)?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供';
  const fresh=row=>Number.isFinite(row.quote_at)&&Date.now()-row.quote_at<=120000&&Date.now()-row.quote_at>=-5000;
  const chart=new window.MarketChart(el('futures-chart'),el('futures-chart-legend'));
  let symbol='RB0',version=0,items=[],chartTimer,boardTimer,chartController,boardBusy=false;

  async function api(url,signal){
    const response=await fetch(url,{cache:'no-store',signal});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'期货服务不可用');
    return data;
  }
  function paintBoard(){
    const group=el('futures-group').value;
    const rows=items.filter(r=>group==='all'||r.group===group);
    el('futures-rows').innerHTML=rows.length?rows.map(r=>`<tr${r.symbol===symbol?' class="selected"':''}><td><button type="button" data-symbol="${esc(r.symbol)}">${esc(r.name)} · ${esc(r.symbol)}</button><small>${esc(r.exchange)} · ${esc(r.contract_label)}</small></td><td>${num(r.price)}<small>${esc(r.price_unit)}</small></td><td>${Number.isFinite(r.change_pct)?num(r.change_pct)+'%':'—'}</td><td>${num(r.volume,0)}</td><td>${num(r.open_interest,0)}</td><td>${stamp(r.quote_at)}</td><td class="${fresh(r)?'bullish':'stale'}">${esc(r.error||(fresh(r)?'源报价在120秒内':'休市或延迟 · 旧报价'))}</td></tr>`).join(''):'<tr><td colspan="7">尚无匹配商品行情</td></tr>';
  }
  async function loadBoard(){
    if(boardBusy)return;
    boardBusy=true;clearTimeout(boardTimer);
    const ctl=new AbortController(),timeout=setTimeout(()=>ctl.abort(),12000);
    let delay=30000;
    try{
      const data=await api('/api/futures',ctl.signal);
      if(data.items)items=data.items;
      paintBoard();
      el('futures-status').textContent=data.error?`${data.error} · 表中保留上次快照，采集 ${stamp(data.fetched_at)}`:data.status==='loading'?`正在采集商品行情…${items.length?' 表中为上次快照':''}`:`${items.length} 个品种 · 新浪财经 · 采集 ${stamp(data.fetched_at)} · 源报价时间见表格`;
      if(data.status==='loading')delay=1200;
    }catch(error){el('futures-status').textContent='行情读取失败：'+error.message+' · 表中仅为上次快照';}
    finally{clearTimeout(timeout);boardBusy=false;boardTimer=setTimeout(loadBoard,delay);}
  }
  function resetDetail(){
    chart.clear();
    el('futures-selected').textContent=symbol+' · 正在读取所选合约';
    el('futures-identity').textContent='等待合约身份与报价单位';
    el('futures-contract-note').textContent=symbol.endsWith('0')?'主力连续用于走势观察，换月可能产生跳空；并非可直接成交的到期合约。':'月份合约可能到期、缺少流动性或尚未上市；以源数据为准。';
    el('futures-quote').textContent='等待所选合约报价';
    el('futures-analysis').textContent='等待完整K线';
    el('futures-analysis-asof').textContent='指标只使用所选合约的完整K线';
    for(const id of ['futures-ma','futures-atr','futures-levels','futures-volume-ratio','futures-oi-change'])el(id).textContent='—';
    el('futures-evidence').innerHTML='';
    el('futures-chart-status').textContent='正在读取K线…';
  }
  function paintDetail(data){
    el('futures-selected').textContent=`${data.name} ${data.symbol} · ${data.continuous?'主力连续':'月份合约'}`;
    el('futures-identity').textContent=`${data.exchange||'—'} · ${data.price_unit||'—'} · ${data.period==='day'?'日线':data.period+'分钟'} · ${data.source||'新浪财经'}`;
    const q=data.quote;
    el('futures-quote').textContent=q&&Number.isFinite(q.price)?`${num(q.price)} ${data.price_unit||''} · ${fresh(q)?'源报价在120秒内':'休市或延迟'} · ${stamp(q.quote_at)}`:'源报价未取得'+(q?.error||data.quote_error?'：'+(q?.error||data.quote_error):'');
    chart.render({...data,volume_unit:'手'},data.period);
    const a=data.analysis||{};
    el('futures-analysis').textContent=a.label||'完整K线不足';
    el('futures-analysis-asof').textContent=`${a.count||0} 根完整K线 · 最后完整${data.period==='day'?'交易日':'线结束时间'} ${stamp(a.asof)}${data.stale?' · 缓存快照':''}`;
    el('futures-ma').textContent=`${num(a.sma20)} / ${num(a.sma60)}`;
    el('futures-atr').textContent=`${num(a.atr14)} ${data.price_unit||''}`;
    el('futures-levels').textContent=`${num(a.support)} / ${num(a.resistance)}`;
    el('futures-volume-ratio').textContent=Number.isFinite(a.volume_ratio)?num(a.volume_ratio)+' 倍':'—';
    el('futures-oi-change').textContent=num(a.oi_change,0);
    el('futures-evidence').innerHTML=(a.evidence||[]).map(s=>`<li>${esc(s)}</li>`).join('');
  }
  async function loadChart(){
    clearTimeout(chartTimer);
    const requestVersion=++version,requestedSymbol=symbol,period=el('futures-period').value;
    if(chartController)chartController.abort();
    chartController=new AbortController();const ctl=chartController;
    const timeout=setTimeout(()=>ctl.abort(),12000);let delay=30000;
    try{
      const data=await api('/api/futures/chart?'+new URLSearchParams({symbol:requestedSymbol,period}),ctl.signal);
      if(requestVersion!==version)return;
      if(data.candles)paintDetail(data);
      el('futures-chart-status').textContent=data.error?`${data.error} · ${data.candles?'保留上次快照':'没有可用图表'} · 采集 ${stamp(data.fetched_at)}`:data.status==='loading'?'正在采集所选期货K线…':`${data.candles?.length||0} 根源K线 · 采集 ${stamp(data.fetched_at)} · 末根可能形成中`;
      if(data.status==='loading')delay=1200;
    }catch(error){if(requestVersion!==version)return;el('futures-chart-status').textContent='K线读取失败：'+error.message+' · 已有图表仅为上次快照';}
    finally{clearTimeout(timeout);if(requestVersion===version)chartTimer=setTimeout(loadChart,delay);}
  }
  function select(value){
    symbol=value.trim().toUpperCase();
    el('futures-symbol').value=symbol;
    resetDetail();paintBoard();loadChart();
  }
  el('futures-search').addEventListener('submit',event=>{event.preventDefault();select(el('futures-symbol').value);});
  el('futures-rows').addEventListener('click',event=>{const button=event.target.closest('[data-symbol]');if(button)select(button.dataset.symbol);});
  el('futures-period').addEventListener('change',()=>{resetDetail();loadChart();});
  el('futures-group').addEventListener('change',paintBoard);
  el('futures-refresh').addEventListener('click',()=>{loadBoard();loadChart();});
  loadBoard();loadChart();
})();
