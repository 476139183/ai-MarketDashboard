const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

function page({saved, failStatus = false, configured = true, modelReady = configured, chosen = true, includeBackend = true, searchResults = []} = {}) {
  const elements = new Map();
  const html = readFileSync(join(__dirname, 'static/research.html'), 'utf8');
  const element = () => ({textContent: '', innerHTML: '', hidden: false, disabled: false, value: '', dataset: {},
    classList: {toggle() {}}, querySelectorAll: () => [],
    children: [], handlers: {}, appendChild(child) {this.children.push(child);},
    replaceChildren(...children) {this.children = children;}, setAttribute() {},
    addEventListener(name, fn) {this.handlers[name] = fn;}});
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) elements.set(id, element());
  elements.get('research-symbol').value = 'NVDA';
  const requests = [], timers = new Map(), storage = new Map(saved ? [['dsa_last_task', JSON.stringify(saved)]] : []);
  if(chosen)storage.set('market_studio_ai_choice','codex_cli');
  let timer = 0, statusCalls = 0;
  const context = vm.createContext({
    document: {getElementById: id => elements.get(id), createElement: element},
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    URLSearchParams, AbortController, location: {search: ''},
    LightweightCharts: {
      createChart: () => ({addSeries: () => ({setData() {}}), remove() {},
        subscribeCrosshairMove() {}, timeScale: () => ({fitContent() {}}), panes: () => []}),
      CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {},
    },
    setTimeout: fn => {timers.set(++timer, fn); return timer;}, clearTimeout: id => timers.delete(id),
    fetch: async (url, options = {}) => {
      requests.push([url, options]);
      let data = {};
      if (url.includes('/api/history-samples')) data={daily:[{symbol:'AAPL',first:'2010-01-04',last:'2025-12-31',rows:4024,source:'FinMind'}],minute:[]};
      if (url.includes('/search?')) data = {items:searchResults};
      if (url.includes('/config')) data = {configured, connected: configured, model_ready: modelReady,...(includeBackend?{backend:'codex_cli'}:{})};
      if (url.includes('/ai-settings')) data = {backend:'codex_cli',local_available:{codex_cli:true}};
      if (url.includes('/analysis')) data = {task_id: 'task_1', status: 'pending'};
      if (url.includes('/api/dsa/history?')) data = {stock_name: '英伟达', data: [
        {date: '2026-10-29', open: 100, high: 104, low: 99, close: 103},
        {date: '2026-10-30', open: 103, high: 106, low: 102, close: 105},
      ]};
      if (url.includes('/status')) {
        statusCalls++;
        data = failStatus ? {error: 'DSA 服务不可用'} : statusCalls === 1
          ? {status: 'processing', progress: 30}
          : {status: 'completed', result: {stock_code: 'NVDA', created_at: '2026-11-02', report: {
            meta: {stock_code: 'NVDA'}, summary: {analysis_summary: '<img src=x onerror=alert(1)>'},
            strategy: {stop_loss: '100'}, details: {news_content: '测试新闻'},
          }}};
      }
      return {ok: !(failStatus && url.includes('/status')), json: async () => data};
    },
  });
  vm.runInContext(readFileSync(join(__dirname, 'static/research.js'), 'utf8'), context);
  return {elements, requests, timers, storage,
    async settle() {await new Promise(resolve => setImmediate(resolve));},
    async click(id) {if (!elements.get(id).disabled) await elements.get(id).handlers.click({preventDefault() {}});},
    async poll() {const [id, fn] = timers.entries().next().value; timers.delete(id); await fn();},
  };
}

test('打开页面不调用模型，点击提交后只轮询状态并安全显示报告', async () => {
  const p = page();
  await p.settle();
  assert.equal(p.requests.filter(([, o]) => o.method === 'POST').length, 0);
  await p.click('btn-analyze');
  assert.equal(p.requests.filter(([, o]) => o.method === 'POST').length, 1);
  assert.match(p.elements.get('analysis-status').textContent, /30/);
  await p.poll();
  assert.match(p.elements.get('report-summary').textContent, /<img/);
  assert.equal(p.elements.get('report-summary').innerHTML, '');
  assert.match(p.elements.get('report-full').textContent, /测试新闻/);
  assert.equal(p.timers.size, 0);
  assert.equal(p.elements.get('btn-analyze').disabled, false);
});

test('刷新恢复已有任务，只查询、不重新提交', async () => {
  const p = page({saved: {task_id: 'task_old', symbol: 'NVDA'}});
  await p.settle();
  assert.ok(p.requests.some(([url]) => url.includes('task_old')));
  assert.equal(p.requests.filter(([, o]) => o.method === 'POST').length, 0);
});

test('查询错误停止轮询并允许手动恢复', async () => {
  const p = page({failStatus: true});
  await p.settle();
  await p.click('btn-analyze');
  assert.match(p.elements.get('analysis-status').textContent, /不可用/);
  assert.equal(p.timers.size, 0);
  assert.equal(p.elements.get('btn-resume').hidden, false);
});

test('未配置服务时禁用提交并显示配置提示', async () => {
  const p = page({configured: false});
  await p.settle();
  assert.equal(p.elements.get('btn-analyze').disabled, true);
  assert.match(p.elements.get('connection-status').textContent, /连接|配置/);
});

test('日 K 展示来源日期与蜡烛，不触发 AI 调用', async () => {
  const p = page();
  await p.settle();
  await p.click('btn-daily');
  assert.equal(p.elements.get('chart-empty').hidden, true);
  assert.match(p.elements.get('daily-status').textContent, /2026-10-30/);
  assert.equal(p.requests.filter(([, o]) => o.method === 'POST').length, 0);
});


test('行情服务可用而未配置模型时，仍加载日 K 并禁用模型调用', async () => {
  const p = page({modelReady: false});
  await p.settle();
  assert.equal(p.elements.get('btn-daily').disabled, false);
  assert.equal(p.elements.get('btn-analyze').disabled, true);
  assert.equal(p.elements.get('chart-empty').hidden, true);
  assert.equal(p.requests.filter(([, o]) => o.method === 'POST').length, 0);
});

test('用户未手动选择时，即使已检测到本机CLI也不自动选中或调用', async () => {
  const p=page({chosen:false});
  await p.settle();
  assert.equal(p.elements.get('ai-backend').value,'');
  assert.equal(p.elements.get('btn-analyze').disabled,true);
  assert.equal(p.requests.filter(([,o])=>o.method==='POST').length,0);
});

test('状态响应缺少后端ID时，保存选择后用已回读的设置核验，不误报不可用',async()=>{
 const p=page({chosen:false,includeBackend:false});
 await p.settle();
 p.elements.get('ai-backend').value='codex_cli';
 await p.elements.get('ai-backend').handlers.change();
 await p.click('btn-ai-save');
 assert.equal(p.elements.get('btn-analyze').disabled,false);
 assert.doesNotMatch(p.elements.get('ai-settings-status').textContent,/不可用/);
 assert.equal(p.requests.filter(([url,o])=>url.includes('/analysis')&&o.method==='POST').length,0);
});


test('名称查询显示市场选项，选择海力士美股后才加载对应行情', async()=>{
  const p=page({searchResults:[
    {symbol:'SKHY',name:'SK海力士 ADR',market:'US',market_label:'美股',supported:true},
    {symbol:'000660.KS',name:'SK海力士',market:'KR',market_label:'韩股',supported:false,note:'暂未接入韩股行情与盘中规则'}
  ]});
  await p.settle();
  const previous=p.requests.filter(([url])=>url.includes('/history')).length;
  p.elements.get('research-symbol').value='海力士';
  await p.click('btn-daily');
  assert.equal(p.elements.get('search-results').hidden,false);
  assert.equal(p.requests.filter(([url])=>url.includes('/history')).length,previous);
  const choices=p.elements.get('search-results').children;
  assert.equal(choices.length,2);
  assert.equal(choices[1].disabled,true);
  await choices[0].handlers.click();
  assert.equal(p.requests.filter(([url])=>url.includes('/history')).length,previous);
  await p.click('btn-daily');
  assert.ok(p.requests.some(([url])=>url.includes('/history?symbol=SKHY')));
  assert.equal(p.elements.get('stock-market').textContent,'美股');
  assert.equal(p.requests.filter(([,o])=>o.method==='POST').length,0);
});

test('没有名称匹配时就近提示，保留当前已加载的K线',async()=>{
  const p=page();await p.settle();
  const price=p.elements.get('stock-price').textContent;
  p.elements.get('research-symbol').value='不存在的股票';
  await p.click('btn-daily');
  assert.match(p.elements.get('search-status').textContent,/未找到/);
  assert.equal(p.elements.get('stock-price').textContent,price);
  assert.equal(p.elements.get('chart-empty').hidden,true);
});

test('切换个股隐藏旧报告，回到原股恢复报告且不重复调用模型',async()=>{
 const p=page();await p.settle();
 await p.click('btn-analyze');await p.poll();
 assert.equal(p.elements.get('report').hidden,false);
 const watch=p.elements.get('research-watchlist').children;
 await watch.find(button=>button.dataset.symbol==='SNDK').handlers.click();
 assert.equal(p.elements.get('report').hidden,true);
 assert.match(p.elements.get('analysis-status').textContent,/SNDK/);
 assert.match(p.elements.get('btn-analyze').textContent,/SNDK/);
 await p.elements.get('research-watchlist').children.find(button=>button.dataset.symbol==='NVDA').handlers.click();
 await p.settle();
 assert.equal(p.elements.get('report').hidden,false);
 assert.equal(p.requests.filter(([url,o])=>url.includes('/analysis')&&o.method==='POST').length,1);
});

test('刷新不会把另一只股票的历史任务套到当前个股',async()=>{
 const p=page({saved:{task_id:'task_sndk',symbol:'SNDK'}});await p.settle();
 assert.equal(p.requests.some(([url])=>url.includes('task_sndk')),false);
 assert.equal(p.elements.get('report').hidden,true);
 await p.elements.get('research-watchlist').children.find(button=>button.dataset.symbol==='SNDK').handlers.click();
 assert.equal(p.requests.some(([url])=>url.includes('task_sndk')),true);
});

test('历史样本面板只在展开时读本地覆盖，不触发数据下载或模型',async()=>{
 const p=page();await p.settle();
 assert.equal(p.requests.some(([url])=>url.includes('/api/history-samples')),false);
 p.elements.get('history-samples').open=true;
 await p.elements.get('history-samples').handlers.toggle();
 assert.match(p.elements.get('history-samples-list').children[0].textContent,/AAPL.*2010-01-04/);
 assert.equal(p.requests.filter(([,o])=>o.method==='POST').length,0);
});
