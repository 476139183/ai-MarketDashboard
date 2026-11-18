const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function page(){
 const nodes=new Map(),pending=[],requests=[];
 const element=()=>({textContent:'',innerHTML:'',hidden:false,children:[],handlers:{},classList:{toggle(){},remove(){}},addEventListener(k,f){this.handlers[k]=f;},appendChild(n){this.children.push(n);},replaceChildren(...n){this.children=n;}});
 for(const [,id]of fs.readFileSync(path.join(__dirname,'static/a-radar.html'),'utf8').matchAll(/id="([^"]+)"/g))nodes.set(id,element());
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'static/a-radar.js'),'utf8'),{document:{getElementById:id=>nodes.get(id),querySelectorAll:()=>[],createElement:element},Intl,Date,Number,JSON,Set,Object,encodeURIComponent,localStorage:{getItem(){return null;},setItem(){}},location:{},setTimeout(){},fetch:(url,options)=>{requests.push({url,options});return new Promise(resolve=>pending.push(resolve));}});
 return {nodes,requests,reply(data){pending.shift()({ok:true,json:async()=>data});},settle:()=>new Promise(r=>setImmediate(r))};
}
test('全市场研究明确显示日期与缺失数据，不能冒充可执行候选，名称用文本渲染',async()=>{
 const p=page();p.reply({strategy_candidates:[],watch_pool:[],selection_research:{signal_date:'2026-11-11',total:5224,candidate_count:1,stages:{candidate:1,data:5223},candidates:[{name:'<img src=x>',code:'601628',plan:{signal_close:34.7,sniper:{secondary_buy:34.42,ideal_buy:35.05,stop_loss:34.18,take_profit:35.26}}}]}});
 await p.settle();assert.equal(p.nodes.get('selection-panel').hidden,false);assert.match(p.nodes.get('selection-status').textContent,/2026-11-11.*5224.*数据未就绪 5223/);
 const row=p.nodes.get('selection-list').children[0];assert.equal(row.children[0].textContent,'<img src=x> 601628');assert.equal(row.children[0].innerHTML,'');assert.match(row.children[1].textContent,/等待盘中确认/);
 assert.equal(p.requests.length,1);assert.equal(p.requests[0].options.method,undefined);
});
test('没有全市场快照时隐藏该区域，不把原热榜说成全市场',async()=>{const p=page();p.reply({strategy_candidates:[],watch_pool:[]});await p.settle();assert.equal(p.nodes.get('selection-panel').hidden,true);});
