(() => {
  'use strict';
  const num=(value,digits=4)=>Number.isFinite(value)?value.toLocaleString('en-US',{maximumFractionDigits:digits}):'—';
  const time=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
  class MarketChart {
    constructor(container,legend,structureHost=null){this.container=container;this.legend=legend;this.chart=null;this.series=null;this.volume=null;this.kind='';this.identity='';this.dataKey='';this.rows=new Map();this.row=null;this.structure=null;this.structureControls=window.MarketStructure?new window.MarketStructure.Controls(structureHost):null;}
    describe(bar){
      const row=this.row;if(!row||!bar)return '等待图表数据';
      if(row.sampled)return `${time(bar.time)} · 分时价格 ${num(bar.value)} · 成交量未提供`;
      const state=bar.complete===false?'形成中 / 未收盘':'已收盘';
      return `${time(bar.time)} · ${state} · 开 ${num(bar.open)} 高 ${num(bar.high)} 低 ${num(bar.low)} 收 ${num(bar.close)} · 量 ${Number.isFinite(bar.volume)?num(bar.volume,8)+' '+(row.volume_unit||'股'):'未提供'}`;
    }
    clear(){this.structureControls?.clear();this.structure?.clear();if(this.series)this.series.setData([]);if(this.volume)this.volume.setData([]);this.rows.clear();this.row=null;this.identity='';this.dataKey='';this.legend.textContent='等待所选标的图表';}
    render(row,scope=''){
      const data=(row.sampled?row.line:(row.chart_candles||row.candles))||[];
      const identity=`${row.source}:${row.symbol}:${row.period_minutes}:${scope}`;
      const dataKey=JSON.stringify([identity,row.sampled,data]);if(dataKey===this.dataKey)return;
      this.row=row;
      this.rows=new Map(data.map(bar=>[bar.time/1000,bar]));
      this.legend.textContent=data.length?this.describe(data.at(-1)):'暂无图表数据 · 成交量未提供';
      const L=window.LightweightCharts;if(!L)return;
      if(!this.chart){
        this.chart=L.createChart(this.container,{autoSize:true,layout:{background:{color:'#182126'},textColor:'#8fa5b2'},grid:{vertLines:{color:'#26333b'},horzLines:{color:'#26333b'}},timeScale:{timeVisible:true,secondsVisible:false,tickMarkFormatter:t=>new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})},localization:{timeFormatter:t=>time(t*1000)}});
        this.chart.subscribeCrosshairMove(event=>{const bar=this.rows.get(event.time)||[...this.rows.values()].at(-1);this.legend.textContent=this.describe(bar);});
      }
      const kind=row.sampled?'line':'candle';
      if(kind!==this.kind){
        this.structure?.destroy();this.structure=null;
        if(this.series)this.chart.removeSeries(this.series);
        this.series=this.chart.addSeries(row.sampled?L.LineSeries:L.CandlestickSeries,row.sampled?{color:'#65c4aa'}:{upColor:'#65c4aa',downColor:'#ef8b87',wickUpColor:'#65c4aa',wickDownColor:'#ef8b87',borderVisible:false});
        this.series.priceScale().applyOptions({scaleMargins:{top:.06,bottom:.28}});this.kind=kind;
        if(window.MarketStructure)this.structure=new window.MarketStructure.Overlay(this.chart,this.series,L,{reservedBottom:.2});
      }
      if(!this.volume){
        this.volume=this.chart.addSeries(L.HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
        this.volume.priceScale().applyOptions({scaleMargins:{top:.80,bottom:0}});
      }
      const lastPrice=row.sampled?data.at(-1)?.value:data.at(-1)?.close;
      const precision=lastPrice!=null&&Math.abs(lastPrice)<1?6:2;
      this.series.applyOptions?.({priceFormat:{type:'price',precision,minMove:10**(-precision)}});
      this.series.setData(data.map(bar=>row.sampled?{time:bar.time/1000,value:bar.value}:{time:bar.time/1000,open:bar.open,high:bar.high,low:bar.low,close:bar.close}));
      this.volume.setData(row.sampled?[]:data.filter(bar=>Number.isFinite(bar.volume)&&bar.volume>=0).map(bar=>({time:bar.time/1000,value:bar.volume,color:bar.close>=bar.open?'#65c4aa88':'#ef8b8788'})));
      if(this.structure)this.structureControls.bind(this.structure,data,{sampled:!!row.sampled});
      if(identity!==this.identity)this.chart.timeScale().fitContent();
      this.identity=identity;this.dataKey=dataKey;
    }
  }
  window.MarketChart=MarketChart;
})();
