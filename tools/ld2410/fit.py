"""Maximum-likelihood fit of the proposed gate-energy model (draft MODEL.md 4.3) on one-person frames
(the LD2450 of the housing sees exactly one target, no second within +-10 s), per kind (moving /
still gates) and mode (walking / standing):

  e_g ~ Gamma(shape alpha, mean mu_g), e = 100 censored (P(e >= 99.5)),
  mu_g = b_{s,g} + v(angle) * A (r / 1 m)^-n * k(c_g - r - delta),
  k(D) = exp(-D^2 / (2 sigma^2))                     for D <= 0 (main lobe, near side),
       = (1 - tau) exp(-D^2 / (2 sigma^2)) + tau exp(-D / ell)   for D > 0 (plus the multipath tail),
  c_g = (g + 0.5) 0.75 m the gate centre, r the LD2450 slant distance (scaled), b the background
  mean of the sensor and gate (no LD2450 target +-30 s), v = 1 in the fit (angle < angle_max).
Then, with the shape fixed, the amplitude per angle bin (beam pattern).

Needs scipy (offline only): uv run --project ../../tracker --with scipy python fit.py"""
import os
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaincc, gammaln

from excess import backgrounds
from geom import scales, weights
from load import load
from profile import single

D = load()
SC = scales()
C = (np.arange(9) + 0.5) * 0.75
BG = backgrounds()
SENSORS = [s for s in os.environ.get("LD_SENSORS", "").split(",") if s]


def data(mode, kind, amin, amax, every=5, rmax=7.5):
    R, A, E, B = [], [], [], []
    for s, x in D.items():
        if SENSORS and s not in SENSORS:
            continue
        w = weights(x["t"], cap=1.0)
        ok, r, ang, walk = single(x, s)
        m = ok & (walk if mode == "walk" else ~walk) & (ang >= amin) & (ang < amax) & (w > 0) & (r < rmax)
        idx = np.nonzero(m)[0][::every]
        R.append(r[idx]); A.append(ang[idx])
        E.append(x["mg" if kind == "moving" else "sg"][idx])
        B.append(np.broadcast_to(BG[s][0 if kind == "moving" else 1], (len(idx), 9)))
    gates = slice(0, 9) if kind == "moving" else slice(2, 9)
    return np.concatenate(R), np.concatenate(A), np.concatenate(E)[:, gates], np.concatenate(B)[:, gates], C[gates]


def kernel(Dl, sigma, tau, ell):
    main = np.exp(-0.5 * (Dl / sigma) ** 2)
    tail = np.exp(-np.maximum(Dl, 0) / ell)
    return np.where(Dl <= 0, main, (1 - tau) * main + tau * tail)


def mean(p, r, c, b, v=1.0):
    logA, n, delta, logs, ltau, logl = p[:6]
    tau = 1 / (1 + np.exp(-ltau))
    Dl = c[None, :] - r[:, None] - delta
    return b + np.atleast_1d(v)[:, None] * np.exp(logA) * r[:, None] ** (-n) * kernel(Dl, np.exp(logs), tau, np.exp(logl))


def nll(p, r, e, b, c, v=1.0, alpha=None):
    mu = np.maximum(mean(p, r, c, b, v), 0.3)
    a = np.exp(p[6]) if alpha is None else alpha
    sat = e >= 99.5
    ee = np.maximum(e, 0.5)
    ll = a * np.log(a / mu) + (a - 1) * np.log(ee) - a * ee / mu - gammaln(a)
    lls = np.log(np.maximum(gammaincc(a, a * 99.5 / mu), 1e-300))
    return -np.sum(np.where(sat, lls, ll)) / len(r)


def fit(mode, kind, amax=30.0):
    r, ang, e, b, c = data(mode, kind, 0, amax)
    p0 = np.array([np.log(100.0), 2.0, 0.4, np.log(0.4), 0.0, np.log(1.5), np.log(3.0)])
    res = minimize(nll, p0, args=(r, e, b, c), method="Nelder-Mead",
                   options=dict(maxiter=6000, maxfev=6000, xatol=1e-3, fatol=1e-6))
    res = minimize(nll, res.x, args=(r, e, b, c), method="L-BFGS-B")
    return res, len(r)


def describe(p):
    logA, n, delta, logs, ltau, logl, loga = p
    return (f"A {np.exp(logA):7.1f} n {n:4.2f} delta {delta:+.2f} m sigma {np.exp(logs):.2f} m "
            f"tail {1 / (1 + np.exp(-ltau)):.2f} ell {np.exp(logl):.2f} m alpha {np.exp(loga):.2f}")


if __name__ == "__main__":
    amax = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    for kind in ("moving", "still"):
        for mode in ("walk", "stand"):
            res, n = fit(mode, kind, amax)
            print(f"{kind:6s} {mode:5s} n={n:6d}  nll/frame {res.fun:.3f}  {describe(res.x)}")
            # beam pattern: amplitude per angle bin with the shape fixed
            out = []
            for a0 in range(0, 90, 15):
                r, ang, e, b, c = data(mode, kind, a0, a0 + 15)
                if len(r) < 100:
                    out.append(f"{a0}-{a0 + 15}: -")
                    continue
                f = lambda lv: nll(res.x, r, e, b, c, v=np.exp(lv[0]))
                rv = minimize(f, [0.0], method="Nelder-Mead")
                out.append(f"{a0}-{a0 + 15}: {np.exp(rv.x[0]):.2f} (n={len(r)})")
            print("     v(angle):", ", ".join(out))
            if not SENSORS:
                np.save(f"/home/leon/.cache/presence-tracker/fit_{kind}_{mode}.npy", res.x)
