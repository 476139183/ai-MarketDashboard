(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=value=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:4}):'—';
  const stamp=value=>Number.isFinite(value)?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供';
  const fields=['per_stock_budget','max_positions','exit_ma','slippage_ticks','commission_rate','min_commission','transfer_rate','stamp_rate'];
  const ratios=['commission_rate','transfer_rate','stamp_rate'];
  const reasons={stop:'计划止损',target:'计划止盈',ma10_exit:'完整日线跌破退出均线'};
  let state=null,busy=false,dirty=false,formLoaded=false,timer,controller;
  function locks(verified=true){
    const editable=verified&&state&&!state.enabled&&!state.positions.length&&!busy;
    for(const key of fields)el('ap-'+key).disabled=!editable;
    el('ap-save').disabled=!editable;
    el('ap-refresh').disabled=busy;
    el('ap-toggle').disabled=busy||!verified||!state||(!state.enabled&&dirty);
  }
  function fillForm(){
    for(const key of fields)el('ap-'+key).value=ratios.includes(key)?state.config[key]*100:state.config[key];
    formLoaded=true;
  }
  function render(){
    el('ap-toggle').textContent=state.enabled?'暂停新买入':'启用A股自动模拟';
    el('ap-running').textContent=state.enabled?'A股自动模拟运行中 · 盘后准备/盘中执行':state.positions.length?'新买入暂停 · 旧持仓按T+1与策略管理':'A股自动模拟尚未启用';
    el('ap-status').textContent=[state.runner_error||state.error,state.scan_error,state.note,'最近检查 '+stamp(state.updated_at)].filter(Boolean).join(' · ');
    el('ap-equity-label').textContent=state.valuation_fresh?'估算权益 / CNY':'上次估算权益 / CNY';
    for(const [id,value] of [['equity',state.equity],['cash',state.cash],['realized',state.realized_pnl],['unrealized',state.unrealized_pnl]])el('ap-'+id).textContent=num(value);
    el('ap-value').textContent='股票市值 '+num(state.market_value)+' CNY';
    el('ap-win-rate').textContent='已平仓胜率 '+(state.total_trades?num(100*state.winning_trades/state.total_trades)+'%':'—');
    el('ap-pending').textContent=Object.keys(state.pending||{}).length+' 个计划等待后续报价';
    el('ap-plan-status').textContent='盘后名单 '+(state.source_plan_date||state.plan_date||'未生成')+' · 策略候选 '+(state.source_candidate_count||0)+' 个 · '+(state.market_label||'')+(state.plan_error?' · '+state.plan_error:'');
    el('ap-plans').innerHTML=(state.plans||[]).map(p=>`<span>${esc(p.name)} ${esc(p.code)} · 回踩 ${num(p.lower)}–${num(p.upper)}</span>`).join('');
    el('ap-position-note').textContent=state.valuation_fresh?'上次报价估值；新买持仓T+1解锁后仍需符合策略才卖出':'报价过期或口径待核对，保留上次估值，未假定可卖出';
    el('ap-positions').innerHTML=state.positions.map(p=>`<tr><td>${esc(p.name)} ${esc(p.code)}<small>候选 ${esc(p.signal_date)}</small></td><td>${num(p.shares)} / ${num(p.sellable_shares)}<small>${p.sellable_shares?'满足T+1，仍须策略触发':'T+1锁定'}</small></td><td>${num(p.entry_price)}<small>${num(p.last_price)}</small></td><td>${num(p.stop)}<small>${num(p.target)}</small></td><td>${num(p.unrealized_pnl)}</td><td>${stamp(p.entry_at)}</td><td>${stamp(p.quote_at)}</td></tr>`).join('');
    el('ap-positions-empty').hidden=state.positions.length>0;
    el('ap-trades').innerHTML=[...state.trades].reverse().map(t=>`<tr><td>${esc(t.name)} ${esc(t.code)}<small>${num(t.shares)} 股 · 候选 ${esc(t.signal_date)}</small></td><td>${num(t.entry_price)}<small>${num(t.exit_price)}</small></td><td>${stamp(t.entry_at)}</td><td>${stamp(t.exit_at)}</td><td>${esc(reasons[t.reason]||t.reason)}</td><td>${num(t.fees)}</td><td>${num(t.net_pnl)}<small>${num(t.return_pct)}%</small></td></tr>`).join('');
    el('ap-trades-empty').hidden=state.trades.length>0;
    el('ap-trade-summary').textContent=state.total_trades+' 笔已平仓 · 最近100条';
    el('ap-fees').textContent='累计费税 '+num(state.fees)+' CNY';
    if(!formLoaded||!dirty)fillForm();
    el('ap-config-status').textContent=state.enabled||state.positions.length?'暂停且空仓后可修改参数':dirty?'参数尚未保存，请先保存再启用':'参数已读取；保存后仍需手动启用';
  }
  async function request(action,body){
    if(busy)return;
    clearTimeout(timer);busy=true;locks();
    if(controller)controller.abort();controller=new AbortController();
    const ctl=controller,timeout=setTimeout(()=>ctl.abort(),12000);let verified=false;
    try{
      const options=action?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
      const response=await fetch('/api/a-paper'+(action?'/'+action:''),{...options,signal:ctl.signal,cache:'no-store'});
      const data=await response.json();
      if(!response.ok)throw Error(data.error||'账户读取失败');
      if(data.currency!=='CNY'||data.market!=='a_share'||!Array.isArray(data.positions)||!Array.isArray(data.trades))throw Error('A股账户身份或响应不一致');
      state=data;if(action==='config'){dirty=false;formLoaded=false;}
      render();verified=true;
      if(action==='config')el('ap-config-status').textContent='配置已保存，账户仍暂停；可手动启用';
    }catch(error){el('ap-status').textContent='读取 / 控制失败：'+error.message+' · 保留上次快照，运行状态待核实';}
    finally{clearTimeout(timeout);busy=false;locks(verified);timer=setTimeout(()=>request(),5000);}
  }
  function changed(){dirty=true;locks();el('ap-config-status').textContent='参数尚未保存，请先保存再启用';}
  el('ap-form').addEventListener('input',changed);
  el('ap-form').addEventListener('submit',event=>{
    event.preventDefault();if(!state||state.enabled||state.positions.length)return;
    const cfg={};
    for(const key of fields)cfg[key]=Number(el('ap-'+key).value)/(ratios.includes(key)?100:1);
    request('config',cfg);
  });
  el('ap-toggle').addEventListener('click',()=>{if(state&&(!dirty||state.enabled))request('control',{enabled:!state.enabled});});
  el('ap-refresh').addEventListener('click',()=>request());
  el('ap-market').addEventListener('change',()=>{location.href=el('ap-market').value;});
  request();
})();
