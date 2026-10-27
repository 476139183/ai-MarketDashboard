#!/usr/bin/env python3
"""Parse cached Yahoo SNDK history and print calibration stats."""
import csv
import math
import re
import statistics
from datetime import datetime
from pathlib import Path

SRC = Path(
    "/Users/yutianduan/.cursor/projects/Users-yutianduan-Documents-GitHub/"
    "agent-tools/87fda368-dcd0-4a23-bf64-9ea758e4ee14.txt"
)
CSV_OUT = Path("/Users/yutianduan/Desktop/分析系统/docs/sndk_daily_sample.csv")

text = SRC.read_text()
rows = []
pat = re.compile(
    r"\|\s*([A-Za-z]{3} \d{1,2}, \d{4})\s*\|\s*([\d,\.]+)\s*\|\s*([\d,\.]+)\s*\|\s*"
    r"([\d,\.]+)\s*\|\s*([\d,\.]+)\s*\|\s*([\d,\.]+)\s*\|\s*([\d,]+)"
)
for m in pat.finditer(text):
    dt = datetime.strptime(m.group(1), "%b %d, %Y").date()
    o, h, l, c = map(lambda x: float(x.replace(",", "")), m.group(2, 3, 4, 5))
    v = int(m.group(7).replace(",", ""))
    rows.append((dt, o, h, l, c, v))
rows.sort()
print("parsed", len(rows), "from", rows[0][0], "to", rows[-1][0])
closes = [r[4] for r in rows]
print("min", min(closes), "max", max(closes), "last", closes[-1])
rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
print("ann_vol", round(statistics.stdev(rets) * math.sqrt(252) * 100, 1))
atrs = []
for i in range(1, len(rows)):
    h, l, pc = rows[i][2], rows[i][3], rows[i - 1][4]
    atrs.append(max(h - l, abs(h - pc), abs(l - pc)) / pc * 100)
print(
    "ATR% mean/60/20",
    round(statistics.mean(atrs), 2),
    round(statistics.mean(atrs[-60:]), 2),
    round(statistics.mean(atrs[-20:]), 2),
)
print(
    "ATR p50/p90 last60",
    round(statistics.median(atrs[-60:]), 2),
    round(sorted(atrs[-60:])[54], 2),
)
peak = closes[0]
mdd = 0.0
pi = ei = ci = 0
for i, c in enumerate(closes):
    if c > peak:
        peak = c
        ci = i
    dd = (peak - c) / peak
    if dd > mdd:
        mdd = dd
        pi = ci
        ei = i
print(
    "max_dd",
    round(mdd * 100, 1),
    rows[pi][0],
    round(closes[pi], 1),
    "->",
    rows[ei][0],
    round(closes[ei], 1),
)
for thr in (4, 5, 8, 10, 15):
    n = sum(1 for i in range(1, len(closes)) if abs(closes[i] / closes[i - 1] - 1) * 100 >= thr)
    print(f"|1d|>={thr}%", n, f"{n / (len(closes) - 1) * 100:.1f}%")
print(
    "ATR>4",
    f"{sum(1 for x in atrs if x > 4) / len(atrs) * 100:.1f}%",
    "ATR>6",
    f"{sum(1 for x in atrs if x > 6) / len(atrs) * 100:.1f}%",
    "ATR>10",
    f"{sum(1 for x in atrs if x > 10) / len(atrs) * 100:.1f}%",
)
gap = []
oc = []
rng = []
for i in range(1, len(rows)):
    gap.append(abs(rows[i][1] - rows[i - 1][4]) / rows[i - 1][4] * 100)
    oc.append(abs(rows[i][4] - rows[i][1]) / rows[i][1] * 100)
    rng.append((rows[i][2] - rows[i][3]) / rows[i][4] * 100)
print(
    "mean |gap|",
    round(statistics.mean(gap), 2),
    "|OC|",
    round(statistics.mean(oc), 2),
    "H-L%",
    round(statistics.mean(rng), 2),
    "H-L p90",
    round(sorted(rng)[int(0.9 * len(rng))], 2),
)
sat4 = sum(1 for i in range(1, len(closes)) if abs(closes[i] / closes[i - 1] - 1) * 100 >= 4)
sat_ad = 0
for i in range(15, len(closes)):
    a = statistics.mean(atrs[i - 14 : i])
    scale = max(4.0, a)
    if abs(closes[i] / closes[i - 1] - 1) * 100 >= scale:
        sat_ad += 1
print(
    "mom sat fixed4%",
    round(sat4 / (len(closes) - 1) * 100, 1),
    "adaptive%",
    round(sat_ad / max(1, len(closes) - 15) * 100, 1),
)


def ema(xs, n):
    k = 2 / (n + 1)
    e = xs[0]
    out = [e]
    for x in xs[1:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


e20 = ema(closes, 20)
e50 = ema(closes, 50)
for name, cond in [
    ("bull", lambda i: closes[i] > e20[i] > e50[i]),
    ("bear", lambda i: closes[i] < e20[i] < e50[i]),
    (
        "mid",
        lambda i: not (
            closes[i] > e20[i] > e50[i] or closes[i] < e20[i] < e50[i]
        ),
    ),
]:
    xs = [
        (closes[i + 1] / closes[i] - 1) * 100
        for i in range(50, len(closes) - 1)
        if cond(i)
    ]
    if not xs:
        print(name, "empty")
        continue
    if name == "bull":
        hit = sum(1 for x in xs if x > 0) / len(xs) * 100
    elif name == "bear":
        hit = sum(1 for x in xs if x < 0) / len(xs) * 100
    else:
        hit = None
    print(
        name,
        "n",
        len(xs),
        "mean1d",
        round(statistics.mean(xs), 2),
        "hit%",
        None if hit is None else round(hit, 1),
    )

CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
with CSV_OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["date", "open", "high", "low", "close", "volume"])
    for r in rows:
        w.writerow(r)
print("csv", CSV_OUT)
