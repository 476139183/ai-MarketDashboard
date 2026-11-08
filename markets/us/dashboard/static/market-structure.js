/* Generic, closed-candle market-structure annotations. Price patterns, not order-flow data. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MarketStructure=api;})(typeof window!=='undefined'?window:globalThis,()=>{
  'use strict';
  const chartTime=t=>typeof t==='number'?t/1000:t;
  const timestamp=t=>typeof t==='number'?t:Date.parse(t+'T00:00:00Z');
  const empty=status=>({status,pivots:[],events:[],zones:[],range:null,trend:'neutral',as_of:null,closed_count:0});
  function analyze(input,{swingLength=5,sampled=false}={}){
    if(sampled)return empty('sampled');
    if(!Number.isInteger(swingLength)||swingLength<2||swingLength>20)throw Error('拐点确认根数需为2至20');
    const rows=input.filter(r=>r.complete!==false),out=empty('insufficient');let previous=-Infinity;
    for(const r of rows){
      const t=timestamp(r.time),values=[r.open,r.high,r.low,r.close];
      if(!Number.isFinite(t)||t<=previous)throw Error('K线时间无效、重复或乱序');
      if(typeof r.time==='string'&&(!/^\d{4}-\d{2}-\d{2}$/.test(r.time)||new Date(t).toISOString().slice(0,10)!==r.time))throw Error('K线日期无效');
      if(values.some(v=>!Number.isFinite(v)||v<=0)||!(r.low<=Math.min(r.open,r.close)&&Math.max(r.open,r.close)<=r.high))throw Error('K线OHLC无效');
      previous=t;
    }
    let high=null,low=null,trend='neutral';const active=[];
    for(let i=0;i<rows.length;i++){
      const r=rows[i],j=i-swingLength;
      if(j>=swingLength){
        const center=rows[j],neighbors=rows.slice(j-swingLength,i+1).filter((_,k)=>k!==swingLength);
        for(const kind of ['high','low']){
          const value=center[kind],isPivot=neighbors.every(b=>kind==='high'?value>b.high:value<b.low);
          if(!isPivot)continue;
          const last=kind==='high'?high:low;
          const label=!last?(kind==='high'?'H':'L'):value===last.price?(kind==='high'?'EQH':'EQL'):kind==='high'?(value>last.price?'HH':'LH'):(value<last.price?'LL':'HL');
          const point={kind,label,time:center.time,price:value,confirmed_at:r.time,confirmation_bars:swingLength};out.pivots.push(point);
          const level={price:value,time:center.time,confirmed_at:r.time,broken:false};if(kind==='high')high=level;else low=level;
        }
      }
      const up=high&&!high.broken&&r.close>high.price,down=low&&!low.broken&&r.close<low.price;
      if(up&&down){trend='neutral';}
      else if(up||down){
        const direction=up?'bullish':'bearish',level=up?high:low,label=trend!=='neutral'&&trend!==direction?'CHoCH':'BOS';
        out.events.push({label,direction,time:r.time,level:level.price,level_time:level.time,level_confirmed_at:level.confirmed_at});
        level.broken=true;trend=direction;
      }
      for(let k=active.length-1;k>=0;k--){
        const z=active[k];
        if(z.direction==='bullish'){
          if(r.low<=z.lower){active.splice(k,1);continue;}
          z.upper=Math.min(z.upper,r.low);
        }else{
          if(r.high>=z.upper){active.splice(k,1);continue;}
          z.lower=Math.max(z.lower,r.high);
        }
      }
      if(i>=2){
        const old=rows[i-2];
        if(r.low>old.high)active.push({kind:'fvg',direction:'bullish',lower:old.high,upper:r.low,created_at:r.time,origin_at:old.time});
        if(r.high<old.low)active.push({kind:'fvg',direction:'bearish',lower:r.high,upper:old.low,created_at:r.time,origin_at:old.time});
      }
    }
    out.closed_count=rows.length;out.as_of=rows.at(-1)?.time??null;out.trend=trend;
    out.zones=active.slice(-12).map(z=>({...z}));
    if(high&&low&&high.price>low.price){
      out.range={upper:high.price,lower:low.price,mid:(high.price+low.price)/2,created_at:timestamp(high.confirmed_at)>timestamp(low.confirmed_at)?high.confirmed_at:low.confirmed_at};
    }
    if(out.pivots.length)out.status='ready';
    return out;
  }
  function dailyRows(rows,symbol,now=Date.now()){
    const zone=/^\d{6}$/.test(symbol)?'Asia/Shanghai':'America/New_York';
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(now));
    const part=k=>parts.find(p=>p.type===k).value,today=part('year')+'-'+part('month')+'-'+part('day');
    return rows.map(r=>({...r,time:r.date,complete:r.complete!==false&&r.date<today}));
  }
  class Overlay {
    constructor(chart,series,L,{dark=true,reservedBottom=0}={}){
      this.chart=chart;this.series=series;this.dark=dark;this.reservedBottom=reservedBottom;this.snapshot=empty('insufficient');this.options={structure:true,areas:true,gaps:true};
      this.marker=L.createSeriesMarkers?.(series,[]);
      this.view={zOrder:()=> 'bottom',renderer:()=>({draw:target=>this.draw(target)})};
      series.attachPrimitive?.(this);
    }
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}
    updateAllViews(){}
    visibleZones(){
      const result=[];
      const r=this.snapshot.range;
      if(this.options.areas&&r){const depth=(r.upper-r.lower)*.2;
        result.push({lower:r.upper-depth,upper:r.upper,start:r.created_at,label:'溢价区',direction:'bearish'});
        result.push({lower:r.lower,upper:r.lower+depth,start:r.created_at,label:'折价区',direction:'bullish'});
      }
      if(this.options.gaps){
        const chosen=['bullish','bearish'].flatMap(direction=>this.snapshot.zones.filter(z=>z.direction===direction).slice(-3));
        result.push(...chosen.map(z=>({...z,start:z.created_at,label:z.direction==='bullish'?'FVG ↑':'FVG ↓'})));
      }
      return result;
    }
    update(rows,options={}){
      this.options={...this.options,...options};this.first=options.visibleFrom??rows[0]?.time;this.last=rows.at(-1)?.time;
      this.snapshot=analyze(rows,this.options);
      const marks=[];
      if(this.options.structure){
        for(const p of this.snapshot.pivots.slice(-36))marks.push({time:chartTime(p.time),position:p.kind==='high'?'aboveBar':'belowBar',shape:p.kind==='high'?'arrowDown':'arrowUp',color:p.kind==='high'?'#d88860':'#2f9b80',text:p.label+' 确+'+p.confirmation_bars,size:.6});
        for(const e of this.snapshot.events.slice(-20))marks.push({time:chartTime(e.time),position:e.direction==='bullish'?'belowBar':'aboveBar',shape:'circle',color:e.label==='CHoCH'?'#ac7ad6':'#479fbd',text:e.label+(e.label==='CHoCH'?' 转向?':''),size:.6});
      }
      // Pivots before the visible price series cannot be passed to the marker plugin.
      this.marker?.setMarkers(marks.filter(m=>this.first!=null&&timestamp(typeof m.time==='number'?m.time*1000:m.time)>=timestamp(this.first)).sort((a,b)=>timestamp(typeof a.time==='number'?a.time*1000:a.time)-timestamp(typeof b.time==='number'?b.time*1000:b.time)));
      this.requestUpdate?.();return this.snapshot;
    }
    clear(){this.snapshot=empty('insufficient');this.marker?.setMarkers([]);this.requestUpdate?.();}
    destroy(){this.marker?.detach?.();this.marker=null;this.series.detachPrimitive?.(this);}
    draw(target){
      if(!this.snapshot.closed_count)return;
      target.useBitmapCoordinateSpace(scope=>{
        const {context:ctx,horizontalPixelRatio:h,verticalPixelRatio:v,bitmapSize}=scope;const labelYs=[],heightLimit=bitmapSize.height*(1-this.reservedBottom);ctx.save();ctx.beginPath();ctx.rect(0,0,bitmapSize.width,heightLimit);ctx.clip();ctx.font=`${10*v}px system-ui`;ctx.textBaseline='middle';
        const xAt=t=>this.chart.timeScale().timeToCoordinate(chartTime(t));
        const xStart=t=>{const x=xAt(t);return x==null&&this.first!=null&&timestamp(t)<timestamp(this.first)?0:x;};
        const end=xAt(this.last),right=end==null?bitmapSize.width:Math.min(bitmapSize.width,end*h+12*h);
        const line=(start,price,color,text,dash=[])=>{const x=xStart(start),y=this.series.priceToCoordinate(price);if(x==null||y==null||y*v<0||y*v>heightLimit)return;ctx.strokeStyle=color;ctx.setLineDash(dash.map(n=>n*h));ctx.beginPath();ctx.moveTo(Math.max(0,x*h),y*v);ctx.lineTo(right,y*v);ctx.stroke();ctx.setLineDash([]);if(text&&!labelYs.some(old=>Math.abs(old-y*v)<13*v)){labelYs.push(y*v);ctx.fillStyle=color;ctx.fillText(text,Math.max(4*h,Math.min(right-85*h,x*h+4*h)),y*v-7*v);}};
        for(const z of this.visibleZones()){
          const x=xStart(z.start),top=this.series.priceToCoordinate(z.upper),bottom=this.series.priceToCoordinate(z.lower);
          if(x==null||top==null||bottom==null||right<=x*h)continue;
          const y=Math.max(0,Math.min(top,bottom)*v),height=Math.min(heightLimit,Math.max(top,bottom)*v)-y;if(height<=0)continue;
          const bullish=z.direction==='bullish';ctx.fillStyle=bullish?(this.dark?'#65c4aa16':'#2a927112'):(this.dark?'#ef8b8716':'#d8797312');ctx.fillRect(Math.max(0,x*h),y,right-Math.max(0,x*h),height);
          line(z.start,z.upper,bullish?'#448f7b88':'#b8716f88',z.label,[3,3]);
        }
        if(this.options.areas&&this.snapshot.range)line(this.snapshot.range.created_at,this.snapshot.range.mid,'#b89a58','均衡 50%',[4,4]);
        if(this.options.structure)for(const e of this.snapshot.events.slice(-20)){
          const start=xStart(e.level_time),endX=xAt(e.time),y=this.series.priceToCoordinate(e.level);if(start==null||endX==null||y==null)continue;
          ctx.strokeStyle=e.label==='CHoCH'?'#ac7ad688':'#479fbd88';ctx.setLineDash([3*h,3*h]);ctx.beginPath();ctx.moveTo(start*h,y*v);ctx.lineTo(endX*h,y*v);ctx.stroke();ctx.setLineDash([]);
        }
        ctx.restore();
      });
    }
  }
  function summary(snapshot,length){
    if(snapshot.status==='sampled')return '分时采样缺少真实OHLC，无法标注市场结构。';
    const note=`高低点须等待右侧${length}根收盘确认；标记“确+${length}”是确认延迟。CHoCH仅为转向候选。`;
    if(snapshot.status!=='ready')return `已收盘${snapshot.closed_count}根；尚无已确认拐点。${note}`;
    const trend={bullish:'结构偏多',bearish:'结构偏空',neutral:'结构待确认'}[snapshot.trend];
    const last=snapshot.pivots.at(-1),when=t=>typeof t==='string'?t:new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
    return `${trend} · 高低点${snapshot.pivots.length}个 · 结构事件${snapshot.events.length}个 · 最新${last.label}：发生K线${when(last.time)}，确认K线${when(last.confirmed_at)}收盘后可见。${note}`;
  }
  class Controls {
    constructor(host){
      this.host=host;this.overlay=null;this.rows=[];this.extra={};this.options={swingLength:5,structure:true,areas:true,gaps:true};
      if(!host||typeof document==='undefined')return;
      host.classList.add('market-structure');
      host.innerHTML='<div class="structure-tools"><strong>市场结构</strong><label><input type="checkbox" data-layer="structure" checked>高低点 / 转向</label><label><input type="checkbox" data-layer="areas" checked>价格区域</label><label><input type="checkbox" data-layer="gaps" checked>价格缺口</label><label>确认<span class="sr-only">右侧</span><select aria-label="拐点右侧确认根数"><option value="3">3根</option><option value="5" selected>5根</option><option value="10">10根</option></select></label></div><p class="structure-note" role="status">等待已收盘K线</p>';
      host.addEventListener('change',event=>{const input=event.target;if(input.dataset.layer)this.options[input.dataset.layer]=input.checked;else if(input.tagName==='SELECT')this.options.swingLength=Number(input.value);this.render();});
    }
    bind(overlay,rows,extra={}){this.overlay=overlay;this.rows=rows;this.extra=extra;return this.render();}
    render(){
      if(!this.overlay)return;
      const node=this.host?.querySelector?.('.structure-note');
      try{const snapshot=this.overlay.update(this.rows,{...this.options,...this.extra});if(node)node.textContent=summary(snapshot,this.options.swingLength)+(this.extra.daily?' 当日日K仅供观察，结构计算保守排除。':'');return snapshot;}
      catch(error){this.overlay.clear();if(node)node.textContent='结构标注暂停：'+error.message;}
    }
    clear(){this.overlay?.clear();this.rows=[];this.extra={};this.overlay=null;const node=this.host?.querySelector?.('.structure-note');if(node)node.textContent='等待所选标的的已收盘K线';}
  }
  return {analyze,dailyRows,Overlay,Controls,summary};
});
