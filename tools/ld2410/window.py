"""One sensor in a time window (local time, 2026-10-06): LD2450 targets and the gate energies,
quantiles per gate; optional dump of every n-th frame.

usage: window.py sensor HH:MM HH:MM [every]"""
import sys
import time

import numpy as np

from basics import thresholds
from geom import scales, targets, weights, wquantile
from load import load

D = load()
SC = scales()
s, a, b = sys.argv[1:4]
every = int(sys.argv[4]) if len(sys.argv) > 4 else 0
day = "2026-10-06 "
t0, t1 = (time.mktime(time.strptime(day + v, "%Y-%m-%d %H:%M")) for v in (a, b))
x = D[s]
m = (x["t"] >= t0) & (x["t"] < t1)
w = weights(x["t"], cap=5.5)[m]
r, ang, valid, walking = targets(x, SC[s])
r, ang, valid, walking = r[m], ang[m], valid[m], walking[m]
n = valid.sum(1)
print(f"{s} {a}-{b}: {m.sum()} frames, LD2450 targets per frame: " +
      ", ".join(f"{k}: {np.sum(w[n == k]) / np.sum(w):.2f}" for k in range(4)))
print("  target r (q10/50/90):", np.percentile(r[valid], [10, 50, 90]).round(2) if valid.any() else "-",
      " angle:", np.percentile(ang[valid], [10, 50, 90]).round(0) if valid.any() else "-",
      " walking frac:", walking[valid].mean().round(2) if valid.any() else "-")
mt, st, mx = thresholds(s)
for kind, E, th in (("moving", x["mg"][m], mt), ("still", x["sg"][m], st)):
    print(f"  {kind}: q10/q50/q90 per gate and P(>= threshold)")
    for g in range(9):
        q = wquantile(E[:, g], w, [0.1, 0.5, 0.9])
        p = np.sum(w * (E[:, g] >= max(th[g], 1))) / w.sum()
        print(f"    g{g}: {q[0]:3.0f} {q[1]:3.0f} {q[2]:3.0f}  P>=th {p:.2f}")
flag = x["still"][m]
sd = x["sd"][m]
print("  still flag", flag.mean().round(2), "still_distance hist (0.5 m):",
      np.unique(np.round(sd[flag] * 2) / 2, return_counts=True))
if every:
    idx = np.nonzero(m)[0][::every]
    for i in idx:
        print(time.strftime("%H:%M:%S", time.localtime(x["t"][i])), f"M{int(x['moving'][i])} S{int(x['still'][i])} sd {x['sd'][i]:.2f}",
              x["mg"][i].astype(int), x["sg"][i, 2:].astype(int),
              [(round(float(rr), 2), round(float(aa))) for rr, aa, vv in zip(*targets(x, SC[s])[:3][:2], targets(x, SC[s])[2]) for rr, aa, vv in [(rr, aa, vv)]][:0])
