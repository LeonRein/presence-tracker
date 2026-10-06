"""Gate energies without anybody there (as far as the LD2450 can tell): time-weighted quantiles
per sensor, kind and gate; for "no LD2450 target in this gate's ring +-1 gate for +-W s" and the
stricter "no LD2450 target at all for +-W s"."""
import sys

import numpy as np

from basics import thresholds
from geom import GATE, scales, since_last, targets, until_next, weights, wquantile
from load import load

W = 30.0
D = load()
SC = scales()
QS = [0.1, 0.5, 0.9, 0.99]


def clear_masks(x, s):
    t = x["t"]
    r, ang, valid, walking = targets(x, SC[s])
    gate = np.floor(r / GATE).astype(int)
    anyt = valid.any(axis=1)
    none_all = (since_last(t, anyt) > W) & (until_next(t, anyt) > W)
    ring = []
    for g in range(9):
        near = (valid & (np.abs(gate - g) <= 1)).any(axis=1)
        ring.append((since_last(t, near) > W) & (until_next(t, near) > W))
    return none_all, ring


if __name__ == "__main__":
    W = float(sys.argv[1]) if len(sys.argv) > 1 else W
    for s, x in D.items():
        w = weights(x["t"])
        none_all, ring = clear_masks(x, s)
        mt, st, mx = thresholds(s)
        print(f"\n{s}: time with no LD2450 target for +-{W:.0f} s: {w[none_all].sum() / 60:.1f} min of {w.sum() / 60:.0f}")
        for kind, E, th in (("moving", x["mg"], mt), ("still", x["sg"], st)):
            print(f"  {kind:6s} gate | ring clear: min   q10 q50 q90 q99  P(>=th) | none at all: min  q10 q50 q90 q99 P(>=th)")
            for g in range(9):
                cells = []
                for m in (ring[g], none_all):
                    if w[m].sum() < 60:
                        cells.append("   (too little)                  ")
                        continue
                    q = wquantile(E[m, g], w[m], QS)
                    p = np.sum(w[m] * (E[m, g] >= max(th[g], 1))) / w[m].sum()
                    cells.append(f"{w[m].sum() / 60:6.1f} {q[0]:3.0f} {q[1]:3.0f} {q[2]:3.0f} {q[3]:3.0f}  {p:5.3f}")
                print(f"  {kind:6s} {g:4d} | {cells[0]} | {cells[1]}   th {th[g]}")
