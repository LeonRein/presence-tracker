"""One sensor's energies and LD2450 targets as a time series (every n-th frame).

usage: series.py sensor HH:MM:SS HH:MM:SS [every]"""
import sys
import time

import numpy as np

from geom import scales, targets
from load import load

D = load()
SC = scales()
s, a, b = sys.argv[1:4]
every = int(sys.argv[4]) if len(sys.argv) > 4 else 5
t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M:%S")) for v in (a, b))
x = D[s]
r, ang, valid, walk = targets(x, SC[s])
idx = np.nonzero((x["t"] >= t0) & (x["t"] < t1))[0][::every]
for i in idx:
    tg = " ".join(f"{rr:.1f}m/{aa:.0f}{'w' if ww else ''}" for rr, aa, vv, ww in zip(r[i], ang[i], valid[i], walk[i]) if vv)
    print(time.strftime("%H:%M:%S", time.localtime(x["t"][i])) + f".{int(x['t'][i] * 10) % 10}",
          "mv", " ".join(f"{v:3.0f}" for v in x["mg"][i]), "| st", " ".join(f"{v:3.0f}" for v in x["sg"][i, 2:]), "|", tg)
