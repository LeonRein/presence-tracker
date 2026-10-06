"""What the moving flag adds to the energies: P(moving flag) (time-weighted) in situations defined by
the LD2450, split by whether any moving gate energy is at/above its firmware threshold."""
import numpy as np

from background import clear_masks
from basics import thresholds
from geom import GATE, scales, targets, weights
from load import load

D = load()
SC = scales()
for s, x in D.items():
    w = weights(x["t"])
    none_all, ring = clear_masks(x, s)
    r, ang, valid, walk = targets(x, SC[s])
    near_walk = (valid & walk & (r < 4.5) & (ang < 45)).any(axis=1)
    near_stand = (valid & ~walk & (r < 4.5) & (ang < 45)).any(axis=1) & ~near_walk
    mt, st, mx = thresholds(s)
    above = (x["mg"][:, :mx + 1] >= mt[:mx + 1]).any(axis=1)
    print(s)
    for name, m in (("nobody (+-30 s)", none_all), ("walker < 4.5 m", near_walk), ("standing < 4.5 m", near_stand)):
        for a_name, a in (("energy >= th", above), ("energy < th", ~above)):
            mm = m & a
            T = w[mm].sum()
            if T < 10:
                continue
            print(f"   {name:18s} {a_name:13s} {T / 60:6.1f} min  P(moving flag) {np.sum(w[mm] * x['moving'][mm]) / T:.3f}"
                  f"  P(still flag) {np.sum(w[mm] * x['still'][mm]) / T:.3f}")
