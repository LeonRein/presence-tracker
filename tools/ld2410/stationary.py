"""Is the background stationary? Time-weighted background mean per sensor, gate and hour
(no LD2450 target for +-30 s)."""
import time

import numpy as np

from background import clear_masks
from geom import weights
from load import load

D = load()
for s, x in D.items():
    w = weights(x["t"])
    none_all, _ = clear_masks(x, s)
    hour = np.array([time.localtime(t).tm_hour for t in x["t"]])
    print(s)
    for kind, E in (("moving", x["mg"]), ("still", x["sg"])):
        for h in sorted(set(hour)):
            m = none_all & (hour == h) & (w > 0)
            if w[m].sum() < 120:
                continue
            mu = np.average(E[m], axis=0, weights=w[m])
            print(f"  {kind:6s} {h}h ({w[m].sum() / 60:4.0f} min): " + " ".join(f"{v:5.1f}" for v in mu))
