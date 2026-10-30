#!/usr/bin/env python3
"""美股票池后台扫描：对齐北京 15m，仅「到价可执行 / 先平仓」时 macOS 通知。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import BJ, analyze_symbol, session_status  # noqa: E402

WATCHLIST = ROOT / "watchlist.txt"
STATE_PATH = ROOT / "cache" / "watch_state.json"
ALERTS_PATH = ROOT / "cache" / "alerts.json"


def now_bj() -> datetime:
    return datetime.now(BJ)


def load_watchlist(path: Path = WATCHLIST) -> List[str]:
    if not path.is_file():
        return ["SNDK", "MU", "NVDA"]
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip().upper()
        if not s or s.startswith("#"):
            continue
        out.append(s.split()[0].strip(","))
    # 去重保序
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq or ["SNDK", "MU", "NVDA"]


def next_quarter(t: datetime) -> datetime:
    slot = (t.minute // 15 + 1) * 15
    base = t.replace(second=0, microsecond=0)
    if slot >= 60:
        return base.replace(minute=0) + timedelta(hours=1)
    return base.replace(minute=slot)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def notify_macos(title: str, body: str) -> None:
    # 转义 AppleScript 字符串
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(body)}" with title "{esc(title)}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def is_alertable(item: Dict[str, Any]) -> bool:
    advice = item.get("advice") or item.get("signal") or ""
    if advice in ("平多", "平空"):
        return True
    if advice in ("做多", "做空") and item.get("at_entry") and item.get("actionable") and (item.get("trend_context") or {}).get("confirmed"):
        return True
    return False


def compact_alert(item: Dict[str, Any], fired_bj: str) -> Dict[str, Any]:
    return {
        "symbol": item.get("symbol"),
        "advice": item.get("advice") or item.get("signal"),
        "trend": item.get("trend"),
        "trend_context": item.get("trend_context"),
        "actionable": item.get("actionable", False),
        "exit_long": item.get("exit_long", False),
        "exit_short": item.get("exit_short", False),
        "position_rule": item.get("position_rule"),
        "price": item.get("price"),
        "ref_price": item.get("ref_price"),
        "stop": item.get("stop") or item.get("S"),
        "target": item.get("target") or item.get("T1"),
        "R1": item.get("R1"),
        "strength": item.get("strength"),
        "risk_pct": item.get("risk_pct"),
        "at_entry": item.get("at_entry"),
        "action_label": item.get("action_label"),
        "reason": (item.get("reason") or "")[:160],
        "fired_bj": fired_bj,
        "signal_since_bj": item.get("signal_since_bj"),
    }


def fire_key(item: Dict[str, Any], day: str) -> str:
    advice = item.get("advice") or item.get("signal") or "观望"
    return f"{item.get('symbol')}|{advice}|{day}"


def scan_once(
    symbols: List[str],
    *,
    notify: bool = True,
    pause: float = 0.35,
) -> Dict[str, Any]:
    t = now_bj()
    day = t.strftime("%Y-%m-%d")
    fired_bj = t.strftime("%Y-%m-%d %H:%M:%S")
    sess = session_status(t)

    state = _load_json(STATE_PATH, {"fired": {}, "last_scan_bj": None})
    fired: Dict[str, Any] = state.get("fired") or {}
    # 丢掉非今日 key，避免文件膨胀
    fired = {k: v for k, v in fired.items() if k.endswith(day)}

    active: List[Dict[str, Any]] = []
    new_alerts: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    hist = _load_json(ALERTS_PATH, {}).get("history") or []
    started = time.monotonic()
    scanned = 0
    observations = []

    def save_progress(scanning):
        doc = {"updated_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
               "started_bj": fired_bj, "session": sess, "watch_n": len(symbols),
               "scanned_n": scanned, "scanning": scanning,
               "elapsed_seconds": round(time.monotonic() - started, 1),
               "active": active, "new": new_alerts,
               "items": observations,
               "history": (new_alerts + hist)[:40], "errors": errors}
        _save_json(ALERTS_PATH, doc)
        return doc

    def analyze(sym):
        try:
            return analyze_symbol(sym, interval="15m")
        except Exception as e:
            return {"symbol": sym, "advice": "观望", "error": True, "reason": str(e)}

    save_progress(True)
    # 82 只票限流并发取数；单票失败继续扫描，网页可读取逐票进度。
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = []
        for i, sym in enumerate(symbols):
            futures.append(pool.submit(analyze, sym))
            if pause > 0 and i + 1 < len(symbols):
                time.sleep(pause)
        for future in as_completed(futures):
            item = future.result()
            scanned += 1
            observations.append(compact_alert(item, now_bj().strftime("%Y-%m-%d %H:%M:%S")))
            if item.get("error"):
                errors.append({"symbol": item.get("symbol"), "error": item.get("reason", "取数失败")})
            if not is_alertable(item):
                save_progress(True)
                continue
            observed_bj = now_bj().strftime("%Y-%m-%d %H:%M:%S")
            row = compact_alert(item, observed_bj)
            active.append(row)
            key = fire_key(item, day)
            if key not in fired:
                fired[key] = {"bj": observed_bj, "advice": row["advice"]}
                new_alerts.append(row)
                if notify:
                    title = f"{row['symbol']} · {row['advice']}"
                    body = (
                        f"价{row.get('price')} 参考{row.get('ref_price')} "
                        f"止损{row.get('stop')} 止盈{row.get('target')} "
                        f"R{row.get('R1')}"
                    )
                    notify_macos(title, body)
                    print(f"[alert] {title} | {body}", flush=True)
            save_progress(True)

    alerts_doc = save_progress(False)
    state = {"fired": fired, "last_scan_bj": fired_bj, "symbols": symbols}
    _save_json(STATE_PATH, state)

    print(
        f"[scan] {fired_bj} BJ · {len(symbols)}票 · 活跃{len(active)} · 新告警{len(new_alerts)} · 错{len(errors)}",
        flush=True,
    )
    return alerts_doc


def run_loop(*, once: bool = False, notify: bool = True, pause: float = 0.35) -> None:
    symbols = load_watchlist()
    print(f"票池 {len(symbols)}: {','.join(symbols)}", flush=True)
    print(f"告警文件: {ALERTS_PATH}", flush=True)
    if once:
        scan_once(symbols, notify=notify, pause=pause)
        return
    while True:
        symbols = load_watchlist()
        scan_once(symbols, notify=notify, pause=pause)
        t = now_bj()
        sess = session_status(t)
        # 周末少扫：每小时一次；盘中对齐 15m；其它时段 30m
        if sess.get("phase") == "周末休市":
            delay = 3600
            label = "周末·1h"
        elif sess.get("is_rth") or sess.get("phase") in ("盘前", "盘后"):
            nxt = next_quarter(t) + timedelta(seconds=8)  # K 线落定后再扫
            delay = max(5, int((nxt - t).total_seconds()))
            label = f"对齐{nxt.strftime('%H:%M')}BJ"
        else:
            delay = 1800
            label = "非活跃·30m"
        print(f"[sleep] {delay}s ({label}) now={t.strftime('%H:%M:%S')} {sess.get('phase')}", flush=True)
        time.sleep(delay)


def main() -> None:
    ap = argparse.ArgumentParser(description="美股票池信号巡检")
    ap.add_argument("--once", action="store_true", help="只扫一轮")
    ap.add_argument("--no-notify", action="store_true", help="不弹系统通知")
    ap.add_argument("--pause", type=float, default=0.35, help="标的间隔秒")
    args = ap.parse_args()
    run_loop(once=args.once, notify=not args.no_notify, pause=args.pause)


if __name__ == "__main__":
    main()
