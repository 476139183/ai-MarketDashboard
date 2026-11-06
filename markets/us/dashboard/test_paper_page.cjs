const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {test}=require('node:test');
const vm=require('node:vm');

function page(){
 const nodes=new Map(),requests=[],timers=new Map();let enabled=false,fail=false;
 const element=()=>({children:[],handlers:{},textContent:'',hidden:false,disabled:false,innerHTML:'',
  replaceChildren(...children){this.children=children;},addEventListener(name,fn){this.handlers[name]=fn;},setAttribute(){}});
 const html=readFileSync(join(__dirname,'static/paper.html'),'utf8');
 for(const [,id] of html.matchAll(/id="([^"]+)"/g))nodes.set(id,element());
 const state=()=>({enabled,cash:9000,equity:10005,realized_pnl:0,unrealized_pnl:5,fees:.6,
  valuation_fresh:true,pending_signals:2,total_trades:0,winning_trades:0,updated_at:Date.now(),
  rules:{initial_balance:10000,order_budget:1000,max_positions:3,fee_rate:.0006,slippage_rate:.0005,stop_pct:.02,target_pct:.04,leverage:1},
  positions:[{symbol:'BTCUSDT',instrument_id:'BTC-USDT-SWAP',side:'long',contracts:1,quantity:.01,
   entry_price:100,last_price:105,stop_price:98,target_price:104,entry_time:Date.now(),last_quote_at:Date.now(),unrealized_pnl:5,quote_fresh:true}],trades:[]});
 let timerId=0;
 const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:element},
  Date,Intl,AbortController,setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
  fetch:async(url,options={})=>{requests.push([url,options]);if(fail)throw Error('offline');
   if(options.method==='POST')enabled=JSON.parse(options.body).enabled;
   return {ok:true,json:async()=>state()};}});
 vm.runInContext(readFileSync(join(__dirname,'static/paper.js'),'utf8'),context);
 return {nodes,requests,timers,fail(){fail=true;},settle:()=>new Promise(resolve=>setImmediate(resolve))};
}

test('打开页面只读取账户，显式启用后发送本地模拟控制',async()=>{
 const p=page();await p.settle();
 assert.equal(p.requests.length,1);assert.equal(p.requests[0][0],'/api/paper');
 assert.equal(p.nodes.get('paper-positions').children.length,1);
 assert.match(p.nodes.get('paper-toggle').textContent,/启用/);
 await p.nodes.get('paper-toggle').handlers.click();
 assert.deepEqual(JSON.parse(p.requests.at(-1)[1].body),{enabled:true});
 assert.equal(p.requests.at(-1)[0],'/api/paper/control');
 assert.match(p.nodes.get('paper-toggle').textContent,/暂停/);
 assert.equal(p.timers.size,1);
});

test('连接失败保留仓位并标记快照，避免显示为已成功暂停',async()=>{
 const p=page();await p.settle();p.fail();
 await p.nodes.get('paper-refresh').handlers.click();
 assert.match(p.nodes.get('paper-status').textContent,/offline/);
 assert.equal(p.nodes.get('paper-positions').children.length,1);
 assert.equal(p.nodes.get('paper-toggle').disabled,true);
});
