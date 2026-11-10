const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function page(){
 const html=fs.readFileSync(path.join(__dirname,'static/quant-research.html'),'utf8'),nodes=new Map(),requests=[],pending=[];
 for(const [,id] of html.matchAll(/id="([^"]+)"/g))nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,handlers:{},addEventListener(k,f){this.handlers[k]=f;}});
 for(const [id,value] of Object.entries({'qr-market':'a_share','qr-engine':'rqalpha','qr-strategy':'setups_v2','qr-symbol':'RB2701','qr-cost':'1','qr-sizing':'fixed'}))nodes.get(id).value=value;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'static/quant-research.js'),'utf8'),{document:{getElementById:id=>nodes.get(id)},Number,JSON,Object,encodeURIComponent,setTimeout:()=>1,clearTimeout(){},fetch:(url,options)=>new Promise(resolve=>{requests.push({url,options});pending.push(resolve);})});
 return {nodes,requests,settle:()=>new Promise(r=>setImmediate(r)),reply(i,data){pending[i]({ok:true,json:async()=>data});}};
}
test('进入页面只读查询；明确点击才提交研究，缺天勤账号不可运行',async()=>{
 const p=page();p.reply(0,{metadata:{engines:{rqalpha:{installed:true,version:'6.5.0'},tqsdk:{installed:true,credentials:false}}}});await p.settle();
 assert.equal(p.requests.length,1);assert.equal(p.requests[0].options.method,undefined);
 p.nodes.get('qr-engine').value='tqsdk';p.nodes.get('qr-engine').handlers.change();assert.equal(p.nodes.get('qr-run').disabled,true);
 p.nodes.get('qr-engine').value='rqalpha';p.nodes.get('qr-market').value='futures';p.nodes.get('qr-market').handlers.change();p.nodes.get('qr-sizing').value='risk';p.nodes.get('qr-run').handlers.click();
 const cfg=JSON.parse(p.requests[1].options.body);assert.equal(cfg.market,'futures');assert.equal(cfg.strategy,'sma');assert.equal(cfg.sizing,'risk');assert.ok(p.requests.every(r=>!r.url.includes('control')));
});
test('零成交和未平仓分开；来源文案安全显示',async()=>{
 const p=page();p.reply(0,{metadata:{engines:{}},result:{market:'a_share',engine:'rqalpha',config:{strategy:'<script>'},data:{start:'2026-08-04',end:'2026-11-03'},summary:{initial_capital:100000,return_pct:0,monthly_equivalent_pct:0,max_drawdown_pct:0,closed_trades:0,win_rate_pct:null,fees:0,rejected_orders:0},open_positions:[],fills:[],assumptions:['<img>']}});await p.settle();
 const html=p.nodes.get('qr-results').innerHTML;assert.match(html,/无已平仓，无法计算/);assert.match(html,/&lt;script/);assert.doesNotMatch(html,/<img>/);
});
