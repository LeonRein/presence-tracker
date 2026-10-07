"""Echo events on an empty night: stretches in which a sensor's still energies (gates 2-8, 1-s means)
are well above their level (> FACTOR x the night's median in some gate, still for >= 3 s, merged
across gaps < 10 s) with no LD2450 target in any sensor within +-60 s. Their rate per sensor and
their durations (for ld_echo_rate / ld_echo_life).

usage: echo_events.py "YYYY-MM-DD HH:MM" "YYYY-MM-DD HH:MM" [FACTOR]"""
import sys
import time

import numpy as np

from load import load

REC = "/home/leon/checkout/presence-tracker/recordings/*.jsonl"
t0, t1 = (time.mktime(time.strptime(v, "%Y-%m-%d %H:%M")) for v in sys.argv[1:3])
F = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
D = load(REC)
busy = set()
for s, x in D.items():
    m = (x["t"] >= t0 - 60) & (x["t"] < t1 + 60) & (x["tg"][..., 3] > 0).any(axis=1)
    for t in np.floor(x["t"][m]).astype(int):
        busy.update(range(t - 60, t + 61))
quiet = (t1 - t0 - len([q for q in busy if t0 <= q < t1])) / 3600
print(f"{(t1 - t0) / 3600:.2f} h, of them without any LD2450 target +-60 s: {quiet:.2f} h; factor {F}")
alld = []
for s, x in D.items():
    m = (x["t"] >= t0) & (x["t"] < t1)
    t = x["t"][m]
    E = x["sg"][m][:, 2:].astype(float)
    sec = np.floor(t).astype(int)
    u, inv, cnt = np.unique(sec, return_inverse=True, return_counts=True)
    means = np.stack([np.bincount(inv, weights=E[:, j]) / cnt for j in range(7)], axis=1)
    level = np.maximum(np.median(means, axis=0), 1.0)
    hot = [q for q, row in zip(u, means) if (row > F * level).any() and q not in busy]
    eps = []
    for q in hot:
        if eps and q - eps[-1][1] < 10:
            eps[-1][1] = q
        else:
            eps.append([q, q])
    eps = [e for e in eps if e[1] - e[0] >= 3]
    d = [e[1] - e[0] + 1 for e in eps]
    alld += d
    print(f"  {s:14s} events {len(eps):3d} ({len(eps) / max(quiet, 1e-9):.1f} per quiet hour), durations s: {sorted(d)}")
if alld:
    print(f"all: {len(alld)} events, median {np.median(alld):.0f} s, mean {np.mean(alld):.0f} s")
