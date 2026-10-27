const $ = (id) => document.getElementById(id);

const LS_TIMER = "signal_board_timer_on";
const LS_SEC = "signal_board_timer_sec";
const LS_INTERVAL = "signal_board_bar_interval";

let timerId = null;
let countdownId = null;
let nextAt = 0;
let timerOn = localStorage.getItem(LS_TIMER) === "1";

function barInterval() {
  const v = ($("bar-interval") && $("bar-interval").value) || "15m";
  return ["1m", "5m", "15m"].includes(v) ? v : "15m";
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

function timerSeconds() {
  const v = Number($("timer-sec").value);
  return Number.isFinite(v) && v > 0 ? v : 60;
}

function stopTimer() {
  if (timerId) clearInterval(timerId);
  if (countdownId) clearInterval(countdownId);
  timerId = null;
  countdownId = null;
  nextAt = 0;
}

function startTimer() {
  stopTimer();
  const ms = timerSeconds() * 1000;
  nextAt = Date.now() + ms;
  timerId = setInterval(() => {
    load();
    nextAt = Date.now() + ms;
  }, ms);
  countdownId = setInterval(updateTimerUi, 500);
  updateTimerUi();
}

function setTimer(on) {
  timerOn = on;
  localStorage.setItem(LS_TIMER, on ? "1" : "0");
  localStorage.setItem(LS_SEC, String(timerSeconds()));
  if (on) startTimer();
  else {
    stopTimer();
    updateTimerUi();
  }
}

function shortHint(item) {
  const r = item.reason || "";
  if (item.signal === "平空") return "空单失效：已收复 VWAP → 立刻平空，不要继续拿着";
  if (item.signal === "平多") return "多单失效：已跌破 VWAP → 立刻平多";
  if (item.signal === "做空") {
    if (r.includes("风控未过")) return "空头结构有，但 R1/5x 未过，不开";
    return "空头 setup 有效：按入场带做空，结构失效/收复 VWAP 则平";
  }
  if (item.signal === "做多") {
    if (r.includes("风控未过")) return "多头结构有，但 R1/5x 未过，不开";
    return "多头 setup 有效：按入场带做多，结构失效/跌破 VWAP 则平";
  }
  if (item.structure === "空头") {
    if (r.includes("不追")) return "结构偏空，先等反抽到入场价，勿追空";
    return "结构偏空，尚未到入场价 → 观望";
  }
  if (item.structure === "多头") {
    if (r.includes("不追")) return "结构偏多，先等回踩到入场价，勿追多";
    return "结构偏多，尚未到入场价 → 观望";
  }
  if (r.includes("震荡")) return "震荡中，等方向明确";
  if (r.includes("休市") || r.includes("不足")) return "休市或数据不足";
  return "暂不操作";
}

function structureClass(st) {
  if (st === "多头" || st === "偏多" || st === "反转多") return "long";
  if (st === "空头" || st === "偏空" || st === "反转空") return "short";
  return "wait";
}

function chgText(item) {
  if (item.change_pct == null) return "";
  const n = Number(item.change_pct);
  const sign = n > 0 ? "+" : "";
  return `<span class="chg ${n >= 0 ? "up" : "dn"}">${sign}${fmt(n)}%</span>`;
}

function renderCard(item) {
  const st = item.structure || "未定";
  const isExit = item.signal === "平空" || item.signal === "平多";
  const hasPlan =
    ["空头", "多头", "偏空", "偏多", "反转空", "反转多"].includes(st) ||
    isExit ||
    (item.E_band && item.E_band.length === 2);
  const entryLabel =
    item.entry_label ||
    (item.signal === "平空"
      ? "空单失效·先平"
      : item.signal === "平多"
        ? "多单失效·先平"
        : st === "空头"
          ? "做空入场价"
          : st === "多头"
            ? "做多入场价"
            : "观察区");
  const band =
    item.entry_low != null && item.entry_high != null
      ? `${fmt(item.entry_low)} – ${fmt(item.entry_high)}`
      : item.E_band && item.E_band.length === 2
        ? `${fmt(item.E_band[0])} – ${fmt(item.E_band[1])}`
        : "—";
  const mid =
    item.entry_mid != null ? `<li><span class="k">建议挂单</span><span class="v">${fmt(item.entry_mid)}</span></li>` : "";
  const hint = shortHint(item);
  const levels = hasPlan
    ? `
        <li class="entry"><span class="k">${entryLabel}</span><span class="v">${band}</span></li>
        ${mid}
        <li><span class="k">止损</span><span class="v">${item.S != null ? fmt(item.S) : "待定"}${item.S_key ? `<span class="sub">${item.S_key}</span>` : ""}</span></li>
        <li><span class="k">止盈</span><span class="v">${item.T1 != null ? fmt(item.T1) : "待定"}${item.T1_key ? `<span class="sub">${item.T1_key}</span>` : ""}</span></li>`
    : `
        <li><span class="k">观察区</span><span class="v">${band}</span></li>
        <li><span class="k">止损</span><span class="v">待定</span></li>
        <li><span class="k">止盈</span><span class="v">待定</span></li>`;
  const tag = item.price_tag ? `<span class="ptag">${item.price_tag}</span>` : "";
  const iv = item.interval || barInterval();
  const scope = item.struct_scope
    ? `<p class="scope">${item.struct_scope}</p>`
    : "";
  const ready = item.entry_ready
    ? `<span class="ready">已到价</span>`
    : hasPlan
      ? `<span class="wait-entry">未到价</span>`
      : "";
  return `
    <article class="card">
      <div class="row">
        <p class="sym">${item.symbol || "—"} <span class="iv">${iv}</span></p>
        <p class="px">${fmt(item.price)}${chgText(item)}${tag}</p>
      </div>
      <div class="badges">
        <div class="badge ${structureClass(st)}">结构·${st}</div>
        <div class="badge ${signalClass(item.signal)}">信号·${item.signal || "观望"}</div>
        ${ready}
      </div>
      <ul class="levels">${levels}</ul>
      ${scope}
      ${hint ? `<p class="hint">${hint}</p>` : ""}
    </article>
  `;
}

let lastRefreshMeta = "";

function updateTimerUi() {
  const btn = $("btn-timer");
  const status = $("timer-status");
  btn.classList.toggle("on", timerOn);
  btn.setAttribute("aria-pressed", timerOn ? "true" : "false");
  btn.textContent = timerOn ? "停定时" : "定时";
  const meta = lastRefreshMeta ? ` · ${lastRefreshMeta}` : "";
  if (!timerOn) {
    status.textContent = meta ? meta.slice(3) : "";
    return;
  }
  const left = Math.max(0, Math.ceil((nextAt - Date.now()) / 1000));
  status.textContent = `每 ${timerSeconds()}s 自动刷新 · ${left}s${meta}`;
}

async function load() {
  const btn = $("btn-refresh");
  const symbols = $("symbols").value.trim() || "SNDK,MU,NVDA";
  const interval = barInterval();
  btn.disabled = true;
  try {
    const res = await fetch(
      `/api/signals?symbols=${encodeURIComponent(symbols)}&interval=${encodeURIComponent(interval)}&_=${Date.now()}`,
      { cache: "no-store" }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    $("cards").innerHTML = (data.items || []).map(renderCard).join("");
    const phase = (data.session && data.session.phase) || "";
    const iv = data.interval || interval;
    lastRefreshMeta = `已刷新 ${data.generated_bj || "—"} · ${iv}${phase ? " · " + phase : ""}`;
  } catch (err) {
    $("cards").innerHTML = `<p class="hint">加载失败：${err.message}</p>`;
    lastRefreshMeta = `刷新失败 ${err.message}`;
  } finally {
    btn.disabled = false;
    updateTimerUi();
  }
}

$("btn-refresh").addEventListener("click", load);
$("btn-timer").addEventListener("click", () => setTimer(!timerOn));
$("timer-sec").addEventListener("change", () => {
  localStorage.setItem(LS_SEC, String(timerSeconds()));
  if (timerOn) startTimer();
  else updateTimerUi();
});
$("bar-interval").addEventListener("change", () => {
  localStorage.setItem(LS_INTERVAL, barInterval());
  load();
});
$("symbols").addEventListener("keydown", (e) => {
  if (e.key === "Enter") load();
});

const savedSec = localStorage.getItem(LS_SEC);
if (savedSec && [...$("timer-sec").options].some((o) => o.value === savedSec)) {
  $("timer-sec").value = savedSec;
}
const savedIv = localStorage.getItem(LS_INTERVAL);
if (savedIv && [...$("bar-interval").options].some((o) => o.value === savedIv)) {
  $("bar-interval").value = savedIv;
}

tickBeijingClock();
setInterval(tickBeijingClock, 1000);
updateTimerUi();
load();
if (timerOn) startTimer();
