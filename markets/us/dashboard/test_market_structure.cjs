const {test}=require('node:test');const assert=require('node:assert/strict');
const S=require('./static/market-structure.js');
const bar=(i,high,low=high-3,close=(high+low)/2)=>({time:i*60000,open:close,high,low,close,volume:10,complete:true});
const fixture=()=>[bar(0,10,7),bar(1,12,8),bar(2,15,10),bar(3,13,9),bar(4,11,8),bar(5,12,9),bar(6,17,10,16),bar(7,14,9),bar(8,12,8),bar(9,9,5,6),bar(10,11,7),bar(11,13,9)];

test('拐点发生在第2根，但必须到第4根收盘才确认',()=>{
 assert.equal(S.analyze(fixture().slice(0,4),{swingLength:2}).pivots.length,0);
 const point=S.analyze(fixture().slice(0,5),{swingLength:2}).pivots.find(p=>p.kind==='high');
 assert.equal(point.time,120000);assert.equal(point.confirmed_at,240000);assert.equal(point.label,'H');
});
test('逐根追加不会修改过去已经确认的拐点或突破事件',()=>{
 const rows=fixture(),full=S.analyze(rows,{swingLength:2});
 for(let n=1;n<=rows.length;n++){
  const result=S.analyze(rows.slice(0,n),{swingLength:2}),cut=rows[n-1].time;
  assert.deepEqual(result.pivots,full.pivots.filter(p=>p.confirmed_at<=cut));
  assert.deepEqual(result.events,full.events.filter(p=>p.time<=cut));
 }
});
test('收盘突破生成一次BOS，首次反方向突破为CHoCH候选',()=>{
 const r=S.analyze(fixture(),{swingLength:2});
 assert.equal(r.events[0].label,'BOS');assert.equal(r.events[0].direction,'bullish');
 assert.equal(r.events[1].label,'CHoCH');assert.equal(r.events[1].direction,'bearish');
 assert.equal(r.events.filter(e=>e.level===15).length,1);
});
test('未收盘K线不会确认拐点、改变趋势或创建缺口',()=>{
 const rows=fixture().slice(0,7),expected=S.analyze(rows,{swingLength:2});
 const live=[...rows,{...bar(7,100,1,90),complete:false}];
 assert.deepEqual(S.analyze(live,{swingLength:2}),expected);
});
test('只有价格采样时不生成市场结构',()=>{
 const r=S.analyze([{time:60000,value:10}],{sampled:true});
 assert.equal(r.status,'sampled');assert.deepEqual(r.pivots,[]);assert.deepEqual(r.zones,[]);
});
test('平价序列不会制造高低点，重复与非法OHLC会拒绝',()=>{
 assert.equal(S.analyze(Array.from({length:30},(_,i)=>bar(i,10,10,10)),{swingLength:2}).pivots.length,0);
 assert.throws(()=>S.analyze([bar(1,10),bar(1,11)]),/时间/);
 assert.throws(()=>S.analyze([bar(1,10,12)]),/OHLC/);
});
test('价格缺口只在第三根收盘创建，后续部分回补缩小、完全回补移除',()=>{
 const rows=[bar(0,10,8),bar(1,13,9),bar(2,16,14)];
 let r=S.analyze(rows,{swingLength:2});const gap=r.zones.find(z=>z.kind==='fvg'&&z.direction==='bullish');
 assert.equal(gap.created_at,120000);assert.equal(gap.origin_at,0);assert.equal(gap.lower,10);assert.equal(gap.upper,14);
 rows.push(bar(3,15,12));r=S.analyze(rows,{swingLength:2});
 assert.equal(r.zones.find(z=>z.created_at===120000).upper,12);
 rows.push(bar(4,14,9));r=S.analyze(rows,{swingLength:2});
 assert.equal(r.zones.some(z=>z.created_at===120000),false);
});
test('同一算法适用于股票日期和合约时间戳，区域边界有序',()=>{
 const rows=fixture(),daily=rows.map((r,i)=>({...r,time:`2026-10-${String(i+1).padStart(2,'0')}`}));
 const a=S.analyze(rows,{swingLength:2}),b=S.analyze(daily,{swingLength:2});
 assert.deepEqual(a.pivots.map(p=>p.label),b.pivots.map(p=>p.label));
 assert.deepEqual(a.events.map(p=>p.label),b.events.map(p=>p.label));
 assert.ok(a.range.upper>a.range.mid&&a.range.mid>a.range.lower);
 assert.ok(a.zones.every(z=>z.upper>z.lower));
});
test('当日日K保守标为未收盘，A股与美股按各自日期处理',()=>{
 const now=Date.parse('2026-11-09T01:00:00Z'),rows=[{date:'2026-11-08',open:10,high:11,low:9,close:10},{date:'2026-11-09',open:10,high:12,low:9,close:11}];
 const cn=S.dailyRows(rows,'300118',now),us=S.dailyRows(rows,'SNDK',now);
 assert.equal(cn[0].complete,true);assert.equal(cn[1].complete,false);
 assert.equal(us[0].complete,false);assert.equal(us[1].complete,false);
});
test('显示层关闭结构后清除标记和区域，切换采样或清空后不遗留旧标注',()=>{
 let primitive,marks=[];const series={attachPrimitive(p){primitive=p;p.attached({chart,series,requestUpdate(){}});},detachPrimitive(){},priceToCoordinate:v=>v};
 const chart={timeScale:()=>({timeToCoordinate:t=>typeof t==='number'?t:1})};
 const L={createSeriesMarkers(){return{setMarkers(v){marks=v;},detach(){}};}};
 const view=new S.Overlay(chart,series,L);view.update(fixture(),{swingLength:2});assert.ok(marks.length>0);assert.ok(primitive.snapshot.pivots.length>0);
 view.update(fixture(),{swingLength:2,structure:false,areas:false,gaps:false});assert.equal(marks.length,0);assert.equal(primitive.visibleZones().length,0);
 view.update([{time:1,value:10}],{sampled:true});assert.equal(marks.length,0);assert.equal(primitive.visibleZones().length,0);
 view.clear();assert.equal(marks.length,0);
});
test('上影线越过前高但收盘未突破时不发BOS',()=>{
 const rows=fixture().slice(0,7);rows[6]={...rows[6],close:14,open:14};
 assert.equal(S.analyze(rows,{swingLength:2}).events.length,0);
});
test('空头缺口对称回补，标签标出更高的高点与更低的低点',()=>{
 const rows=[bar(0,20,18),bar(1,19,15),bar(2,14,12)];
 let r=S.analyze(rows,{swingLength:2});assert.equal(r.zones[0].lower,14);assert.equal(r.zones[0].upper,18);
 rows.push(bar(3,16,13));r=S.analyze(rows,{swingLength:2});assert.equal(r.zones.find(z=>z.created_at===120000).lower,16);
 rows.push(bar(4,19,14));assert.equal(S.analyze(rows,{swingLength:2}).zones.some(z=>z.created_at===120000),false);
 assert.ok(S.analyze(fixture(),{swingLength:2}).pivots.some(p=>p.label==='HH'));
 assert.ok(S.analyze(fixture(),{swingLength:2}).pivots.some(p=>p.label==='LL'));
});
test('日K显示范围裁切时不把窗口外的拐点交给图表标记插件',()=>{
 let markers=[];const chart={timeScale:()=>({timeToCoordinate:()=>1})};
 const series={attachPrimitive(){},detachPrimitive(){}};
 const view=new S.Overlay(chart,series,{createSeriesMarkers:()=>({setMarkers(v){markers=v;}})});
 view.update(fixture(),{swingLength:2,visibleFrom:6*60000});
 assert.ok(markers.length>0);assert.ok(markers.every(m=>m.time>=360));
});
