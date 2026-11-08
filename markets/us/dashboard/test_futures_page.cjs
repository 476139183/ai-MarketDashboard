const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function page(){
 const html=fs.readFileSync(path.join(__dirname,'static/futures.html'),'utf8'),nodes=new Map(),requests=[],pending=[],charts=[];
 const element=()=>({value:'',textContent:'',innerHTML:'',disabled:false,handlers:{},classList:{toggle(){}},addEventListener(n,f){this.handlers[n]=f;}});
 for(const [,id] of html.matchAll(/id="([^"]+)"/g))nodes.set(id,element());
 nodes.get('futures-period').value='day';nodes.get('futures-group').value='all';
 const row={symbol:'RB0',name:'<img onerror=bad>',exchange:'SHFE',contract_label:'主力连续',price_unit:'元/吨',group:'黑色',price:3000,quote_at:1,volume:null,open_interest:100,change_pct:null};
 const context=vm.createContext({document:{getElementById:id=>nodes.get(id)},window:{MarketChart:class{clear(){charts.push('clear');}render(r){charts.push(r.symbol);}}},URLSearchParams,Date,Number,Math,AbortController,setTimeout:()=>1,clearTimeout(){},
 fetch:(url,options)=>new Promise(resolve=>{requests.push({url,options});if(url==='/api/futures')resolve({ok:true,json:async()=>({status:'idle',items:[row]})});else pending.push({url,resolve});})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'static/futures.js'),'utf8'),context);
 return{nodes,requests,pending,charts,settle:()=>new Promise(r=>setImmediate(r)),resolve(i,data){pending[i].resolve({ok:true,json:async()=>data});}};
}
const chart=symbol=>({symbol,name:symbol,period:'day',continuous:true,candles:[{time:1,close:3}],analysis:{label:'历史结构偏强',count:60,evidence:[]},status:'idle'});
test('只读加载真实期货接口，缺值与来源内容安全展示',async()=>{const p=page();await p.settle();assert.ok(p.requests.every(r=>!r.options?.method));assert.match(p.nodes.get('futures-rows').innerHTML,/&lt;img/);assert.doesNotMatch(p.nodes.get('futures-rows').innerHTML,/<img/);assert.match(p.nodes.get('futures-rows').innerHTML,/—/);assert.ok(!p.requests.some(r=>/paper|analysis\//.test(r.url)));});
test('切换合约立即清空旧图，迟到响应不能覆盖新合约',async()=>{const p=page();await p.settle();p.nodes.get('futures-symbol').value='AU0';p.nodes.get('futures-search').handlers.submit({preventDefault(){}});await p.settle();assert.ok(p.charts.includes('clear'));p.resolve(1,chart('AU0'));await p.settle();p.resolve(0,chart('RB0'));await p.settle();assert.equal(p.charts.at(-1),'AU0');assert.match(p.nodes.get('futures-selected').textContent,/AU0/);});
test('失败保留同合约快照并标出错误，切换后不留旧摘要',async()=>{const p=page();await p.settle();p.resolve(0,chart('RB0'));await p.settle();await p.nodes.get('futures-refresh').handlers.click();await p.settle();p.resolve(1,{...chart('RB0'),status:'error',stale:true,error:'源不可用'});await p.settle();assert.match(p.nodes.get('futures-chart-status').textContent,/源不可用/);p.nodes.get('futures-symbol').value='AU0';p.nodes.get('futures-search').handlers.submit({preventDefault(){}});assert.doesNotMatch(p.nodes.get('futures-analysis').textContent,/历史结构偏强/);});
