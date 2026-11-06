const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

function page() {
  const now = Date.now(), nodes = new Map(), timers = new Map();
  function element() { return {value:'', textContent:'', innerHTML:'', hidden:false, disabled:false,
    dataset:{}, children:[], handlers:{}, setAttribute(){}, classList:{toggle(){}},
    appendChild(child){this.children.push(child);}, replaceChildren(...children){this.children=children;},
    addEventListener(event,fn){this.handlers[event]=fn;}}; }
  const html = readFileSync(join(__dirname,'static/contracts.html'),'utf8');
  for(const [,id] of html.matchAll(/id="([^"]+)"/g)) nodes.set(id,element());
  nodes.get('refresh-interval').value='10'; nodes.get('direction-filter').value='all';
  const groups=['all','crypto','stock'].map(group=>Object.assign(element(),{dataset:{group}}));
  let fail=false, requests=0, clock=now;
  const item=(symbol,group,direction)=>({symbol,name:symbol,group,direction,label:direction==='bullish'?'看涨':'看跌',
    price:100,quote_at:now,quote_fresh:true,structure_fresh:true,candle_close_at:Math.floor(now/14400000)*14400000,
    change_pct:5,funding_pct:.01,open_interest_usdt:1000000,volume_usdt:2000000,reasons:['结构依据'],
    invalidation:'4小时收盘确认',ema20:90,ema50:80,support:75,resistance:110,candles:[]});
  const items=[item('BTCUSDT','crypto','bullish'),item('MUUSDT','stock','bearish')];
  class Clock extends Date {static now(){return clock;}}
  const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:element,
    querySelectorAll:()=>groups},window:{},Date:Clock,Intl,AbortController,
    setTimeout:fn=>{const id=timers.size+1;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
    setInterval:fn=>{context.expire=fn;},
    fetch:async()=>{requests++;if(fail)throw Error('offline');return {ok:true,json:async()=>({items,status:'idle',total:2,generated_at:now,error:null})};}});
  vm.runInContext(readFileSync(join(__dirname,'static/contracts.js'),'utf8'),context);
  return {nodes,groups,timers,items,settle:()=>new Promise(resolve=>setImmediate(resolve)),
    fail(){fail=true;},get requests(){return requests;},advance(ms){clock+=ms;context.expire();}};
}

test('合约页加载、搜索、分组及选择标的不调用股票行情',async()=>{
  const p=page();await p.settle();
  assert.equal(p.nodes.get('contract-rows').children.length,2);
  p.nodes.get('contract-rows').children[1].handlers.click();
  assert.equal(p.nodes.get('detail-symbol').textContent,'MUUSDT');
  p.nodes.get('contract-search').value='BTC';p.nodes.get('contract-search').handlers.input();
  assert.equal(p.nodes.get('contract-rows').children.length,1);
  p.groups[2].handlers.click();
  assert.equal(p.nodes.get('contract-rows').children.length,0);
  assert.equal(p.nodes.get('empty-results').hidden,false);
  assert.equal(p.requests,1);
});

test('报价到期自动撤销方向，刷新失败保留价格并提示',async()=>{
  const p=page();await p.settle();
  assert.equal(p.nodes.get('bullish-count').textContent,1);
  p.advance(31000);
  assert.equal(p.nodes.get('bullish-count').textContent,0);
  assert.equal(p.nodes.get('detail-direction').textContent,'待确认');
  p.fail();await p.nodes.get('refresh-now').handlers.click();
  assert.match(p.nodes.get('connection-status').textContent,/offline/);
  assert.equal(p.nodes.get('contract-rows').children.length,2);
});

test('暂停取消轮询，恢复只安排一个刷新任务',async()=>{
  const p=page();await p.settle();
  assert.equal(p.timers.size,1);
  p.nodes.get('pause-refresh').handlers.click();assert.equal(p.timers.size,0);
  p.nodes.get('pause-refresh').handlers.click();assert.equal(p.timers.size,1);
});
