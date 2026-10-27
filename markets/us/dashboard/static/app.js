const $ = (id) => document.getElementById(id);

const LS_TIMER = "signal_board_timer_on";
/** 美东常规开盘 09:30，定时刷新按 15m K 线对齐 */
const RTH_OPEN_MIN = 9 * 60 + 30;
const BAR_CLOSE_LAG_MS = 2500;

let timerId = null;
let countdownId = null;
let nextAt = 0;
// 默认定时关闭；仅当用户曾打开并写入 localStorage=1 才自动开
let timerOn = localStorage.getItem(LS_TIMER) === "1";

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
  if (item.signal === "平空" || item.signal === "平多") {
    return "先平仓：结构失效，暂不开新仓";
  }
  if (item.actionable || item.signal === "做空" || item.signal === "做多") {
    const side = item.signal === "做空" ? "做空" : "做多";
    return `当前可操作：按入场带${side}（最新价 + 15m 结构）`;
  }
  if (item.structure === "空头") {
    if (r.includes("不追")) return "当前观望：结构偏空但已离开反抽带，勿追空";
    if (r.includes("逆势")) return "当前观望：结构空但短线偏多，暂不开空";
    return "当前观望：结构偏空，等现价回到做空入场带";
  }
  if (item.structure === "多头") {
    if (r.includes("不追")) return "当前观望：结构偏多但已离开回踩带，勿追多";
    if (r.includes("逆势")) return "当前观望：结构多但短线偏空，暂不开多";
    return "当前观望：结构偏多，等现价回到做多入场带";
  }
  if (r.includes("震荡")) return "当前观望：震荡，等 15m 方向明确";
  if (r.includes("休市") || r.includes("不足")) return "当前观望：休市或数据不足";
  return "当前观望：暂不操作";
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
  const scope = item.struct_scope ? `<p class="scope">${item.struct_scope} · 15m</p>` : "";
  const actionable = !!item.actionable || item.signal === "做多" || item.signal === "做空";
  const actionText = item.action_label || (actionable ? "当前可操作" : "当前观望");
  const actionCls = actionable ? "ready" : item.signal === "平空" || item.signal === "平多" ? "wait-entry" : "wait-entry";
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
        <div class="badge ${structureClass(st)}">结构·${st}</div>
        <div class="badge ${signalClass(item.signal)}">信号·${item.signal || "观望"}</div>
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
    ? "关闭自动刷新（仅展示）"
    : "开启自动刷新（仅展示，不影响策略）";
  const meta = lastRefreshMeta ? ` · ${lastRefreshMeta}` : "";
  if (!timerOn) {
    status.textContent = `不定时 · 手动刷新 · 仅15m策略 · 现价/日高低温实时${meta}`;
    return;
  }
  const left = Math.max(0, Math.ceil((nextAt - Date.now()) / 1000));
  const nextNy = nextAt ? formatNyHm(nextAt) : "—";
  status.textContent = `定时刷新（仅展示）· 下次 ${nextNy} ET · ${left}s${meta}`;
}

async function load() {
  const btn = $("btn-refresh");
  const symbols = $("symbols").value.trim() || "SNDK,MU,NVDA";
  btn.disabled = true;
  try {
    const res = await fetch(
      `/api/signals?symbols=${encodeURIComponent(symbols)}&interval=15m&multi=0&_=${Date.now()}`,
      { cache: "no-store" }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    $("cards").innerHTML = (data.items || []).map(renderCard).join("");
    const phase = (data.session && data.session.phase) || "";
    lastRefreshMeta = `已刷新 ${data.generated_bj || "—"} · 15m${phase ? " · " + phase : ""}`;
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
$("symbols").addEventListener("keydown", (e) => {
  if (e.key === "Enter") load();
});

tickBeijingClock();
setInterval(tickBeijingClock, 1000);
updateTimerUi();
load();
if (timerOn) startTimer();
