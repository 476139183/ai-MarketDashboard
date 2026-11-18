const {test}=require('node:test'),assert=require('node:assert/strict');
const T=require('./static/technical-indicators.js');
const bars=(n=100,price=i=>100+i)=>Array.from({length:n},(_,i)=>({date:new Date(Date.UTC(2026,0,1+i)).toISOString().slice(0,10),open:price(i),high:price(i)+1,low:price(i)-1,close:price(i),volume:1000}));
test('Wilder RSI的上涨、下跌、平价边界；MACD常量为0',()=>{
 assert.equal(T.calculate(bars()).at(-1).rsi14,100);assert.equal(T.calculate(bars(100,i=>200-i)).at(-1).rsi14,0);
 const last=T.calculate(bars(100,()=>100)).at(-1);assert.equal(last.rsi14,50);assert.equal(last.dif,0);assert.equal(last.dea,0);assert.equal(last.macd,0);assert.equal(last.atr14,2);assert.equal(last.bollUpper,100);
});
test('EMA使用SMA种子，MACD柱使用2*(DIF-DEA)，不足热身保持null',()=>{
 const rows=T.calculate(bars());assert.equal(rows[24].dif,null);assert.equal(rows[32].dea,null);assert.equal(rows[33].dif,7);assert.equal(rows[33].dea,7);assert.equal(rows[33].macd,0);assert.equal(rows[58].ma60,null);assert.equal(rows[59].ma60,129.5);
});
test('历史指标不受未来K线和展示窗口影响',()=>{
 const input=bars();const full=T.calculate(input),prefix=T.calculate(input.slice(0,70));assert.deepEqual(full.slice(0,70),prefix);assert.equal(full[69].ma20,159.5);
});
test('相对成交量使用之前20根，缺失不填零；切断坏数据后重新热身',()=>{
 const rows=bars();rows.at(-1).volume=2000;assert.equal(T.calculate(rows).at(-1).volumeRatio,2);
 rows.at(-2).volume=null;assert.equal(T.calculate(rows).at(-1).volumeRatio,null);
 rows[50].close=NaN;assert.equal(T.calculate(rows)[51].rsi14,null);
});
test('不接受重复和倒序日期，布林带采用20日总体标准差',()=>{
 assert.throws(()=>T.calculate([...bars(),bars()[0]]),/日期/);
 const last=T.calculate(bars(20)).at(-1);assert.ok(Math.abs(last.bollUpper-(109.5+2*Math.sqrt(33.25)))<1e-10);
});
test('Wilder手工参考序列与跳空真实波幅一致',()=>{
 const closes=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28];
 const last=T.calculate(bars(closes.length,i=>closes[i])).at(-1);assert.ok(Math.abs(last.rsi14-70.46413502109705)<1e-9);
 const rows=bars(40,()=>100);rows[39]={...rows[39],open:110,high:111,low:109,close:110};assert.ok(Math.abs(T.calculate(rows).at(-1).atr14-37/14)<1e-10);
});
