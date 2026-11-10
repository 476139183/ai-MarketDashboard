const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function page(){
 const html=fs.readFileSync(path.join(__dirname,'static/a-paper.html'),'utf8'),nodes=new Map(),requests=[],pending=[];
 for(const [,id] of html.matchAll(/id="([^"]+)"/g))nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,handlers:{},addEventListener(k,f){this.handlers[k]=f;}});
 nodes.get('ar-budget').value='10000';nodes.get('ar-positions').value='5';nodes.get('ar-exit-ma').value='20';
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'static/a-paper-research.js'),'utf8'),{document:{getElementById:id=>nodes.get(id)},Number,JSON,Object,AbortController,encodeURIComponent,setTimeout:()=>1,clearTimeout(){},
 fetch:(url,options)=>new Promise(resolve=>{requests.push({url,options});pending.push(resolve);})});
 return{nodes,requests,settle:()=>new Promise(r=>setImmediate(r)),reply(i,data){pending[i]({ok:true,json:async()=>data});}};
}
test('历史复盘首次只查询，点击才提交独立参数且不控制运行账户',async()=>{const p=page();p.reply(0,{status:'idle'});await p.settle();assert.equal(p.requests.length,1);p.nodes.get('ar-run').handlers.click();const body=JSON.parse(p.requests[1].options.body);assert.equal(body.config.per_stock_budget,10000);assert.equal(body.config.exit_ma,20);assert.equal(body.config.max_positions,5);assert.ok(p.requests.every(r=>!r.url.includes('control')));});
test('无成交不展示0胜率，来源文字安全渲染',async()=>{const p=page();p.reply(0,{result:{start:'2026-08-04',end:'2026-11-03',symbols:['600000'],limitations:['<img>'],results:[{label:'<script>',diagnostics:{ready_plans:0},summary:{closed_trades:0,return_pct:0,monthly_equivalent_pct:0,max_drawdown_pct:0,fees:0,win_rate_pct:null},open_positions:[],monthly:[]}]}});await p.settle();assert.match(p.nodes.get('ar-results').innerHTML,/无已平仓，无法计算/);assert.match(p.nodes.get('ar-results').innerHTML,/&lt;script/);assert.doesNotMatch(p.nodes.get('ar-results').innerHTML,/<img>/);});
