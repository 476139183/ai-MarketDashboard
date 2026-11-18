(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.TechnicalIndicators=api;})(globalThis,()=>{
 'use strict';
 const finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
 function calculate(rows){
  let previousDate='',prices=[],volumes=[],trs=[],gains=[],losses=[],fast=null,slow=null,dea=null,difs=[],gain=null,loss=null,atr=null;
  const average=(values,n)=>values.length<n?null:values.slice(-n).reduce((a,b)=>a+b,0)/n;
  const ema=(value,old,n,values)=>old===null?(values.length>=n?average(values,n):null):old+(value-old)*2/(n+1);
  return rows.map(row=>{
   if(!/^\d{4}-\d{2}-\d{2}$/.test(row.date)||row.date<=previousDate)throw Error('指标日期必须严格递增且无重复');previousDate=row.date;
   const result={date:row.date,ma5:null,ma10:null,ma20:null,ma60:null,dif:null,dea:null,macd:null,rsi14:null,atr14:null,bollMid:null,bollUpper:null,bollLower:null,bias20:null,volumeRatio:null,volumeMA5:null,volumeMA20:null};
   const [o,h,l,c]=['open','high','low','close'].map(k=>Number(row[k]));
   if(!['open','high','low','close'].every(k=>finite(row[k]))||!(l>0&&l<=Math.min(o,c)&&Math.max(o,c)<=h)){
    prices=[];volumes=[];trs=[];gains=[];losses=[];difs=[];fast=slow=dea=gain=loss=atr=null;return result;
   }
   const previous=prices.at(-1),priorVolumes=volumes.slice(-20);prices.push(c);
   if(previous!==undefined){const delta=c-previous;gains.push(Math.max(delta,0));losses.push(Math.max(-delta,0));trs.push(Math.max(h-l,Math.abs(h-previous),Math.abs(l-previous)));}
   for(const n of [5,10,20,60])result['ma'+n]=average(prices,n);
   fast=ema(c,fast,12,prices);slow=ema(c,slow,26,prices);
   if(fast!==null&&slow!==null){result.dif=fast-slow;difs.push(result.dif);dea=ema(result.dif,dea,9,difs);result.dea=dea;result.macd=dea===null?null:2*(result.dif-dea);}
   if(gains.length>=14){gain=gain===null?average(gains,14):(gain*13+gains.at(-1))/14;loss=loss===null?average(losses,14):(loss*13+losses.at(-1))/14;result.rsi14=loss===0?(gain===0?50:100):100-100/(1+gain/loss);}
   if(trs.length>=14){atr=atr===null?average(trs,14):(atr*13+trs.at(-1))/14;result.atr14=atr;}
   if(result.ma20!==null){const deviation=Math.sqrt(prices.slice(-20).reduce((s,v)=>s+(v-result.ma20)**2,0)/20);result.bollMid=result.ma20;result.bollUpper=result.ma20+2*deviation;result.bollLower=result.ma20-2*deviation;result.bias20=(c/result.ma20-1)*100;}
   const volume=finite(row.volume)&&Number(row.volume)>=0?Number(row.volume):null;volumes.push(volume);
   for(const n of [5,20]){const values=volumes.slice(-n);if(values.length===n&&values.every(v=>v!==null))result['volumeMA'+n]=average(values,n);}
   if(volume!==null&&priorVolumes.length===20&&priorVolumes.every(v=>v!==null)&&average(priorVolumes,20)>0)result.volumeRatio=volume/average(priorVolumes,20);
   return result;
  });
 }
 return {calculate};
});
