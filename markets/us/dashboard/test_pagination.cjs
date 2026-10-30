const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

function board(symbols) {
  const elements = new Map();
  const html = readFileSync(join(__dirname, "static/index.html"), "utf8");
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) {
    elements.set(id, {
      value: id === "symbols" ? "SNDK,SKHY,MU,NVDA" : "",
      disabled: false,
      hidden: false,
      textContent: "",
      innerHTML: "",
      handlers: {},
      classList: { toggle() {} },
      setAttribute() {},
      addEventListener(event, fn) { this.handlers[event] = fn; },
    });
  }
  const requests = [];
  const context = vm.createContext({
    document: { getElementById: (id) => elements.get(id) },
    localStorage: { getItem: () => null, setItem() {} },
    location: { search: "" },
    URLSearchParams, AbortController, setTimeout, clearTimeout,
    setInterval() {}, clearInterval() {},
    fetch: async (url) => {
      const path = new URL(url, "http://localhost");
      if (path.pathname === "/api/signals") {
        requests.push(path.searchParams.get("symbols").split(","));
      }
      return {
        ok: true,
        json: async () => path.pathname === "/api/watchlist"
          ? { symbols, joined: symbols.join(",") }
          : path.pathname === "/api/alerts"
            ? { active: [] }
            : { items: [], generated_bj: "2026-10-30 12:00:00" },
      };
    },
  });
  vm.runInContext(readFileSync(join(__dirname, "static/app.js"), "utf8"), context);
  return {
    elements, requests,
    async settle() { await new Promise((resolve) => setImmediate(resolve)); },
    async click(id) {
      const element = elements.get(id);
      assert.ok(element, `缺少 ${id} 控件`);
      if (!element.disabled) await element.handlers.click();
    },
  };
}

test("完整票池可以逐页浏览，末页不丢标的且不能越界", async () => {
  const symbols = Array.from({ length: 19 }, (_, i) => `STOCK${i}`);
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
  const app = board(Array.from({ length: 12 }, (_, i) => `STOCK${i}`));
  await app.settle();
  await app.click("btn-next");
  app.elements.get("symbols").value = " nvda, NVDA, mu, ,aapl ";
  await app.click("btn-refresh");
  assert.deepEqual(app.requests.at(-1), ["NVDA", "MU", "AAPL"]);
  assert.equal(app.elements.get("btn-prev").disabled, true);
  assert.equal(app.elements.get("btn-next").disabled, true);
});

test("切换仅信号隐藏分页，返回全部保留当前页", async () => {
  const symbols = Array.from({ length: 12 }, (_, i) => `STOCK${i}`);
  const app = board(symbols);
  await app.settle();
  await app.click("btn-next");
  await app.click("btn-only");
  await app.settle();
  assert.equal(app.elements.get("pagination").hidden, true);
  assert.equal(app.requests.length, 2);
  await app.click("btn-only");
  await app.settle();
  assert.equal(app.elements.get("pagination").hidden, false);
  assert.deepEqual(app.requests.at(-1), symbols.slice(8));
});
