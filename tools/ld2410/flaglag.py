"""Is the flag a threshold on the gate energies some frames earlier/later, or on a run of them?"""
import sys

import numpy as np

from basics import thresholds
from load import load

D = load()
for s, x in D.items():
    mg, sg = x["mg"], x["sg"]
    mt, st, mx = thresholds(s)
    pm = (mg[:, :mx + 1] >= mt[:mx + 1]).any(axis=1)
    ps = (sg[:, 2:mx + 1] >= st[2:mx + 1]).any(axis=1)
    out = []
    for name, pred, flag in (("moving", pm, x["moving"]), ("still", ps, x["still"])):
        row = []
        for k in range(-3, 4):
            if k >= 0:
                a, b = pred[:len(pred) - k], flag[k:]
            else:
                a, b = pred[-k:], flag[:k]
            row.append(f"{k:+d}:{np.mean(a == b):.3f}")
        out.append(f"{name} " + " ".join(row))
    print(s, "flag[i+k] vs threshold[i]:", *out, sep="\n   ")
    # moving flag vs max(move energy - threshold) distribution
    m = (mg[:, :mx + 1] - mt[:mx + 1]).max(axis=1)
    for lo, hi in ((-100, -20), (-20, -10), (-10, 0), (0, 10), (10, 30), (30, 100)):
        sel = (m >= lo) & (m < hi)
        if sel.sum() > 50:
            print(f"   max(move-th) in [{lo},{hi}): n={sel.sum():6d}  P(moving)={x['moving'][sel].mean():.3f}  P(still)={x['still'][sel].mean():.3f}")
if len(sys.argv) > 1:
    s = sys.argv[1]
    x = D[s]
    i0 = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    for i in range(i0, i0 + 40):
        print(f"{x['t'][i] - x['t'][0]:9.2f} M{int(x['moving'][i])} S{int(x['still'][i])} md {x['md'][i]:.2f} me {x['me'][i]:3.0f} sd {x['sd'][i]:.2f} se {x['se'][i]:3.0f}",
              x["mg"][i].astype(int), x["sg"][i].astype(int))
