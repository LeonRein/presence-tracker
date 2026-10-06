"""Arbeitszimmer 22:20-22:50 again: does a second standing person at r2 explain the energies better
than Leon alone, (i) with Leon's amplitude fixed at the fitted profile, (ii) with a latent amplitude
factor for Leon per 60 s block (levels of a Gamma(2, 2) prior, integrated: a Swerling-like slow
fluctuation of the person's echo, like kappa for the LD2450)? Tempered sums (dt / tau)."""
import time

import numpy as np

from check import BG, C, D, P, SC, TAU, loglik
from fit import mean
from geom import targets, weights

import sys

if len(sys.argv) > 1 and sys.argv[1] == "own":
    # parameters fitted on the arbeitszimmer alone (fit.py with LD_SENSORS=arbeitszimmer, 6.10.)
    lg = lambda v: np.log(v / (1 - v))  # noqa: E731
    P[("still", "stand")] = np.array([np.log(591.6), 2.26, 0.34, np.log(0.71), lg(0.42), np.log(1.59), np.log(2.39)])
    P[("moving", "stand")] = np.array([np.log(53.2), 2.47, 0.49, np.log(0.52), lg(0.22), np.log(1.33), np.log(3.01)])
LEVELS = np.array([0.35, 0.65, 0.95, 1.35, 2.1])  # ~ quintile midpoints of Gamma(2, 1/2)
x = D["arbeitszimmer"]
t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M")) for v in ("22:20", "22:50"))
m = (x["t"] >= t0) & (x["t"] < t1)
r, ang, valid, walk = targets(x, SC["arbeitszimmer"])
rl = np.max(np.where(valid[m], r[m], 0), axis=1)
rl = np.where(rl > 0, rl, 1.5)
w = weights(x["t"], cap=1.0)[m]
blk = np.floor((x["t"][m] - t0) / 60).astype(int)
for kind, key, gs in (("moving", "mg", slice(0, 9)), ("still", "sg", slice(2, 9))):
    p = P[(kind, "stand")]
    alpha = np.exp(p[6])
    b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
    e = x[key][m][:, gs]
    S1 = mean(p, rl, C[gs], 0 * b)
    out_fixed, out_latent = [], []
    for r2 in (None, 2.5, 3.0, 4.0, 4.5, 5.0, 6.0):
        S2 = 0 * S1 if r2 is None else mean(p, np.full(len(rl), r2), C[gs], 0 * b)
        fixed = np.sum(w * loglik(e, b + S1 + S2, alpha).sum(1)) / TAU[kind]
        # latent level per block, integrated (equal prior weights)
        L = np.array([np.bincount(blk, weights=w * loglik(e, b + k * S1 + S2, alpha).sum(1)) / TAU[kind] for k in LEVELS])
        mx = L.max(0)
        latent = np.sum(mx + np.log(np.mean(np.exp(L - mx), axis=0)))
        out_fixed.append(fixed)
        out_latent.append(latent)
    names = ["alone", "+2.5", "+3.0", "+4.0", "+4.5", "+5.0", "+6.0"]
    print(f"{kind:6s} fixed : " + " ".join(f"{n} {v - out_fixed[0]:+6.0f}" for n, v in zip(names, out_fixed)))
    print(f"{kind:6s} latent: " + " ".join(f"{n} {v - out_latent[0]:+6.0f}" for n, v in zip(names, out_latent))
          + f"   (latent vs fixed, alone: {out_latent[0] - out_fixed[0]:+.0f})")
