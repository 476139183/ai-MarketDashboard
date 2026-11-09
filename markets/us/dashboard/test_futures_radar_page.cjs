const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function page(){
 const html=fs.readFileSync(path.join(__dirname,'static/futures-radar.html'),'utf8'),nodes=new Map(),requests=[],pending=[];
 const element=()=>({value:'',textContent:'',innerHTML:'',disabled:false,handlers:{},addEventListener(n,f){this.handlers[n]=f;}});
 for(const [,id] of html.matchAll(/id="([^"]+)"/g))nodes.set(id,element());
 for(const [key,value] of Object.entries({period:'day',group:'all',trend:'all',sort:'change'}))nodes.get('fr-'+key).value=value;
 const context=vm.createContext({document:{getElementById:id=>nodes.get(id)},URLSearchParams,Date,Number,Math,AbortController,setTimeout:()=>1,clearTimeout(){},
 fetch:(url,options)=>new Promise(resolve=>{requests.push({url,options});pending.push(resolve);})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'static/futures-radar.js'),'utf8'),context);
 return{nodes,requests,settle:()=>new Promise(r=>setImmediate(r)),reply(i,data){pending[i]({ok:true,json:async()=>data});}};
}
const row=(symbol,group='黑色',trend='strong')=>({symbol,name:'<img src=x>',group,price:3000,quote_at:Date.now(),change_pct:2,analysis:{trend,label:'历史结构偏强',asof:123,count:60,volume_ratio:1.2,oi_change:10}});
test('雷达只读批量接口，安全显示来源内容并链接所选合约',async()=>{const p=page();p.reply(0,{items:[row('RB0')],period:'day',status:'idle',fetched_at:123});await p.settle();const html=p.nodes.get('fr-rows').innerHTML;assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/);assert.match(html,/\/futures\?symbol=RB0&amp;period=day/);assert.ok(p.requests.every(r=>r.url.startsWith('/api/futures/radar?')&&!r.options.method));});
test('分类及结构筛选不把缺失分析当作有效趋势',async()=>{const p=page();p.reply(0,{items:[row('RB0'),{...row('AU0','贵金属'),analysis:null,analysis_error:'offline'}],period:'day',status:'idle'});await p.settle();p.nodes.get('fr-trend').value='strong';p.nodes.get('fr-trend').handlers.change();assert.doesNotMatch(p.nodes.get('fr-rows').innerHTML,/AU0/);p.nodes.get('fr-trend').value='all';p.nodes.get('fr-group').value='贵金属';p.nodes.get('fr-group').handlers.change();assert.match(p.nodes.get('fr-rows').innerHTML,/offline/);assert.doesNotMatch(p.nodes.get('fr-rows').innerHTML,/RB0/);});
test('切周期清除旧摘要，迟到响应不能覆盖新周期',async()=>{const p=page();p.nodes.get('fr-period').value='15';p.nodes.get('fr-period').handlers.change();p.reply(1,{items:[row('AU0')],period:'15',status:'idle'});await p.settle();p.reply(0,{items:[row('RB0')],period:'day',status:'idle'});await p.settle();assert.match(p.nodes.get('fr-rows').innerHTML,/AU0/);assert.doesNotMatch(p.nodes.get('fr-rows').innerHTML,/RB0/);});
test('过期缓存保留历史结构，标出报价及采集状态',async()=>{const p=page();p.reply(0,{items:[{...row('RB0'),quote_at:1}],period:'day',status:'error',stale:true,error:'source failed',fetched_at:123});await p.settle();assert.match(p.nodes.get('fr-status').textContent,/source failed/);assert.match(p.nodes.get('fr-rows').innerHTML,/休市或延迟/);assert.match(p.nodes.get('fr-rows').innerHTML,/历史结构偏强/);});
