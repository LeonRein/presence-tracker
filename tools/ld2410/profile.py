"""Energy profile over all gates given ONE person at slant distance r (MODEL.md 4.3 draft):
frames where the LD2450 of the same housing sees exactly one target and no second one within
+-W s; binned by the target's gate (r / 0.75 m), its angle off the axis, walking (|v| > 0.05 m/s)
or standing. Prints the time-weighted median (and q90) energy of every gate, moving and still.

usage: profile.py [sensor|all] [angle_max] [W]"""
import sys

import numpy as np

from geom import GATE, scales, since_last, targets, until_next, weights, wquantile
from load import load

D = load()
SC = scales()


def single(x, s, W=10.0):
    """(mask of frames with exactly one person per the LD2450, its r, angle, walking)."""
    t = x["t"]
    r, ang, valid, walking = targets(x, SC[s])
    n = valid.sum(axis=1)
    multi = n >= 2
    ok = (n == 1) & (since_last(t, multi) > W) & (until_next(t, multi) > W)
    i = np.argmax(valid, axis=1)
    k = np.arange(len(t))
    return ok, r[k, i], ang[k, i], walking[k, i]


def profile(sensors, amax=30.0, W=10.0, amin=0.0, q=0.5):
    rows = {}
    for s in sensors:
        x = D[s]
        w = weights(x["t"], cap=1.0)  # with a target, frames come at 10 Hz
        ok, r, ang, walk = single(x, s, W)
        g = np.floor(r / GATE).astype(int)
        for mode, mm in (("walk", walk), ("stand", ~walk)):
            for tg in range(9):
                m = ok & mm & (g == tg) & (ang >= amin) & (ang < amax) & (w > 0)
                rows.setdefault((mode, tg), []).append((x["mg"][m], x["sg"][m], w[m]))
    out = {}
    for key, parts in rows.items():
        mg = np.concatenate([p[0] for p in parts])
        sg = np.concatenate([p[1] for p in parts])
        w = np.concatenate([p[2] for p in parts])
        if w.sum() < 20:
            continue
        out[key] = (w.sum(), [wquantile(mg[:, j], w, [q])[0] for j in range(9)],
                    [wquantile(sg[:, j], w, [q])[0] for j in range(9)])
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    amax = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    W = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
    amin = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    q = float(sys.argv[5]) if len(sys.argv) > 5 else 0.5
    sensors = list(D) if which == "all" else which.split(",")
    out = profile(sensors, amax, W, amin, q)
    print(f"{which}, one LD2450 target, angle {amin:.0f}-{amax:.0f} deg, quantile {q}: energy per gate (moving | still)")
    for mode in ("walk", "stand"):
        for tg in range(9):
            if (mode, tg) not in out:
                continue
            T, m, st = out[(mode, tg)]
            print(f"  {mode:5s} in gate {tg} ({T / 60:5.1f} min): " + " ".join(f"{v:3.0f}" for v in m) + "  | "
                  + " ".join(f"{v:3.0f}" for v in st[2:]))
