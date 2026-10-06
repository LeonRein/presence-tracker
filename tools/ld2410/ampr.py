"""Amplitude per slant bin with the fitted profile shape fixed (n = 0 inside a bin): does the power
law A r^-n hold near the sensor? Prints the fitted amplitude per bin, the power law's, and the
saturated fraction of the strongest gate observed vs predicted. (fit.py must have run.)"""
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaincc

from fit import data, mean, nll

kind = sys.argv[1] if len(sys.argv) > 1 else "still"
mode = sys.argv[2] if len(sys.argv) > 2 else "stand"
p = np.load(f"/home/leon/.cache/presence-tracker/fit_{kind}_{mode}.npy")
r, ang, e, b, c = data(mode, kind, 0, 30, every=3)
for lo in np.arange(0.75, 6.5, 0.375):
    m = (r >= lo) & (r < lo + 0.375)
    if m.sum() < 150:
        continue
    q = p.copy()
    q[1] = 0.0

    def f(v):
        q[0] = v[0]
        return nll(q, r[m], e[m], b[m], c)
    res = minimize(f, [p[0] - p[1] * np.log(lo + 0.19)], method="Nelder-Mead")
    q[0] = res.x[0]
    mu = mean(q, r[m], c, b[m])
    j = int(np.argmax(mu.mean(0)))
    a = np.exp(p[6])
    print(f"{kind} {mode} r {lo:.2f}-{lo + 0.375:.2f} n={m.sum():5d}: amplitude {np.exp(res.x[0]):7.1f}  power law "
          f"{np.exp(p[0]) * (lo + 0.19) ** -p[1]:7.1f}   gate {j + (2 if kind == 'still' else 0)}: P(100) observed "
          f"{np.mean(e[m][:, j] >= 99.5):.2f} fitted {np.mean(gammaincc(a, a * 99.5 / mu[:, j])):.2f}")
