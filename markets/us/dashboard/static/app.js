const $ = (id) => document.getElementById(id);

const LS_TIMER = "signal_board_timer_on";
const LS_INTERVAL = "signal_board_bar_interval";
/** 美东常规开盘 09:30，定时刷新以此为锚点按 K 线周期对齐 */
const RTH_OPEN_MIN = 9 * 60 + 30;
const BAR_CLOSE_LAG_MS = 2500;

let timerId = null;
let countdownId = null;
let nextAt = 0;
// 默认定时关闭；仅当用户曾打开并写入 localStorage=1 才自动开
let timerOn = localStorage.getItem(LS_TIMER) === "1";

function barInterval() {
  const v = ($("bar-interval") && $("bar-interval").value) || "15m";
  return ["1m", "5m", "15m"].includes(v) ? v : "15m";
}

function barMinutes() {
  return { "1m": 1, "5m": 5, "15m": 15 }[barInterval()] || 15;
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
  if (item.signal === "平空") return "空单失效：已收复 VWAP → 立刻平空，不要继续拿着";
  if (item.signal === "平多") return "多单失效：已跌破 VWAP → 立刻平多";
  if (item.signal === "做空") {
    if (r.includes("风控未过")) return "空头结构有，但 R1/价序未过，不开";
    return "空头 setup 有效：按入场带做空，结构失效/收复 VWAP 则平";
  }
  if (item.signal === "做多") {
    if (r.includes("风控未过")) return "多头结构有，但 R1/价序未过，不开";
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
  const scope = item.struct_scope ? `<p class="scope">${item.struct_scope}</p>` : "";
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
  btn.textContent = timerOn ? "停定时" : "不定时";
  btn.title = timerOn
    ? "关闭自动刷新（改回手动）"
    : "开启：按美东开盘09:30对齐K线收盘自动刷新";
  const meta = lastRefreshMeta ? ` · ${lastRefreshMeta}` : "";
  if (!timerOn) {
    status.textContent = `不定时 · 点「刷新」即可${meta}`;
    return;
  }
  const left = Math.max(0, Math.ceil((nextAt - Date.now()) / 1000));
  const iv = barInterval();
  const nextNy = nextAt ? formatNyHm(nextAt) : "—";
  status.textContent = `定时中 · 对齐开盘 09:30 ET · ${iv} 收盘刷新 · 下次 ${nextNy} ET · ${left}s${meta}`;
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
$("bar-interval").addEventListener("change", () => {
  localStorage.setItem(LS_INTERVAL, barInterval());
  load();
  if (timerOn) startTimer();
});
$("symbols").addEventListener("keydown", (e) => {
  if (e.key === "Enter") load();
});

const savedIv = localStorage.getItem(LS_INTERVAL);
if (savedIv && [...$("bar-interval").options].some((o) => o.value === savedIv)) {
  $("bar-interval").value = savedIv;
}

tickBeijingClock();
setInterval(tickBeijingClock, 1000);
updateTimerUi();
load();
if (timerOn) startTimer();
