(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const num=(v,n=2)=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:n}):'—';
  const pct=v=>Number.isFinite(v)?num(v)+'%':'—';
  const date=v=>Number.isFinite(v)?new Date(v).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}):'—';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reasons={signal:'信号变化',gap_stop:'跳空止损',atr_stop:'ATR保护',sample_end:'样本结束',margin_exit:'保证金退出'};
  let version=0,timer,controller,result=null,equityChart,series,specs={};

  async function api(url,body){
    controller=new AbortController();const ctl=controller,timeout=setTimeout(()=>ctl.abort(),12000);
    try{
      const response=await fetch(url,{cache:'no-store',signal:ctl.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
      const data=await response.json();if(!response.ok)throw Error(data.error||'回测请求失败');return data;
    }finally{clearTimeout(timeout);}
  }
  function invalidate(message=true){
    version++;clearTimeout(timer);if(controller)controller.abort();
    result=null;el('bt-results').hidden=true;el('bt-export').disabled=true;el('bt-run').disabled=false;
    if(series)series.setData([]);
    if(message)el('bt-status').textContent='参数已变更，请重新运行回测。';
  }
  function showStrategy(key){
    const row=result.results.find(r=>r.strategy===key)||result.results[0];
    el('bt-selected').textContent=row.label+' · 收盘权益曲线';
    el('bt-warnings').innerHTML=(row.warnings||[]).map(s=>`<li>${esc(s)}</li>`).join('');
    el('bt-trades').innerHTML=row.trades.length?row.trades.map(t=>`<tr><td>${t.direction==='long'?'多':'空'}</td><td>${date(t.entry_at)}</td><td>${date(t.exit_at)}</td><td>${num(t.entry_price)} / ${num(t.exit_price)}</td><td>${num(t.entry_fee+t.exit_fee)}</td><td>${num(t.net_pnl)}</td><td>${esc(reasons[t.reason]||t.reason)}</td></tr>`).join(''):'<tr><td colspan="7">没有完整交易；请同时检查保证金拒单及未平头寸提示。</td></tr>';
    const points=row.curve||[];el('bt-curve-readout').textContent=points.length?`${date(points.at(-1).time)} · 权益 ${num(points.at(-1).equity)} 元`:'暂无曲线';
    const L=window.LightweightCharts;if(!L)return;
    if(!equityChart){
      equityChart=L.createChart(el('bt-equity'),{autoSize:true,layout:{background:{color:'#182126'},textColor:'#a1b4bf'},grid:{vertLines:{color:'#26333b'},horzLines:{color:'#26333b'}},localization:{timeFormatter:t=>date(t*1000)}});
      series=equityChart.addSeries(L.LineSeries,{color:'#dfbc7c',lineWidth:2,priceFormat:{type:'price',precision:2,minMove:.01}});
    }
    series.setData(points.map(p=>({time:p.time/1000,value:p.equity})));equityChart.timeScale().fitContent();
  }
  function paint(data){
    result=data;el('bt-results').hidden=false;el('bt-export').disabled=false;
    const cfg=result.config,first=result.results[0].summary;
    el('bt-sample').textContent=`${cfg.symbol} · ${date(first.start_at)} 至 ${date(first.end_at)} · ${first.bars||'—'} 根回测日线 / ${first.warmup_bars||'—'} 根预热 · ${result.version} · 样本指纹 ${result.sample_sha256.slice(0,12)}`;
    el('bt-comparison').innerHTML=result.results.map(r=>`<tr><td><button type="button" data-strategy="${esc(r.strategy)}">${esc(r.label)}</button></td><td>${pct(r.summary.return_pct)}</td><td>${pct(r.summary.max_drawdown_pct)}</td><td>${num(r.summary.trade_count,0)}</td><td>${pct(r.summary.win_rate_pct)}</td><td>${num(r.summary.fees)}</td><td>${pct(r.stress.summary.return_pct)}</td><td>${pct(r.tail.summary.return_pct)}</td></tr>`).join('');
    el('bt-assumptions').textContent=`本次参数：本金 ${num(cfg.capital)} 元；${cfg.lots} 手；乘数 ${cfg.multiplier}；最小价位 ${cfg.tick_size}；保证金 ${pct(cfg.margin_rate*100)}；占用上限 ${pct(cfg.max_margin_fraction*100)}；每边费用 ${num(cfg.fee_fixed)} 元/手 + 成交额万分之 ${num(cfg.fee_rate*10000,4)}；平今倍数 ${cfg.close_today_factor}；每边滑点 ${cfg.slippage_ticks} 跳。`;
    el('bt-limitations').innerHTML=(result.limitations||[]).map(s=>`<li>${esc(s)}</li>`).join('');
    showStrategy(result.results[0].strategy);
  }
  async function receive(job,id,started){
    if(id!==version)return;
    if(job.status==='failed')throw Error(job.error||'回测失败');
    if(job.status==='done'){
      paint(job.result);el('bt-status').textContent='回测完成；结果对应下方已运行参数，不代表未来收益。';el('bt-run').disabled=false;return;
    }
    if(!job.id)throw Error('回测任务未返回ID');
    if(Date.now()-started>90000)throw Error('回测查询超时，可重新运行；后台任务可能仍在完成。');
    el('bt-status').textContent='正在读取月份合约日线并运行两个策略…';
    timer=setTimeout(async()=>{
      try{const next=await api('/api/futures/backtest?'+new URLSearchParams({id:job.id}));await receive(next,id,started);}
      catch(error){fail(error,id);}
    },1000);
  }
  function fail(error,id){if(id!==version)return;el('bt-status').textContent='回测失败：'+error.message;el('bt-run').disabled=false;}
  el('bt-form').addEventListener('submit',async event=>{
    event.preventDefault();invalidate(false);const id=version;
    const symbol=el('bt-symbol').value.trim().toUpperCase();
    if(!/^[A-Z]{1,2}[0-9]{4}$/.test(symbol)){el('bt-status').textContent='请输入四位月份合约，如RB2701；连续合约不能回测。';return;}
    const value=id=>Number(el('bt-'+id).value);
    const params={symbol,capital:value('capital'),lots:value('lots'),multiplier:value('multiplier'),tick_size:value('tick'),margin_rate:value('margin')/100,max_margin_fraction:value('occupancy')/100,fee_fixed:value('fixed'),fee_rate:value('rate')/10000,close_today_factor:value('today'),slippage_ticks:value('slippage'),start:el('bt-start').value,end:el('bt-end').value};
    el('bt-run').disabled=true;el('bt-status').textContent='正在提交回测任务…';
    try{const started=Date.now();const job=await api('/api/futures/backtest',params);await receive(job,id,started);}
    catch(error){fail(error,id);}
  });
  el('bt-form').addEventListener('input',()=>invalidate());
  el('bt-symbol').addEventListener('change',()=>{
    const root=el('bt-symbol').value.trim().toUpperCase().match(/^[A-Z]+/)?.[0],spec=specs[root];
    if(spec){el('bt-multiplier').value=spec.multiplier;el('bt-tick').value=spec.tick_size;}
  });
  el('bt-comparison').addEventListener('click',event=>{const button=event.target.closest('[data-strategy]');if(button&&result)showStrategy(button.dataset.strategy);});
  el('bt-export').addEventListener('click',()=>{
    if(!result)return;const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=`futures-backtest-${result.config.symbol}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  // Metadata only; entering the page never starts a backtest or changes any account.
  api('/api/futures/backtest').then(data=>{specs=data.specs||{};}).catch(error=>{if(version===0)el('bt-status').textContent='回测配置读取失败：'+error.message;});
})();
