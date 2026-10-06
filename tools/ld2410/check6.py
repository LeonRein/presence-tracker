"""Arbeitszimmer 22:20-22:50: a per-stay latent echo of each person - amplitude level x tail level
(the multipath share), equal prior weights on a small grid, constant over blocks of B s and
integrated per block - against the fixed average profile. Does a second person at r2 still win?
Tempered sums (dt / tau, tau 1 s moving, 8 s still).

usage: check6.py [block_s]"""
import sys

import numpy as np

from check import BG, C, P, TAU, loglik
from check5 import window
from fit import mean

B = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
AMP = np.array([0.5, 0.8, 1.25, 2.0, 3.2])
TAIL = np.array([0.4, 0.7, 1.0, 1.4])
if "fixed" in sys.argv:  # no latent echo: the average profile
    AMP, TAIL = np.array([1.0]), np.array([1.0])
x, m, rl, w = window("arbeitszimmer", "22:20", "22:50")
RS = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0  # scale on the LD2450 slant (calibration)
rl = rl * RS
if "median" in sys.argv:  # the person where the filter would have him: one smoothed position, not each LD2450 frame
    rl = np.full(len(rl), np.median(rl))
blk = np.floor((x["t"][m] - x["t"][m][0]) / B).astype(int)


def profile(p, r, gs, ka, kt):
    q = p.copy()
    tau = 1 / (1 + np.exp(-p[4]))
    t2 = min(tau * kt, 0.99)
    q[4] = np.log(t2 / (1 - t2))
    q[0] = p[0] + np.log(ka)
    return mean(q, r, C[gs], np.zeros(gs.stop - gs.start))


for kind, key, gs in (("moving", "mg", slice(0, 9)), ("still", "sg", slice(2, 9))):
    p = P[(kind, "stand")]
    a = np.exp(p[6])
    b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
    e = x[key][m][:, gs]
    S1 = {(ka, kt): profile(p, rl, gs, ka, kt) for ka in AMP for kt in TAIL}

    def ev(S2):
        L = np.array([np.bincount(blk, weights=w * loglik(e, b + S + S2, a).sum(1)) / TAU[kind] for S in S1.values()])
        mx = L.max(0)
        return np.sum(mx + np.log(np.mean(np.exp(L - mx), axis=0)))

    base = ev(0.0)
    nob = np.sum(w * loglik(e, np.broadcast_to(b, e.shape), a).sum(1)) / TAU[kind]
    out = [f"nobody {nob - base:+8.0f}"]
    for r2 in (2.5, 3.0, 4.0, 4.5, 5.0, 6.0):
        out.append(f"+{r2:.1f} {ev(mean(p, np.full(len(rl), r2), C[gs], 0 * b)) - base:+6.0f}")
    print(f"{kind:6s} slant x{RS} latent amplitude x tail per {B:.0f} s: " + " ".join(out))
