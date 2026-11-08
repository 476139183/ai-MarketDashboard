const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');const path=require('node:path');
function setup(){
 const series=[],charts=[],container={},legend={textContent:''};
 const L={CandlestickSeries:'candle',LineSeries:'line',HistogramSeries:'volume',createChart(){const chart={addSeries(type,options){const s={type,options,data:[],setData(data){this.data=data;},priceScale(){return{applyOptions(){}};}};series.push(s);return s;},removeSeries(){},subscribeCrosshairMove(fn){chart.hover=fn;},timeScale(){return{fitContent(){chart.fits=(chart.fits||0)+1;}};}};charts.push(chart);return chart;}};
 const context=vm.createContext({window:{LightweightCharts:L},Date,Number,Map});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'static/market-chart.js'),'utf8'),context);
 return{chart:new context.window.MarketChart(container,legend),legend,series,charts};
}
test('OHLCV 图使用真实每根成交量、保留未收盘 K 线并更新同时间价格与量',()=>{
 const p=setup();const row={symbol:'SNDK',source:'test',period_minutes:1,volume_unit:'股',sampled:false,chart_candles:[{time:60000,open:10,high:12,low:9,close:11,volume:1234,complete:false}]};
 p.chart.render(row);assert.equal(p.series[0].type,'candle');assert.equal(p.series[1].type,'volume');assert.equal(p.series[1].data[0].value,1234);assert.match(p.legend.textContent,/形成中/);assert.match(p.legend.textContent,/1,234 股/);
 row.chart_candles[0].close=12;row.chart_candles[0].volume=1300;p.chart.render(row);
 assert.equal(p.series[0].data[0].close,12);assert.equal(p.series[1].data[0].value,1300);assert.equal(p.charts[0].fits,1);p.charts[0].hover({});assert.match(p.legend.textContent,/1,300 股/);
});
test('采样只有价格线，成交量缺失不填零；切换标的清除旧量柱',()=>{
 const p=setup();p.chart.render({symbol:'BTC',source:'OKX',candles:[{time:60000,open:10,high:11,low:9,close:10,volume:0}],volume_unit:'基础币'});
 assert.equal(p.series[1].data[0].value,0);
 p.chart.render({symbol:'NVDA',source:'Nasdaq',sampled:true,line:[{time:60000,value:20}],volume_unit:'股'});
 assert.equal(p.series[1].data.length,0);assert.match(p.legend.textContent,/成交量未提供/);
 assert.equal(p.series.at(-1).type,'line');
});
test('共享图表将已收盘OHLCV传入标注层，清空或切换图型时清理旧标注',()=>{
 const calls=[];let removed=0;
 class Overlay{constructor(){calls.push('new');}clear(){calls.push('clear');}destroy(){calls.push('destroy');}}
 class Controls{bind(overlay,rows,options){calls.push({rows,options});}clear(){calls.push('control-clear');}}
 const L={CandlestickSeries:'candle',LineSeries:'line',HistogramSeries:'volume',createChart(){return{addSeries(){return{setData(){},priceScale(){return{applyOptions(){}};}};},removeSeries(){removed++;},subscribeCrosshairMove(){},timeScale(){return{fitContent(){}};}};}};
 const context=vm.createContext({window:{LightweightCharts:L,MarketStructure:{Overlay,Controls}},Date,Number,Map});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'static/market-chart.js'),'utf8'),context);
 const view=new context.window.MarketChart({}, {textContent:''},{}),rows=[{time:60000,open:10,high:11,low:9,close:10,complete:true}];
 view.render({symbol:'BTC',source:'OKX',chart_candles:rows});
 assert.equal(calls.find(c=>c&&c.rows)?.rows,rows);
 view.clear();assert.ok(calls.includes('control-clear'));
 view.render({symbol:'NVDA',source:'Nasdaq',sampled:true,line:[{time:60000,value:10}]});
 assert.ok(calls.includes('destroy'));assert.equal(removed,1);
});
