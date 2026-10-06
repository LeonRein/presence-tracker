"""The proposed likelihood with separate noise of background and person (Satterthwaite moment match:
mean b + sum S, variance b^2/alpha_b + sum S^2/alpha_s), alpha_b from the background spread,
alpha_s the person's (slow, Swerling-like) fluctuation. Arbeitszimmer 22:20-22:50: tempered
log-likelihood ratio of a second standing person at r2 against Leon alone, and of nobody.

usage: check5.py alpha_s [tau_moving tau_still]"""
import sys
import time

import numpy as np
from scipy.special import gammaincc, gammaln

from check import BG, C, D, P, SC
from fit import mean
from geom import targets, weights

ARGV = sys.argv if __name__ == "__main__" else []
ALPHA_S = float(ARGV[1]) if len(ARGV) > 1 else 1.5
TAU = {"moving": float(ARGV[2]) if len(ARGV) > 2 else 1.0, "still": float(ARGV[3]) if len(ARGV) > 3 else 8.0}
ALPHA_B = {"moving": 6.0, "still": 25.0}  # measured CV 0.4 / 0.2 of the background (shape.py)


def loglik(e, b, S, ab, as_):
    mu = np.maximum(b + S, 0.3)
    V = b ** 2 / ab + S ** 2 / as_
    a = mu ** 2 / V
    ee = np.maximum(e, 0.5)
    ll = a * np.log(a / mu) + (a - 1) * np.log(ee) - a * ee / mu - gammaln(a)
    ls = np.log(np.maximum(gammaincc(a, a * 99.5 / mu), 1e-300))
    return np.where(e >= 99.5, ls, ll)


def window(sensor, a, b_, people=None):
    x = D[sensor]
    t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M")) for v in (a, b_))
    m = (x["t"] >= t0) & (x["t"] < t1)
    r, ang, valid, walk = targets(x, SC[sensor])
    rl = np.max(np.where(valid[m], r[m], 0), axis=1)
    return x, m, np.where(rl > 0, rl, 1.5), weights(x["t"], cap=1.0)[m]


if __name__ == "__main__":
    x, m, rl, w = window("arbeitszimmer", "22:20", "22:50")
    rl = np.full(len(rl), np.median(rl))  # one smoothed position (as the filter has it), not each LD2450 frame
    for kind, key, gs in (("moving", "mg", slice(0, 9)), ("still", "sg", slice(2, 9))):
        p = P[(kind, "stand")]
        b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
        e = x[key][m][:, gs]
        S1 = mean(p, rl, C[gs], 0 * b)
        l1 = np.sum(w * loglik(e, b, S1, ALPHA_B[kind], ALPHA_S).sum(1)) / TAU[kind]
        l0 = np.sum(w * loglik(e, b, 0 * S1, ALPHA_B[kind], ALPHA_S).sum(1)) / TAU[kind]
        out = [f"nobody {l0 - l1:+8.0f}"]
        for r2 in (2.5, 3.0, 4.0, 4.5, 5.0, 6.0):
            S2 = mean(p, np.full(len(rl), r2), C[gs], 0 * b)
            l2 = np.sum(w * loglik(e, b, S1 + S2, ALPHA_B[kind], ALPHA_S).sum(1)) / TAU[kind]
            out.append(f"+{r2:.1f} {l2 - l1:+6.0f}")
        print(f"{kind:6s} alpha_s {ALPHA_S} tau {TAU[kind]:.0f} s: " + " ".join(out))
