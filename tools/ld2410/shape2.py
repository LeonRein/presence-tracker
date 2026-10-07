"""Where along the gates does the average profile miss, per sensor (MODEL.md 4.3, 10)? One-person frames
that the filter would count as measurements (amplitude2.frame_info: one LD2450 target, not held, not behind
a wall), standing, < 45 degrees: per sensor and kind the measured excess over the background against the
profile, summed per distance of the gate behind the person (c - r, 0.75 m bins) and per distance of the
person (1 m bins): ratio = sum (e - b) / sum S (energies at 100 left out). A ratio that is the same in all
bins is a scale; one that grows behind the person is the multipath tail; one that changes with r is the
distance law.

usage: uv run --project ../../tracker --with scipy python shape2.py [pattern...]"""
import sys

import numpy as np

import stays as A2
from background import clear_masks
from geom import weights
from load import REC, load

PATTERNS = sys.argv[1:] or [REC]
DB = np.arange(-1.5, 6.01, 0.75)
RB = np.arange(0.5, 8.01, 1.0)

acc = {}
for pat in PATTERNS:
    D = load(pat)
    for s, x in D.items():
        none_all, _ = clear_masks(x, s)
        w = weights(x["t"])
        m = none_all & (w > 0)
        if w[m].sum() < 300:
            continue
        bg = (np.average(x["mg"][m], axis=0, weights=w[m]), np.average(x["sg"][m], axis=0, weights=w[m]))
        ok, held, nvalid, r, ang, walk, pos = A2.frame_info(x, s)
        dt = weights(x["t"], cap=1.0)
        f = ok & ~walk & (ang < 45) & (r < 7.5) & (dt > 0)
        for kind, key, ki in (("moving", "mg", 0), ("still", "sg", 1)):
            g = A2.GATES[kind]
            c = A2.C[g]
            S = A2.mean(A2.P[(kind, "stand")], r[f], c)
            e = x[key][f][:, g]
            ex = e - bg[ki][g][None, :]
            use = e < 99.5
            Dl = c[None, :] - r[f][:, None]
            di = np.digitize(Dl, DB)
            ri = np.digitize(np.broadcast_to(r[f][:, None], Dl.shape), RB)
            ww = np.broadcast_to(dt[f][:, None], Dl.shape) * use
            a = acc.setdefault((s, kind), [np.zeros((len(DB) + 1, len(RB) + 1)) for _ in range(3)])
            np.add.at(a[0], (di, ri), ww * ex)
            np.add.at(a[1], (di, ri), ww * S)
            np.add.at(a[2], (di, ri), ww)

for (s, kind), (num, den, t) in sorted(acc.items()):
    print(f"\n{s} {kind}: measured excess / profile, rows: gate centre - r (m), columns: r (m); (s of frames)")
    print("   c-r \\ r   " + " ".join(f"{lo:5.1f}+" for lo in RB[:-1]) + "    all")
    for i in range(1, len(DB)):
        cells = []
        for j in range(1, len(RB)):
            cells.append(f"{num[i, j] / den[i, j]:6.2f}" if t[i, j] > 20 and den[i, j] > 5 else "     .")
        tot = num[i].sum() / den[i].sum() if t[i].sum() > 20 else np.nan
        print(f"   {DB[i - 1]:+5.2f}     " + " ".join(cells) + f" {tot:6.2f}")
