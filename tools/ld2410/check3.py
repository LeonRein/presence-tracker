"""Arbeitszimmer 22:20-22:50: the same comparison as check2.py, but with a slowly varying gain u_g
per gate (log u ~ Ornstein-Uhlenbeck, time constant T, stationary sd eps) multiplying the expected
energy - the model's discrepancy as a nuisance state, integrated by Gauss-Hermite quadrature per
1-s block (prequential log-evidence). Each hypothesis carries its own gain (Bayes factor with the
nuisance integrated out).

usage: check3.py [own] [people] [T_s] [eps]   (people: the gain multiplies only the people's part)"""
import sys
import time

import numpy as np

from check import BG, C, D, P, SC, TAU, loglik
from fit import mean
from geom import targets, weights

args = [a for a in sys.argv[1:] if a not in ("own", "people")]
ON_PEOPLE = "people" in sys.argv  # the gain multiplies the people's part only, not the background
if "own" in sys.argv:
    lg = lambda v: np.log(v / (1 - v))  # noqa: E731
    P[("still", "stand")] = np.array([np.log(591.6), 2.26, 0.34, np.log(0.71), lg(0.42), np.log(1.59), np.log(2.39)])
    P[("moving", "stand")] = np.array([np.log(53.2), 2.47, 0.49, np.log(0.52), lg(0.22), np.log(1.33), np.log(3.01)])
T = float(args[0]) if args else 300.0
EPS = float(args[1]) if len(args) > 1 else 0.4
GH_X, GH_W = np.polynomial.hermite_e.hermegauss(9)
GH_W = GH_W / GH_W.sum()

x = D["arbeitszimmer"]
t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M")) for v in ("22:20", "22:50"))
m = (x["t"] >= t0) & (x["t"] < t1)
r, ang, valid, walk = targets(x, SC["arbeitszimmer"])
rl = np.max(np.where(valid[m], r[m], 0), axis=1)
rl = np.where(rl > 0, rl, 1.5)
w = weights(x["t"], cap=1.0)[m]
blk = np.floor(x["t"][m] - t0).astype(int)
nb = blk.max() + 1


def evidence(e, b, S, alpha, tau):
    """Prequential log-evidence with a gain per gate (columns of e)."""
    G = e.shape[1]
    mu_l = np.zeros(G)
    var_l = np.full(G, EPS ** 2)
    total = 0.0
    a = np.exp(-1.0 / T)
    for k in range(nb):
        sel = blk == k
        if not sel.any():
            mu_l *= a
            var_l = var_l * a * a + EPS ** 2 * (1 - a * a)
            continue
        nodes = mu_l[None, :] + np.sqrt(var_l)[None, :] * GH_X[:, None]  # (9, G)
        L = np.zeros((len(GH_X), G))
        for i in range(len(GH_X)):
            u = np.exp(nodes[i])
            mu = b + S[sel] * u if ON_PEOPLE else (b + S[sel]) * u
            L[i] = np.sum(w[sel, None] * loglik(e[sel], mu, alpha), axis=0) / tau
        mx = L.max(0)
        q = GH_W[:, None] * np.exp(L - mx)
        Z = q.sum(0)
        total += np.sum(mx + np.log(Z))
        post = q / Z
        mu_l = np.sum(post * nodes, 0)
        var_l = np.maximum(np.sum(post * nodes ** 2, 0) - mu_l ** 2, 1e-6)
        mu_l *= a
        var_l = var_l * a * a + EPS ** 2 * (1 - a * a)
    return total


for kind, key, gs in (("moving", "mg", slice(0, 9)), ("still", "sg", slice(2, 9))):
    p = P[(kind, "stand")]
    alpha = np.exp(p[6])
    b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
    e = x[key][m][:, gs]
    S1 = mean(p, rl, C[gs], 0 * b)
    base = evidence(e, b, S1, alpha, TAU[kind])
    nob = evidence(e, b, 0 * S1, alpha, TAU[kind])
    out = [f"nobody {nob - base:+7.0f}"]
    for r2 in (2.5, 3.0, 4.0, 4.5, 5.0, 6.0):
        S2 = mean(p, np.full(len(rl), r2), C[gs], 0 * b)
        out.append(f"+{r2:.1f} {evidence(e, b, S1 + S2, alpha, TAU[kind]) - base:+6.0f}")
    print(f"{kind:6s} gain{' on people' if ON_PEOPLE else ''} T {T:.0f} s eps {EPS}: " + " ".join(out))
