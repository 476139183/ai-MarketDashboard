const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

function page(search='') {
  const now = Date.now(), nodes = new Map(), timers = new Map();
  function element() { return {value:'', textContent:'', innerHTML:'', hidden:false, disabled:false,
    dataset:{}, children:[], handlers:{}, setAttribute(){}, classList:{toggle(){}},
    appendChild(child){this.children.push(child);}, replaceChildren(...children){this.children=children;},
    addEventListener(event,fn){this.handlers[event]=fn;}}; }
  const html = readFileSync(join(__dirname,'static/contracts.html'),'utf8');
  for(const [,id] of html.matchAll(/id="([^"]+)"/g)) nodes.set(id,element());
  nodes.get('refresh-interval').value='10'; nodes.get('direction-filter').value='all';
  const groups=['all','crypto','stock'].map(group=>Object.assign(element(),{dataset:{group}}));
  let fail=false, requests=0, clock=now; const urls=[];
  const item=(symbol,group,direction)=>({symbol,name:symbol,group,direction,label:direction==='bullish'?'看涨':'看跌',
    source:'OKX',category:group==='crypto'?'main':'stock',category_label:group==='crypto'?'主流':'股票合约',price:100,quote_at:now,quote_fresh:true,structure_fresh:true,candle_close_at:Math.floor(now/14400000)*14400000,
    change_pct:5,funding_pct:.01,open_interest_usdt:1000000,volume_usdt:2000000,reasons:['结构依据'],
    invalidation:'4小时收盘确认',ema20:90,ema50:80,support:75,resistance:110,candles:[]});
  const items=[item('BTCUSDT','crypto','bullish'),item('MUUSDT','stock','bearish')];
  class Clock extends Date {static now(){return clock;}}
  const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:element,
    querySelectorAll:()=>groups},window:{},location:{search},Date:Clock,Intl,AbortController,
    setTimeout:fn=>{const id=timers.size+1;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
    setInterval:fn=>{context.expire=fn;},
    fetch:async(url)=>{urls.push(url);requests++;if(fail)throw Error('offline');const source=url.includes('bitget')?'Bitget':url.includes('binance')?'Binance':'OKX';const selected=source==='Binance'?items.filter(row=>row.group==='crypto'):items;return {ok:true,json:async()=>({source,items:selected.map(row=>({...row,source})),status:'idle',total:selected.length,generated_at:now,error:null})};}});
  vm.runInContext(readFileSync(join(__dirname,'static/contracts.js'),'utf8'),context);
  return {nodes,groups,timers,items,urls,settle:()=>new Promise(resolve=>setImmediate(resolve)),
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

test('默认欧易，切换 Bitget 后来源和价格口径一起切换',async()=>{
  const p=page();await p.settle();
  assert.match(p.urls[0],/exchange=okx/);
  assert.match(p.nodes.get('detail-source').textContent,/OKX/);
  p.nodes.get('exchange-source').value='bitget';
  await p.nodes.get('exchange-source').handlers.change();await p.settle();
  assert.match(p.urls.at(-1),/exchange=bitget/);
  assert.match(p.nodes.get('detail-source').textContent,/Bitget/);
});

test('币安切换保留正确来源与模拟入口，分类仅过滤加密合约',async()=>{
  const p=page();await p.settle();
  p.nodes.get('exchange-source').value='binance';
  await p.nodes.get('exchange-source').handlers.change();await p.settle();
  assert.match(p.nodes.get('detail-source').textContent,/Binance/);
  assert.match(p.nodes.get('paper-link').href,/exchange=binance/);
  assert.equal(p.nodes.get('contract-rows').children.length,1);
  p.nodes.get('category-filter').value='defi';p.nodes.get('category-filter').handlers.change();
  assert.equal(p.nodes.get('contract-rows').children.length,0);
  p.nodes.get('category-filter').value='main';p.nodes.get('category-filter').handlers.change();
  assert.equal(p.nodes.get('contract-rows').children.length,1);
});

test('从币安模拟账户进入信号页时保留显式来源，普通入口仍默认欧易',async()=>{
 const p=page('?exchange=binance');await p.settle();
 assert.match(p.urls[0],/exchange=binance/);
 assert.match(p.nodes.get('detail-source').textContent,/Binance/);
});


test('分钟周期切换同时更改 API 参数、详情与表格口径',async()=>{
 const p=page();await p.settle();
 p.nodes.get('signal-period').value='10';await p.nodes.get('signal-period').handlers.change();await p.settle();
 assert.match(p.urls.at(-1),/period=10/);
 assert.match(p.nodes.get('chart-summary').textContent,/10 分钟/);
 assert.match(p.nodes.get('contract-rows').children[0].innerHTML,/10 分钟结构/);
});
