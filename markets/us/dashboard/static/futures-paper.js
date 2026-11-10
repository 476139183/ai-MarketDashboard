(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=value=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:4}):'—';
  const stamp=value=>Number.isFinite(value)?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供';
  const fields=['symbol','lots','multiplier','tick_size','margin_rate','max_margin_fraction','fee_fixed','fee_rate','close_today_factor','slippage_ticks'];
  const ratios=['margin_rate','max_margin_fraction','fee_rate'];
  const reasons={atr_stop:'固定ATR保护价触发',signal_exit:'日线方向改变',margin_exit:'保证金不足'};
  let state=null,busy=false,dirty=false,formLoaded=false,timer,controller;
  function locks(verified=true){
    const editable=verified&&state&&!state.enabled&&!state.positions.length&&!busy;
    for(const key of fields)el('fp-'+key).disabled=!editable;
    el('fp-save').disabled=!editable;
    el('fp-refresh').disabled=busy;
    el('fp-toggle').disabled=busy||!verified||!state||(!state.enabled&&dirty);
  }
  function fillForm(){
    for(const key of fields)el('fp-'+key).value=ratios.includes(key)?state.config[key]*100:state.config[key];
    formLoaded=true;
  }
  function render(){
    el('fp-toggle').textContent=state.enabled?'暂停新开仓':'启用期货自动模拟';
    el('fp-running').textContent=state.enabled?'期货自动模拟运行中 · 日盘采样':state.positions.length?'新开仓已暂停 · 持仓继续日盘管理':'期货自动模拟尚未启用';
    el('fp-status').textContent=[state.runner_error||state.error,state.note,'最近检查 '+stamp(state.updated_at)].filter(Boolean).join(' · ');
    el('fp-equity-label').textContent=state.valuation_fresh?'估算权益 / CNY':'上次估算权益 / CNY';
    for(const [id,value] of [['equity',state.equity],['available',state.available],['realized',state.realized_pnl],['unrealized',state.unrealized_pnl]])el('fp-'+id).textContent=num(value);
    el('fp-margin').textContent='保证金占用 '+num(state.margin)+' CNY';
    el('fp-balance').textContent='账户余额 '+num(state.balance)+' CNY';
    el('fp-detail').href='/futures?'+new URLSearchParams({symbol:state.config.symbol,period:'day'});
    el('fp-pending').textContent=state.pending?'已观察日线信号，等待后续新报价':'无待成交信号';
    el('fp-position-note').textContent=state.valuation_fresh?'按最后收到的源最新价估值；非盘口成交保证':'报价过期，保留上次估值；等待有效日盘新报价';
    el('fp-positions').innerHTML=state.positions.map(p=>`<tr><td>${esc(p.symbol)}</td><td>${p.side===1?'做多':'做空'} · ${num(p.lots)} 手</td><td>${num(p.entry_price)}<small>${num(p.last_price)}</small></td><td>${num(p.stop)}</td><td>${num(p.unrealized_pnl)}</td><td>${stamp(p.entry_at)}</td><td>${stamp(p.quote_at)}</td></tr>`).join('');
    el('fp-positions-empty').hidden=state.positions.length>0;
    el('fp-trades').innerHTML=[...state.trades].reverse().map(t=>`<tr><td>${esc(t.symbol)}<small>${t.side===1?'做多':'做空'} · ${num(t.lots)} 手</small></td><td>${num(t.entry_price)}<small>${num(t.exit_price)}</small></td><td>${stamp(t.entry_at)}</td><td>${stamp(t.exit_at)}</td><td>${esc(reasons[t.reason]||t.reason)}</td><td>${num(t.fees)}${t.close_today?'<small>平今</small>':''}</td><td>${num(t.net_pnl)}</td></tr>`).join('');
    el('fp-trades-empty').hidden=state.trades.length>0;
    el('fp-trade-summary').textContent=state.total_trades+' 笔已平仓'+(state.total_trades?' · 胜率 '+num(100*state.winning_trades/state.total_trades)+'%':'')+' · 最近100条';
    el('fp-fees').textContent='累计费用 '+num(state.fees)+' CNY';
    if(!formLoaded||!dirty)fillForm();
    el('fp-config-status').textContent=state.enabled||state.positions.length?'暂停且空仓后可修改参数':dirty?'参数尚未保存，请先保存再启用':'参数已读取；保存后仍需手动启用';
  }
  async function request(action,body){
    if(busy)return;
    clearTimeout(timer);busy=true;locks();
    if(controller)controller.abort();controller=new AbortController();
    const ctl=controller,timeout=setTimeout(()=>ctl.abort(),12000);let verified=false;
    try{
      const options=action?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
      const response=await fetch('/api/futures/paper'+(action?'/'+action:''),{...options,signal:ctl.signal,cache:'no-store'});
      const data=await response.json();
      if(!response.ok)throw Error(data.error||'账户读取失败');
      if(data.currency!=='CNY'||data.market!=='domestic_futures'||!Array.isArray(data.positions)||!Array.isArray(data.trades))throw Error('期货账户身份或响应不一致');
      state=data;if(action==='config'){dirty=false;formLoaded=false;}
      render();verified=true;
      if(action==='config')el('fp-config-status').textContent='配置已保存，账户仍暂停；可手动启用';
    }catch(error){el('fp-status').textContent='读取 / 控制失败：'+error.message+' · 保留上次快照，运行状态待核实';}
    finally{clearTimeout(timeout);busy=false;locks(verified);timer=setTimeout(()=>request(),5000);}
  }
  function changed(){dirty=true;locks();el('fp-config-status').textContent='参数尚未保存，请先保存再启用';}
  el('fp-form').addEventListener('input',changed);
  el('fp-symbol').addEventListener('change',()=>{
    const symbol=el('fp-symbol').value.trim().toUpperCase(),root=symbol.match(/^[A-Z]+/)?.[0],spec=state?.specs?.[root];
    el('fp-symbol').value=symbol;
    if(spec){el('fp-multiplier').value=spec.multiplier;el('fp-tick_size').value=spec.tick_size;}
    changed();
  });
  el('fp-form').addEventListener('submit',event=>{
    event.preventDefault();if(!state||state.enabled||state.positions.length)return;
    const cfg={strategy:'sma'};
    for(const key of fields)cfg[key]=key==='symbol'?el('fp-'+key).value.trim().toUpperCase():Number(el('fp-'+key).value)/(ratios.includes(key)?100:1);
    request('config',cfg);
  });
  el('fp-toggle').addEventListener('click',()=>{if(state&&(!dirty||state.enabled))request('control',{enabled:!state.enabled});});
  el('fp-refresh').addEventListener('click',()=>request());
  el('fp-market').addEventListener('change',()=>{location.href=el('fp-market').value;});
  request();
})();
