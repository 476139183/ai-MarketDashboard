(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let state = null, timer = null, busy = false;
  const num = (value, digits = 2) => value == null || !Number.isFinite(value) ? '—'
    : value.toLocaleString('en-US', {maximumFractionDigits:digits});
  const time = value => value == null ? '—' : new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false});
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const color = value => value > 0 ? 'bullish' : value < 0 ? 'bearish' : '';
  const side = position => position.side === 'long' ? '做多' : '做空';

  function render() {
    el('paper-toggle').textContent = state.enabled ? '暂停新开仓' : '启用自动模拟';
    el('paper-toggle').setAttribute('aria-pressed', String(state.enabled));
    el('paper-running').textContent = state.enabled ? '自动模拟运行中 · OKX 合约'
      : state.positions.length ? '新开仓已暂停 · 现有持仓继续管理' : '自动模拟尚未启用';
    el('paper-running').className = state.enabled ? 'paper-running' : 'paper-paused';
    el('paper-status').textContent = state.runner_error || state.error
      ? `后台等待恢复：${state.runner_error || state.error}`
      : `最近检查 ${time(state.updated_at)} · 本地服务运行时自动检查${state.valuation_fresh ? '' : ' · 部分持仓报价过期，权益为上次估值'}`;
    el('paper-status').className = state.runner_error || state.error || !state.valuation_fresh ? 'error' : '';
    el('equity-label').textContent = state.valuation_fresh ? '估算权益 / USDT' : '上次估算权益 / USDT';
    for (const [id, value] of [['equity',state.equity],['cash',state.cash],['realized',state.realized_pnl],['unrealized',state.unrealized_pnl]]) {
      el(`paper-${id}`).textContent = num(value);
      if (id === 'realized' || id === 'unrealized') el(`paper-${id}`).className = color(value);
    }
    el('paper-position-count').textContent = `当前持仓 ${state.positions.length} / ${state.rules.max_positions}`;
    el('paper-pending').textContent = `${state.pending_signals} 个信号等待后续报价 / 仓位额度`;
    el('paper-position-note').textContent = state.valuation_fresh ? '按最新可成交侧报价估值，交易所原始合约代码。' : '部分报价过期；保留上次估值，暂停相应合约撮合。';
    const r = state.rules;
    el('paper-rule-tags').replaceChildren(...[
      `初始本金 ${num(r.initial_balance)} USDT`, `单笔预算 ${num(r.order_budget)} USDT`,
      `${r.leverage} 倍名义敞口 / 最多 ${r.max_positions} 仓`,
      `止损 ${num(r.stop_pct * 100)}% / 止盈 ${num(r.target_pct * 100)}%`,
      `每边手续费 ${num(r.fee_rate * 100, 3)}% / 滑点 ${num(r.slippage_rate * 100, 3)}%`,
    ].map(text => {const span=document.createElement('span');span.textContent=text;return span;}));
    el('paper-positions').replaceChildren(...state.positions.map(position => {
      const tr=document.createElement('tr');
      tr.innerHTML=`<td><strong>${escape(position.instrument_id)}</strong><small>${escape(position.name)}</small></td>
        <td>${side(position)}</td><td>${num(position.contracts,6)} 张<small>${num(position.quantity,8)} 基础币</small></td>
        <td>${num(position.entry_price,6)}</td><td>${num(position.last_price,6)}</td>
        <td class="${color(position.unrealized_pnl)}">${num(position.unrealized_pnl)}</td>
        <td>${num(position.stop_price,6)}<small>${num(position.target_price,6)}</small></td>
        <td>${escape(time(position.entry_time))}</td><td class="${position.quote_fresh?'':'stale'}">${position.quote_fresh?'新鲜':'过期 / 暂停撮合'}<small>${escape(time(position.last_quote_at))}</small></td>`;
      return tr;
    }));
    el('paper-positions-empty').hidden=state.positions.length>0;
    const reasons={stop_loss:'止损',take_profit:'止盈',signal_invalid:'信号失效'};
    el('paper-trades').replaceChildren(...state.trades.map(trade=>{
      const tr=document.createElement('tr');
      tr.innerHTML=`<td>${escape(trade.instrument_id)}</td><td>${side(trade)}</td>
        <td>${num(trade.entry_price,6)}<small>${num(trade.exit_price,6)}</small></td>
        <td>${escape(time(trade.entry_time))}</td><td>${escape(time(trade.exit_time))}</td>
        <td>${escape(reasons[trade.exit_reason]||trade.exit_reason)}</td><td>${num(trade.fees,4)}</td>
        <td class="${color(trade.net_pnl)}">${num(trade.net_pnl)}</td>`;
      return tr;
    }));
    el('paper-trades-empty').hidden=state.trades.length>0;
    el('paper-trade-summary').textContent = `${state.total_trades} 笔已平仓${state.total_trades ? ` · 胜率 ${num(state.winning_trades / state.total_trades * 100)}%` : ''} · 最近 100 条`;
    el('paper-fees').textContent = `累计费用 ${num(state.fees,4)} USDT`;
  }

  async function request(enabled) {
    if(busy)return;
    busy=true; el('paper-toggle').disabled=true;el('paper-refresh').disabled=true;
    if(timer!==null)clearTimeout(timer);
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),12000);
    let succeeded=false;
    try{
      const control=typeof enabled==='boolean';
      const options=control?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})}:{};
      const response=await fetch(control?'/api/paper/control':'/api/paper',{...options,signal:controller.signal,cache:'no-store'});
      const data=await response.json();
      if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);
      if(!Array.isArray(data.positions)||!Array.isArray(data.trades))throw Error('无效的账户响应');
      state=data;render();succeeded=true;
    }catch(error){
      el('paper-status').textContent=`读取 / 控制失败：${error.message} · 画面保留上次快照，运行状态待核实`;
      el('paper-status').className='error';
    }finally{
      clearTimeout(timeout);busy=false;el('paper-refresh').disabled=false;el('paper-toggle').disabled=!succeeded;
      timer=setTimeout(()=>request(),3000);
    }
  }
  el('paper-refresh').addEventListener('click',()=>request());
  el('paper-toggle').addEventListener('click',()=>request(!state.enabled));
  request();
})();
