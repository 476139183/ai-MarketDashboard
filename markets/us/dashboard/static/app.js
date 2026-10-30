const $ = (id) => document.getElementById(id);

const LS_TIMER = "signal_board_timer_on";
const LS_ONLY = "signal_board_only_signal";
/** 美东常规开盘 09:30，定时刷新按 15m K 线对齐 */
const RTH_OPEN_MIN = 9 * 60 + 30;
const BAR_CLOSE_LAG_MS = 2500;

let timerId = null;
let countdownId = null;
let nextAt = 0;
// 默认定时关闭；仅当用户曾打开并写入 localStorage=1 才自动开
// URL ?timer=1 / ?timer=0 可强制开/关并写入 localStorage
const _timerParam = new URLSearchParams(location.search).get("timer");
let timerOn =
  _timerParam === "1"
    ? true
    : _timerParam === "0"
      ? false
      : localStorage.getItem(LS_TIMER) === "1";
if (_timerParam === "1" || _timerParam === "0") {
  localStorage.setItem(LS_TIMER, _timerParam);
}
const _onlyParam = new URLSearchParams(location.search).get("only");
let onlySignals =
  _onlyParam === "1"
    ? true
    : _onlyParam === "0"
      ? false
      : localStorage.getItem(LS_ONLY) === "1";
if (_onlyParam === "1" || _onlyParam === "0") {
  localStorage.setItem(LS_ONLY, _onlyParam);
}

function barInterval() {
  return "15m";
}

function barMinutes() {
  return 15;
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

/** 美东墙钟拆解 */
function nyParts(date = new Date()) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const o = {};
  for (const p of fmt.formatToParts(date)) {
    if (p.type !== "literal") o[p.type] = p.value;
  }
  return {
    y: Number(o.year),
    m: Number(o.month),
    d: Number(o.day),
    H: Number(o.hour),
    M: Number(o.minute),
    S: Number(o.second),
  };
}

/** 把美东墙钟转成 UTC ms（迭代校正夏令时） */
function nyWallToUtcMs(y, m, d, H, M, S = 0) {
  let t = Date.UTC(y, m - 1, d, H + 5, M, S);
  for (let i = 0; i < 4; i++) {
    const p = nyParts(new Date(t));
    const wantDay = Date.UTC(y, m - 1, d);
    const gotDay = Date.UTC(p.y, p.m - 1, p.d);
    t += wantDay - gotDay;
    const wantSec = H * 3600 + M * 60 + S;
    const gotSec = p.H * 3600 + p.M * 60 + p.S;
    t += (wantSec - gotSec) * 1000;
  }
  return t;
}

function addNyDays(y, m, d, delta) {
  const dt = new Date(Date.UTC(y, m - 1, d + delta));
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

/**
 * 下一根 K 线收盘对齐时刻（开盘 09:30 ET 起算）+ 短延迟。
 * 例：15m → 09:45 / 10:00 / …；5m → 09:35 / 09:40 / …
 */
function nextOpenAlignedAt(now = Date.now()) {
  const step = barMinutes();
  const p = nyParts(new Date(now));
  const nowMin = p.H * 60 + p.M;
  const nowSecTotal = nowMin * 60 + p.S;
  const openSec = RTH_OPEN_MIN * 60;
  const stepSec = step * 60;

  let day = { y: p.y, m: p.m, d: p.d };
  let targetSec;

  if (nowSecTotal < openSec) {
    // 未到开盘：等到开盘后第一根收盘
    targetSec = openSec + stepSec;
  } else {
    const elapsed = nowSecTotal - openSec;
    const n = Math.floor(elapsed / stepSec) + 1;
    targetSec = openSec + n * stepSec;
    // 跨日（超过午夜按次日开盘第一根）
    if (targetSec >= 24 * 3600) {
      day = addNyDays(day.y, day.m, day.d, 1);
      targetSec = openSec + stepSec;
    }
  }

  let H = Math.floor(targetSec / 3600);
  let M = Math.floor((targetSec % 3600) / 60);
  let S = targetSec % 60;
  if (H >= 24) {
    day = addNyDays(day.y, day.m, day.d, 1);
    H -= 24;
  }

  let at = nyWallToUtcMs(day.y, day.m, day.d, H, M, S) + BAR_CLOSE_LAG_MS;
  if (at <= now + 200) {
    // 刚好踩在边界上，顺延下一根
    const again = nextOpenAlignedAt(now + stepSec * 1000);
    return again;
  }
  return at;
}

function formatNyHm(ms) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
}

function signalClass(sig) {
  if (sig === "做多") return "long";
  if (sig === "做空") return "short";
  if (sig === "平空" || sig === "平多") return "exit";
  return "wait";
}

function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US", {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

/** 北京时间实时钟（前端每秒走，不依赖接口） */
function tickBeijingClock() {
  const now = new Date();
  const text = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);
  $("bj-clock").textContent = `北京 ${text.replace(/\//g, "-")}`;
}

function stopTimer() {
  if (timerId) clearTimeout(timerId);
  if (countdownId) clearInterval(countdownId);
  timerId = null;
  countdownId = null;
  nextAt = 0;
}

function armNextTick() {
  nextAt = nextOpenAlignedAt(Date.now());
  const delay = Math.max(500, nextAt - Date.now());
  timerId = setTimeout(async () => {
    timerId = null;
    await load();
    if (timerOn) armNextTick();
  }, delay);
}

function startTimer() {
  stopTimer();
  armNextTick();
  countdownId = setInterval(updateTimerUi, 500);
  updateTimerUi();
}

function setTimer(on) {
  timerOn = on;
  localStorage.setItem(LS_TIMER, on ? "1" : "0");
  if (on) startTimer();
  else {
    stopTimer();
    updateTimerUi();
  }
}

function shortHint(item) {
  const r = item.reason || "";
  if (r.length > 120) return r.slice(0, 118) + "…";
  return r;
}

function trendClass(tr) {
  if (tr === "上涨" || tr === "多头") return "long";
  if (tr === "下跌" || tr === "空头") return "short";
  return "wait";
}

function chgText(item) {
  if (item.change_pct == null) return "";
  const n = Number(item.change_pct);
  const sign = n > 0 ? "+" : "";
  return `<span class="chg ${n >= 0 ? "up" : "dn"}">${sign}${fmt(n)}%</span>`;
}

function renderCard(item) {
  const trend = item.trend || item.structure || "震荡";
  const advice = item.advice || item.signal || "观望";
  const isExit = advice === "平空" || advice === "平多";
  const hasSide = advice === "做多" || advice === "做空" || isExit;
  const band =
    item.entry_low != null && item.entry_high != null
      ? `${fmt(item.entry_low)} – ${fmt(item.entry_high)}`
      : item.E_band && item.E_band.length === 2
        ? `${fmt(item.E_band[0])} – ${fmt(item.E_band[1])}`
        : "—";
  const stop = item.stop != null ? item.stop : item.S;
  const stopNote = item.stop_note || item.S_key || "";
  const target = item.target != null ? item.target : item.T1;
  const targetNote = item.target_note || item.T1_key || "";
  const ref = item.ref_price != null ? item.ref_price : item.entry_mid;
  const strength =
    item.strength != null
      ? `${item.strength}/5${item.strength_label ? "·" + item.strength_label : ""}`
      : "—";
  const risk =
    item.risk_pct != null
      ? `${fmt(item.risk_pct)}% · ${item.risk_label || ""}`
      : "—";
  const since = item.signal_since_bj || "—";
  const lastSig = item.last_signal || "本次";
  const hint = shortHint(item);
  const levels = hasSide
    ? `
        <li class="entry"><span class="k">入场带</span><span class="v">${band}${item.at_entry ? `<span class="sub">现价在带内</span>` : ""}</span></li>
        <li><span class="k">参考价</span><span class="v">${ref != null ? fmt(ref) : "—"}</span></li>
        <li><span class="k">建议止损</span><span class="v">${stop != null ? fmt(stop) : "待定"}${stopNote ? `<span class="sub">${stopNote}</span>` : ""}</span></li>
        <li><span class="k">建议止盈</span><span class="v">${target != null ? fmt(target) : "待定"}${targetNote ? `<span class="sub">${targetNote}</span>` : ""}</span></li>
        <li><span class="k">盈亏比</span><span class="v">${item.R1 != null ? fmt(item.R1) + " : 1" : "—"}</span></li>
        <li><span class="k">强度</span><span class="v">${strength}</span></li>
        <li><span class="k">风险</span><span class="v">${risk}</span></li>
        <li><span class="k">本次信号</span><span class="v">${since}</span></li>
        <li><span class="k">上次信号</span><span class="v">${lastSig}</span></li>`
    : `
        <li><span class="k">参考价</span><span class="v">${ref != null ? fmt(ref) : fmt(item.price)}</span></li>
        <li><span class="k">建议止损</span><span class="v">待定</span></li>
        <li><span class="k">建议止盈</span><span class="v">待定</span></li>
        <li><span class="k">本次信号</span><span class="v">${since}</span></li>
        <li><span class="k">上次信号</span><span class="v">${lastSig}</span></li>`;
  const tag = item.price_tag ? `<span class="ptag">${item.price_tag}</span>` : "";
  const scope = item.struct_scope ? `<p class="scope">${item.struct_scope} · 15m</p>` : "";
  const actionable = !!item.actionable;
  const actionText = item.action_label || (actionable ? "可按点位执行" : "当前观望");
  const actionCls = actionable ? "ready" : isExit ? "wait-entry" : "wait-entry";
  const dayHi = item.day_high != null ? fmt(item.day_high) : "—";
  const dayLo = item.day_low != null ? fmt(item.day_low) : "—";
  const vwap = item.vwap != null ? fmt(item.vwap) : "—";
  const orb =
    item.orb && item.orb.low != null
      ? `${fmt(item.orb.low)} – ${fmt(item.orb.high)}`
      : "—";
  return `
    <article class="card">
      <div class="row">
        <p class="sym">${item.symbol || "—"} <span class="iv">15m</span></p>
        <p class="px">${fmt(item.price)}${chgText(item)}${tag}</p>
      </div>
      <p class="intraday">日高 ${dayHi} · 日低 ${dayLo} · VWAP ${vwap} · ORB ${orb}</p>
      <div class="badges">
        <span class="${actionCls}">${actionText}</span>
        <div class="badge ${trendClass(trend)}">趋势·${trend}</div>
        <div class="badge ${signalClass(advice)}">建议·${advice}</div>
      </div>
      <ul class="levels">${levels}</ul>
      ${scope}
      ${hint ? `<p class="hint">${hint}</p>` : ""}
    </article>
  `;
}

let lastRefreshMeta = "";

function updateOnlyUi() {
  const btn = $("btn-only");
  if (!btn) return;
  btn.classList.toggle("on", onlySignals);
  btn.setAttribute("aria-pressed", onlySignals ? "true" : "false");
  btn.textContent = onlySignals ? "仅信号" : "全部";
}

function setOnly(on) {
  onlySignals = on;
  localStorage.setItem(LS_ONLY, on ? "1" : "0");
  updateOnlyUi();
  load();
}

function updateTimerUi() {
  const btn = $("btn-timer");
  const status = $("timer-status");
  btn.classList.toggle("on", timerOn);
  btn.setAttribute("aria-pressed", timerOn ? "true" : "false");
  btn.textContent = timerOn ? "停定时" : "不定时";
  btn.title = timerOn
    ? "关闭自动刷新（仅展示）"
    : "开启自动刷新（仅展示，不影响策略）";
  const meta = lastRefreshMeta ? ` · ${lastRefreshMeta}` : "";
  const mode = onlySignals ? "仅信号" : "全部标的";
  if (!timerOn) {
    status.textContent = `不定时 · ${mode} · 15m 趋势卡片 · 后台巡检有信号才通知${meta}`;
    return;
  }
  const left = Math.max(0, Math.ceil((nextAt - Date.now()) / 1000));
  const nextNy = nextAt ? formatNyHm(nextAt) : "—";
  status.textContent = `定时刷新 · ${mode} · 下次 ${nextNy} ET · ${left}s${meta}`;
}

function alertLine(a) {
  const bits = [
    `<strong>${a.symbol || "—"}</strong>`,
    a.advice || "—",
    a.price != null ? `价${fmt(a.price)}` : null,
    a.ref_price != null ? `参考${fmt(a.ref_price)}` : null,
    a.stop != null ? `止损${fmt(a.stop)}` : null,
    a.target != null ? `止盈${fmt(a.target)}` : null,
    a.R1 != null ? `R${fmt(a.R1)}` : null,
  ].filter(Boolean);
  return bits.join(" · ");
}

async function fetchJson(url, timeoutMs = 8000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { cache: "no-store", signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    if (err && err.name === "AbortError") throw new Error("请求超时");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function fetchAlerts() {
  return fetchJson(`/api/alerts?_=${Date.now()}`, 5000);
}

function renderAlertCards(active) {
  return active
    .map((a) =>
      renderCard({
        ...a,
        signal: a.advice,
        action_label: a.action_label || (a.at_entry ? "可按点位执行" : a.advice),
        actionable: !!a.at_entry,
        structure:
          a.trend === "上涨" ? "多头" : a.trend === "下跌" ? "空头" : "震荡",
      })
    )
    .join("");
}

function paintAlertBanner(data) {
  const el = $("alert-banner");
  if (!el) return;
  const active = (data && data.active) || [];
  const updated = (data && data.updated_bj) || "—";
  if (!active.length) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML =
    `<div class="alert-head">巡检活跃 ${active.length} · 更新 ${updated}</div>` +
    `<ul class="alert-list">${active.map((a) => `<li>${alertLine(a)}</li>`).join("")}</ul>`;
}

async function refreshAlertBanner() {
  const el = $("alert-banner");
  if (!el) return;
  try {
    paintAlertBanner(await fetchAlerts());
  } catch (err) {
    el.hidden = false;
    el.innerHTML = `<div class="alert-head">巡检读取失败：${err.message || err}（可点刷新）</div>`;
  }
}

async function loadWatchlistIntoInput() {
  const input = $("symbols");
  if (!input) return;
  try {
    const res = await fetch("/api/watchlist", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    if (data.joined) input.value = data.joined;
  } catch (_) {
    /* keep default */
  }
}

async function loadOnlyFromAlerts() {
  // 仅信号：只读巡检 JSON，不再拉 /api/signals（易超时导致整页 Load failed）
  const data = await fetchAlerts();
  const active = data.active || [];
  paintAlertBanner(data);
  if (!active.length) {
    $("cards").innerHTML =
      `<p class="hint">巡检暂无到价/平仓信号（更新 ${data.updated_bj || "—"}）。后台：./start-watch.sh</p>`;
    lastRefreshMeta = `巡检 ${data.updated_bj || "—"} · 仅信号 · 0`;
    return;
  }
  $("cards").innerHTML = renderAlertCards(active);
  lastRefreshMeta = `巡检 ${data.updated_bj || "—"} · 仅信号 · ${active.length}`;
}

async function load() {
  const btn = $("btn-refresh");
  btn.disabled = true;
  try {
    if (onlySignals) {
      await loadOnlyFromAlerts();
      return;
    }
    // 全部模式也限制标的数量，避免一次扫爆
    const raw = $("symbols").value.trim() || "SNDK,MU,NVDA";
    const list = raw.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 8);
    const symbols = list.join(",");
    const data = await fetchJson(
      `/api/signals?symbols=${encodeURIComponent(symbols)}&interval=15m&multi=0&_=${Date.now()}`,
      45000
    );
    const items = data.items || [];
    $("cards").innerHTML = items.map(renderCard).join("");
    const phase = (data.session && data.session.phase) || "";
    lastRefreshMeta = `已刷新 ${data.generated_bj || "—"} · 15m${phase ? " · " + phase : ""} · ${list.length}只`;
    await refreshAlertBanner();
  } catch (err) {
    lastRefreshMeta = `刷新失败 ${err.message || err}`;
    // 失败时仍尽量显示巡检信号，避免整页空白
    try {
      await loadOnlyFromAlerts();
      lastRefreshMeta += " · 已回退巡检";
    } catch (e2) {
      $("cards").innerHTML = `<p class="hint">加载失败：${err.message || err}</p>`;
    }
  } finally {
    btn.disabled = false;
    updateTimerUi();
  }
}

$("btn-refresh").addEventListener("click", load);
$("btn-timer").addEventListener("click", () => setTimer(!timerOn));
$("btn-only").addEventListener("click", () => setOnly(!onlySignals));
$("symbols").addEventListener("keydown", (e) => {
  if (e.key === "Enter") load();
});

tickBeijingClock();
setInterval(tickBeijingClock, 1000);
updateOnlyUi();
updateTimerUi();
(async () => {
  await loadWatchlistIntoInput();
  await load();
  if (timerOn) startTimer();
  setInterval(refreshAlertBanner, 10000);
  setInterval(() => {
    if (onlySignals) loadOnlyFromAlerts().catch(() => {});
  }, 60000);
})();
