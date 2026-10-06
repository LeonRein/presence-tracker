"""Mean excess energy (energy - background mean of that sensor and gate) per gate, for one person
(single LD2450 target, none other within +-10 s) in fine slant bins, standing / walking, and by
angle. Saturated values (100) count as 100 (so near gates are underestimated). Background: the
time-weighted mean with no LD2450 target for +-30 s.

usage: excess.py [walk|stand] [angle_min angle_max] [binwidth]"""
import sys

import numpy as np

from background import clear_masks
from geom import GATE, scales, weights
from load import load
from profile import single

D = load()
SC = scales()


def backgrounds():
    out = {}
    for s, x in D.items():
        w = weights(x["t"])
        none_all, _ = clear_masks(x, s)
        m = none_all & (w > 0)
        out[s] = (np.average(x["mg"][m], axis=0, weights=w[m]), np.average(x["sg"][m], axis=0, weights=w[m]))
    return out


def collect(mode, amin=0, amax=30, W=10.0):
    bg = backgrounds()
    R, A, EM, ES, WT, S = [], [], [], [], [], []
    for s, x in D.items():
        w = weights(x["t"], cap=1.0)
        ok, r, ang, walk = single(x, s, W)
        mm = ok & (walk if mode == "walk" else ~walk) & (ang >= amin) & (ang < amax) & (w > 0)
        R.append(r[mm]); A.append(ang[mm]); WT.append(w[mm]); S += [s] * int(mm.sum())
        EM.append(x["mg"][mm] - bg[s][0]); ES.append(x["sg"][mm] - bg[s][1])
    return (np.concatenate(R), np.concatenate(A), np.concatenate(EM), np.concatenate(ES),
            np.concatenate(WT), np.array(S))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stand"
    amin, amax = (float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else (0, 30)
    bw = float(sys.argv[4]) if len(sys.argv) > 4 else 0.375
    R, A, EM, ES, WT, S = collect(mode, amin, amax)
    print(f"{mode}, angle {amin:.0f}-{amax:.0f}: mean excess per gate (moving g0-8 | still g2-8); gate centres "
          + " ".join(f"{(g + 0.5) * GATE:.2f}" for g in range(9)))
    for lo in np.arange(0.5, 7.0, bw):
        m = (R >= lo) & (R < lo + bw)
        if WT[m].sum() < 15:
            continue
        em = np.average(EM[m], axis=0, weights=WT[m])
        es = np.average(ES[m], axis=0, weights=WT[m])
        sens = ",".join(sorted(set(S[m]), key=lambda z: -np.sum(WT[m][S[m] == z])))[:30]
        print(f"  r {lo:4.2f}-{lo + bw:4.2f} ({WT[m].sum() / 60:5.1f} min, {sens:30s}): " + " ".join(f"{v:3.0f}" for v in em)
              + " | " + " ".join(f"{v:3.0f}" for v in es[2:]))
