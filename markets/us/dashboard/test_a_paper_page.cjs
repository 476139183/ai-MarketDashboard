const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function page(){
 const html=fs.readFileSync(path.join(__dirname,'static/a-paper.html'),'utf8'),nodes=new Map(),requests=[],pending=[];
 const element=()=>({value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,handlers:{},addEventListener(n,f){this.handlers[n]=f;}});
 for(const [,id] of html.matchAll(/id="([^"]+)"/g))nodes.set(id,element());
 const context=vm.createContext({document:{getElementById:id=>nodes.get(id)},Date,Number,JSON,URLSearchParams,AbortController,Object,setTimeout:()=>1,clearTimeout(){},
 fetch:(url,options)=>new Promise(resolve=>{requests.push({url,options});pending.push(resolve);})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'static/a-paper.js'),'utf8'),context);
 return{nodes,requests,settle:()=>new Promise(r=>setImmediate(r)),reply(i,data,ok=true){pending[i]({ok,json:async()=>data});}};
}
const state=(enabled=false)=>({market:'a_share',currency:'CNY',enabled,cash:100000,equity:100000,market_value:0,unrealized_pnl:0,positions:[],plans:[],trades:[],total_trades:0,winning_trades:0,realized_pnl:0,fees:0,valuation_fresh:true,source_candidate_count:0,config:{per_stock_budget:20000,max_positions:3,commission_rate:.0003,min_commission:5,transfer_rate:.00001,stamp_rate:.0005,slippage_ticks:1,exit_ma:10}});
test('A股页面只读加载，显式点击后才启用',async()=>{const p=page();p.reply(0,state());await p.settle();assert.equal(p.requests[0].url,'/api/a-paper');assert.equal(p.requests[0].options.method,undefined);p.nodes.get('ap-toggle').handlers.click();assert.equal(p.requests[1].url,'/api/a-paper/control');assert.equal(JSON.parse(p.requests[1].options.body).enabled,true);});
test('T+1和来源安全展示，计划摘要不混入其他市场账户',async()=>{const p=page();const s=state();s.positions=[{code:'600000',name:'<img src=x>',shares:100,sellable_shares:0,entry_price:10,last_price:10,stop:9,target:12}];p.reply(0,s);await p.settle();assert.match(p.nodes.get('ap-positions').innerHTML,/T\+1/);assert.match(p.nodes.get('ap-positions').innerHTML,/&lt;img/);assert.equal(p.nodes.get('ap-save').disabled,true);});
test('保存配置可调整退出均线，失败保留状态待核实',async()=>{const p=page();p.reply(0,state());await p.settle();p.nodes.get('ap-exit_ma').value='20';p.nodes.get('ap-form').handlers.submit({preventDefault(){}});assert.equal(JSON.parse(p.requests[1].options.body).exit_ma,20);p.reply(1,{error:'offline'},false);await p.settle();assert.match(p.nodes.get('ap-status').textContent,/待核实/);assert.equal(p.nodes.get('ap-toggle').disabled,true);});
