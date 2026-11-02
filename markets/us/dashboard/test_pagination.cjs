const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

function board(symbols, {delayPages=false, searchResults=[]}={}) {
  const elements = new Map();
  const html = readFileSync(join(__dirname, "static/index.html"), "utf8");
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) {
    elements.set(id, {
      value: id === "symbols" ? "SNDK,SKHY,MU,NVDA" : "",
      disabled: false,
      hidden: false,
      textContent: "",
      innerHTML: "",
      handlers: {}, children: [], replaceChildren(...children) {this.children=children;},
      classList: { toggle() {} },
      attributes: {}, setAttribute(key,value) {this.attributes[key]=value;},
      addEventListener(event, fn) { this.handlers[event] = fn; },
    });
  }
  const requests = [], pending = [];
  const context = vm.createContext({
    document: { getElementById: (id) => elements.get(id), createElement: () => ({
      children: [], handlers: {}, appendChild(child){this.children.push(child);},
      addEventListener(name,fn){this.handlers[name]=fn;},
    }) },
    localStorage: { getItem: () => null, setItem() {} },
    location: { search: "" },
    URLSearchParams, AbortController, setTimeout, clearTimeout,
    setInterval() {}, clearInterval() {},
    fetch: async (url) => {
      const path = new URL(url, "http://localhost");
      if (path.pathname === "/api/signals") {
        requests.push(path.searchParams.get("symbols").split(","));
        if(delayPages && requests.length>1) await new Promise(resolve=>pending.push(resolve));
      }
      return {
        ok: true,
        json: async () => path.pathname === "/api/dsa/search" ? {items:searchResults} : path.pathname === "/api/watchlist"
          ? { symbols, joined: symbols.join(",") }
          : path.pathname === "/api/alerts"
            ? { active: [] }
            : { items: [], generated_bj: "2026-10-30 12:00:00" },
      };
    },
  });
  vm.runInContext(readFileSync(join(__dirname, "static/app.js"), "utf8"), context);
  return {
    elements, requests, pending,
    async settle() { await new Promise((resolve) => setImmediate(resolve)); },
    async click(id) {
      const element = elements.get(id);
      assert.ok(element, `缺少 ${id} 控件`);
      if (!element.disabled) await element.handlers.click();
    },
  };
}

test("完整票池可以逐页浏览，末页不丢标的且不能越界", async () => {
  const symbols = Array.from({ length: 19 }, (_, i) => `ST${String.fromCharCode(65+i)}`);
  const app = board(symbols);
  await app.settle();
  assert.deepEqual(app.requests[0], symbols.slice(0, 8));
  assert.ok(app.elements.get("btn-next"), "票池超过 8 只时必须能翻页");
  assert.equal(app.elements.get("btn-prev").disabled, true);
  await app.click("btn-next");
  assert.deepEqual(app.requests.at(-1), symbols.slice(8, 16));
  await app.click("btn-next");
  assert.deepEqual(app.requests.at(-1), symbols.slice(16));
  assert.equal(app.elements.get("btn-next").disabled, true);
  assert.match(app.elements.get("page-status").textContent, /19/);
  await app.click("btn-next");
  assert.equal(app.requests.length, 3);
  await app.click("btn-prev");
  assert.deepEqual(app.requests.at(-1), symbols.slice(8, 16));
});

test("修改标的会回到第一页，统一大小写并去重", async () => {
  const app = board(Array.from({ length: 12 }, (_, i) => `ST${String.fromCharCode(65+i)}`));
  await app.settle();
  await app.click("btn-next");
  app.elements.get("symbols").value = " nvda, NVDA, mu, ,aapl ";
  await app.click("btn-refresh");
  assert.deepEqual(app.requests.at(-1), ["NVDA", "MU", "AAPL"]);
  assert.equal(app.elements.get("btn-prev").disabled, true);
  assert.equal(app.elements.get("btn-next").disabled, true);
});

test("切换仅信号隐藏分页，返回全部保留当前页", async () => {
  const symbols = Array.from({ length: 12 }, (_, i) => `ST${String.fromCharCode(65+i)}`);
  const app = board(symbols);
  await app.settle();
  await app.click("btn-next");
  await app.click("btn-only");
  await app.settle();
  assert.equal(app.elements.get("pagination").hidden, true);
  assert.equal(app.requests.length, 2);
  await app.click("btn-all");
  await app.settle();
  assert.equal(app.elements.get("pagination").hidden, false);
  assert.deepEqual(app.requests.at(-1), symbols.slice(8));
});


test('翻页等待时显示加载状态，完成后显示新页且恢复按钮',async()=>{
 const app=board(Array.from({length:19},(_,i)=>`ST${String.fromCharCode(65+i)}`),{delayPages:true});
 await app.settle();
 const change=app.click('btn-next');await app.settle();
 assert.equal(app.elements.get('page-loading').hidden,false);
 assert.match(app.elements.get('page-loading-text').textContent,/第 2 页/);
 assert.equal(app.elements.get('cards').attributes['aria-busy'],'true');
 assert.equal(app.elements.get('btn-next').disabled,true);
 app.pending.shift()();await change;
 assert.equal(app.elements.get('page-loading').hidden,true);
 assert.equal(app.elements.get('cards').attributes['aria-busy'],'false');
 assert.equal(app.elements.get('btn-next').disabled,false);
 assert.match(app.elements.get('page-status').textContent,/第 2\/3 页/);
});

test('定时刷新使用开关语义与明确的开启关闭状态',async()=>{
 const app=board(['NVDA']);await app.settle();
 assert.equal(app.elements.get('btn-timer').attributes['aria-checked'],'false');
 assert.equal(app.elements.get('timer-label').textContent,'关闭');
 await app.click('btn-timer');
 assert.equal(app.elements.get('btn-timer').attributes['aria-checked'],'true');
 assert.equal(app.elements.get('timer-label').textContent,'开启');
 await app.click('btn-timer');
});


test('搜索先显示候选，选择后等待确认，不显示识别提示',async()=>{
 const app=board(['NVDA'],{searchResults:[{symbol:'SNDK',name:'闪迪',market_label:'美股',supported:true}]});
 await app.settle();
 const count=app.requests.length;
 app.elements.get('board-search-input').value='闪迪';
 await app.elements.get('board-search-input').handlers.keydown({key:'Enter',preventDefault(){}});
 assert.equal(app.requests.length,count);
 const choices=app.elements.get('board-search-results').children;
 assert.equal(choices.length,1);
 await choices[0].handlers.click();
 assert.equal(app.requests.length,count);
 assert.equal(app.elements.get('btn-confirm').disabled,false);
 await app.click('btn-confirm');
 assert.deepEqual(app.requests.at(-1),['SNDK']);
 assert.equal(app.elements.get('symbols-status').hidden,true);
 assert.doesNotMatch(app.elements.get('symbols-status').textContent,/已识别/);
});

test('筛选使用两个明确选项，点击当前选项不翻转模式',async()=>{
 const app=board(['NVDA']);await app.settle();
 assert.equal(app.elements.get('btn-all').attributes['aria-pressed'],'true');
 await app.click('btn-only');
 assert.equal(app.elements.get('btn-only').attributes['aria-pressed'],'true');
 assert.equal(app.elements.get('btn-all').attributes['aria-pressed'],'false');
 await app.click('btn-only');
 assert.equal(app.elements.get('btn-only').attributes['aria-pressed'],'true');
 await app.click('btn-all');
 assert.equal(app.elements.get('btn-all').attributes['aria-pressed'],'true');
});


test('错误名称不会发送行情请求或替换已有卡片',async()=>{
 const app=board(['NVDA']);await app.settle();
 app.elements.get('cards').innerHTML='原有行情';
 app.elements.get('board-search-input').value='不存在的股票';
 const count=app.requests.length;await app.elements.get('board-search-input').handlers.keydown({key:'Enter',preventDefault(){}});
 assert.equal(app.requests.length,count);
 assert.equal(app.elements.get('cards').innerHTML,'原有行情');
 assert.match(app.elements.get('symbols-status').textContent,/未找到/);
});
