(() => {
  'use strict';
  const el=id=>document.getElementById(id), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=v=>v==null?'—':new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
  const num=v=>v==null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:4});
  let watchlist=['NVDA','MU','TSLA','AMD','AAPL','MSFT','AMZN','META'],page=0,search='',items=[],selected='',paused=false;
  let timer=null,requestId=0,controller=null,busy=false;
  const symbols=()=>search?[search]:watchlist.slice(page*8,page*8+8);
  let detail=null, detailKey='', detailTimer=null, detailController=null, detailRequest=0;
  const chartView=window.MarketChart?new window.MarketChart(el('us-chart'),el('us-chart-legend')):null;
  const detailIdentity=row=>`${row.symbol}:${el('us-period').value}:${el('us-session').value}:${el('us-provider').value}`;
  function showChart(row){
    el('us-selected').textContent=`${row.symbol} · ${row.source}`;
    el('source-name').textContent=row.source;
    el('us-explanation').textContent=`${row.reason}${row.warning?' · '+row.warning:''}`;
    const data=(row.sampled?row.line:(row.chart_candles||row.candles))||[];
    el('us-chart-note').textContent=row.sampled?'分时价格采样，没有每分钟真实 OHLCV；K 线周期不改变原始分时线。':'当前未收盘 K 线持续更新，仅供图表观察；方向判断仍只使用完整收盘线。';
    el('us-chart-volume').textContent=`源日累计成交量：${num(row.day_volume)} 股 · ${row.day_volume_source||'Nasdaq'} · 数据日期 ${row.day_volume_date||'未提供'}。${row.sampled?'逐根成交量缺失。':`下方量柱单位：${row.volume_unit||'股'}。`}`;
    if(chartView)chartView.render(row,el('us-session').value);
  }
  async function loadDetail(key){
    if(key!==detailKey||paused)return;
    const id=++detailRequest;if(detailController)detailController.abort();detailController=new AbortController();const ctl=detailController;
    const timeout=setTimeout(()=>ctl.abort(),12000);let running=false;
    try{
      const params=new URLSearchParams({symbols:selected,period:el('us-period').value,session:el('us-session').value,provider:el('us-provider').value});
      const response=await fetch('/api/us-chart?'+params,{signal:ctl.signal,cache:'no-store'}),data=await response.json();
      if(id!==detailRequest||key!==detailKey)return;if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);
      if(data.items.length){detail=data.items[0];showChart(detail);}
      running=data.status==='running';
      el('us-chart-status').textContent=`${running?'正在更新所选标的':'图表快照'} · 采集 ${time(data.generated_at)} · 每 15 秒轮询采集，数据实时性取决于来源权限`;
    }catch(error){if(id===detailRequest&&key===detailKey)el('us-chart-status').textContent=`图表更新失败：${error.message} · 保留上次快照`;}
    finally{
      clearTimeout(timeout);
      if(id===detailRequest&&key===detailKey&&!paused)detailTimer=setTimeout(()=>loadDetail(key),running?2000:15000);
    }
  }
  function renderChart(){
    const row=items.find(r=>r.symbol===selected)||items[0];if(!row)return;selected=row.symbol;
    const key=detailIdentity(row);
    if(key!==detailKey){
      detailKey=key;detail=null;detailRequest++;if(detailController)detailController.abort();if(detailTimer!==null)clearTimeout(detailTimer);
      if(chartView)chartView.clear();showChart(row);loadDetail(key);
    }else showChart(detail||row);
  }
  function render(){
    el('us-rows').replaceChildren(...items.map(row=>{
      const tr=document.createElement('tr');
      const coverage=key=>{const c=row.coverage[key];return `${c.count} ${row.sampled?'采样点':'根'}<small>${esc(row.coverage_date||'未取得')} · 最后 ${esc(time(c.last_at))}</small>`;};
      // Recheck quote age in the browser; an old cached direction cannot stay active.
      const fresh=row.valid && Date.now()<row.valid_until && row.quote_at!=null && Date.now()-row.quote_at<=120000 && Date.now()-row.quote_at>=-5000;
      tr.innerHTML=`<td><strong>${esc(row.symbol)}</strong><small>${esc(row.source)}</small></td><td><span class="direction-badge ${fresh?row.direction:'observe'}">${esc(fresh?row.label:'观望 / 待确认')}</span></td><td>${num(row.price)}<small>${esc(row.quote_at==null?(row.quote_time_text||'源未提供时间'):time(row.quote_at))} · ${esc(row.quote_source||'Nasdaq')}报价${row.quote_at==null?' · 无分时戳':''}${fresh?'':' · 参考价'}</small></td><td>${esc(time(row.bar_close_at))}</td><td>${coverage('pre')}</td><td>${coverage('regular')}</td><td>${coverage('post')}</td><td>${esc(row.error||row.warning||row.reason)}</td>`;
      tr.addEventListener('click',()=>{selected=row.symbol;renderChart();});return tr;
    }));
    el('us-page').textContent=search?`单股 · ${search}`:`${page+1} / ${Math.ceil(watchlist.length/8)} 页 · ${watchlist.length} 个美股`;
    el('us-prev').disabled=!!search||page===0;el('us-next').disabled=!!search||(page+1)*8>=watchlist.length;renderChart();
  }
  function schedule(running=false){if(timer!==null)clearTimeout(timer);if(!paused)timer=setTimeout(()=>load(),running?2000:Number(el('us-refresh').value)*1000);}
  function clear(){detailKey='';detail=null;detailRequest++;if(detailController)detailController.abort();if(detailTimer!==null)clearTimeout(detailTimer);if(chartView)chartView.clear();items=[];selected='';render();el('us-selected').textContent='等待所选行情';el('us-explanation').textContent='正在读取所选周期与时段…';el('us-chart-note').textContent='';el('us-chart-volume').textContent='源日累计成交量：—';el('us-chart-status').textContent='正在读取所选标的图表…';}
  async function load(force=false){
    if(busy&&!force)return;busy=true;const id=++requestId;if(controller)controller.abort();controller=new AbortController();const ctl=controller;
    el('us-now').disabled=true;const timeout=setTimeout(()=>ctl.abort(),12000);
    try{
      const params=new URLSearchParams({symbols:symbols().join(','),period:el('us-period').value,session:el('us-session').value,provider:el('us-provider').value});
      const response=await fetch('/api/us-radar?'+params,{signal:ctl.signal,cache:'no-store'}),data=await response.json();
      if(id!==requestId)return;if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);
      items=data.items;render();el('us-status').textContent=`${data.status==='running'?'正在采集':'行情快照'} · 采集 ${time(data.generated_at)} · ${data.note} · 覆盖数量不代表时段完整${paused?' · 页面刷新已暂停':''}`;schedule(data.status==='running');
    }catch(e){if(id===requestId){items=items.map(r=>({...r,valid:false}));render();el('us-status').textContent=`行情读取失败：${e.message} · 上次快照仅供参考`;schedule();}}
    finally{clearTimeout(timeout);if(id===requestId){busy=false;el('us-now').disabled=false;}}
  }
  for(const id of ['us-period','us-session','us-provider'])el(id).addEventListener('change',()=>{clear();load(true);});
  el('us-refresh').addEventListener('change',()=>schedule());el('us-now').addEventListener('click',()=>{load();if(detailKey)loadDetail(detailKey);});
  el('us-pause').addEventListener('click',()=>{paused=!paused;el('us-pause').textContent=paused?'继续':'暂停';schedule();if(detailTimer!==null)clearTimeout(detailTimer);if(!paused){load();if(detailKey)loadDetail(detailKey);}});
  el('us-prev').addEventListener('click',()=>{page--;clear();load(true);});el('us-next').addEventListener('click',()=>{page++;clear();load(true);});
  el('us-search-form').addEventListener('submit',event=>{event.preventDefault();const code=el('us-search').value.trim().toUpperCase();if(!/^[A-Z][A-Z0-9.\-]{0,11}$/.test(code)){el('us-status').textContent='请输入有效美股代码，例如 NVDA';return;}search=code;clear();load(true);});
  el('us-reset').addEventListener('click',()=>{search='';el('us-search').value='';page=0;clear();load(true);});
  setInterval(()=>{el('us-clock').textContent=time(Date.now());if(items.length)render();},1000);
  fetch('/api/watchlist').then(r=>r.json()).then(data=>{const list=Array.isArray(data)?data:(data.symbols||data.watchlist||[]);if(list.length){watchlist=list.map(s=>typeof s==='string'?s:s.symbol).filter(s=>/^[A-Z][A-Z0-9.\-]{0,11}$/.test(s));if(!watchlist.length)watchlist=['NVDA','MU'];}load(true);}).catch(()=>load());
})();
