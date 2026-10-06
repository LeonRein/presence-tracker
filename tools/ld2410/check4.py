"""Arbeitszimmer 22:20-22:50, still gates: per gate the observed median / P(saturated) against the
model's for Leon alone, and the per-gate contribution to the tempered log-likelihood ratio of a
second person at 2.5 m."""
import time

import numpy as np
from scipy.special import gammaincc

from check import BG, C, D, P, SC, TAU, loglik
from fit import mean
from geom import targets, weights

x = D["arbeitszimmer"]
t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M")) for v in ("22:20", "22:50"))
m = (x["t"] >= t0) & (x["t"] < t1)
r, ang, valid, walk = targets(x, SC["arbeitszimmer"])
rl = np.max(np.where(valid[m], r[m], 0), axis=1)
rl = np.where(rl > 0, rl, 1.5)
w = weights(x["t"], cap=1.0)[m]
for kind, key, gs in (("still", "sg", slice(2, 9)), ("moving", "mg", slice(0, 9))):
    p = P[(kind, "stand")]
    a = np.exp(p[6])
    b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
    e = x[key][m][:, gs]
    mu1 = mean(p, rl, C[gs], b)
    mu2 = mu1 + mean(p, np.full(len(rl), 2.5), C[gs], 0 * b)
    d = np.sum(w[:, None] * (loglik(e, mu2, a) - loglik(e, mu1, a)), 0) / TAU[kind]
    print(kind)
    for j, g in enumerate(range(gs.start, gs.stop)):
        print(f"  g{g}: observed median {np.median(e[:, j]):5.0f} P(100) {np.mean(e[:, j] >= 99.5):.2f} | model mean {np.median(mu1[:, j]):6.1f}"
              f" median ~{np.median(mu1[:, j]) * (1 - 1 / (3 * a)):5.0f} P(100) {np.median(gammaincc(a, a * 99.5 / mu1[:, j])):.2f}"
              f" | LLR(+2.5 m) {d[j]:+6.0f}")
