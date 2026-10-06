"""How the fitted model (fit.py, one Gamma shape per kind/mode) judges whole 60-s blocks, with the
frames tempered by a look time T (log-likelihood times dt / T):
  E: blocks without any LD2450 target in this sensor for +-30 s: max over r of the log-likelihood
     ratio "one standing person at slant r (in the beam)" vs "nobody" (> 0: a phantom is preferred);
  O: blocks with exactly one LD2450 target: "nobody" vs that person (should be << 0), and the best
     "second standing person at r2" vs "that person alone" (> 0: a second person is preferred).
Moving and still gates together. Background: the mean of the sensor (excess.backgrounds).

usage: blocks.py T_moving T_still"""
import sys

import numpy as np

from background import clear_masks
from check import BG, C, D, P, SC, loglik
from fit import mean
from geom import weights
from profile import single

ARGS = [a for a in sys.argv[1:] if a not in ("raw", "satt")]
TM = float(ARGS[0]) if ARGS else 1.0
TS = float(ARGS[1]) if len(ARGS) > 1 else 8.0
ALPHA_S = float(ARGS[2]) if len(ARGS) > 2 else 2.5
R2 = np.arange(1.0, 7.01, 0.5)
SMOOTH = "raw" not in sys.argv  # the person at a 5-s moving average of the LD2450 slant (as a filter would have him)
SATT = "satt" in sys.argv  # Satterthwaite: background shape per sensor and gate (1/CV^2), person shape ALPHA_S


def background_shapes():
    out = {}
    for s, x in D.items():
        w = weights(x["t"])
        none_all, _ = clear_masks(x, s)
        m = none_all & (w > 0)
        res = []
        for key in ("mg", "sg"):
            mu = np.average(x[key][m], axis=0, weights=w[m])
            var = np.average((x[key][m] - mu) ** 2, axis=0, weights=w[m])
            res.append(np.where(var > 0, mu ** 2 / np.maximum(var, 1e-9), 25.0))
        out[s] = res
    return out


AB = background_shapes() if SATT else None


def ll(x, idx, S_m, S_s, w):
    """Tempered log-likelihood of frames idx given person contributions (n, 9) and (n, 7)."""
    s = 0.0
    for key, S, gs, k, T in (("mg", S_m, slice(0, 9), 0, TM), ("sg", S_s, slice(2, 9), 1, TS)):
        kind = "moving" if k == 0 else "still"
        b = BG[x["_s"]][k][gs]
        e = x[key][idx][:, gs]
        if SATT:
            from check5 import loglik as sloglik
            s += np.sum(w[idx, None] * sloglik(e, b, S, AB[x["_s"]][k][gs], ALPHA_S)) / T
        else:
            a = np.exp(P[(kind, "stand")][6])
            s += np.sum(w[idx, None] * loglik(e, b + S, a)) / T
    return s


def contrib(r):
    r = np.atleast_1d(r).astype(float)
    return (mean(P[("moving", "stand")], r, C, np.zeros(9)),
            mean(P[("still", "stand")], r, C[2:], np.zeros(7)))


if __name__ == "__main__":
    E_res, O_none, O_second = [], [], []
    for s, x in D.items():
        x["_s"] = s
        w = weights(x["t"])
        none_all, _ = clear_masks(x, s)
        ok, r, ang, walk = single(x, s)
        blk = np.floor((x["t"] - x["t"][0]) / 60).astype(int)
        for k in np.unique(blk):
            idx = np.nonzero(blk == k)[0]
            if w[idx].sum() < 50:
                continue
            n = len(idx)
            if none_all[idx].all():
                base = ll(x, idx, np.zeros((n, 9)), np.zeros((n, 7)), w)
                best = max(ll(x, idx, *[np.repeat(c, n, 0) for c in contrib(r2)], w) - base for r2 in R2)
                E_res.append((s, best))
            elif ok[idx].all() and (ang[idx] < 45).all():
                rs = np.convolve(np.pad(r[idx], 28, mode="edge"), np.ones(57) / 57, mode="valid") if SMOOTH else r[idx]
                if SMOOTH and walk[idx].mean() < 0.2:  # standing: one position per block (the filter's x, without the wandering offset)
                    rs = np.full(n, np.median(r[idx]))
                Sm, Ss = contrib(rs)
                base = ll(x, idx, Sm, Ss, w)
                O_none.append((s, ll(x, idx, 0 * Sm, 0 * Ss, w) - base))
                best = max(ll(x, idx, Sm + np.repeat(contrib(r2)[0], n, 0), Ss + np.repeat(contrib(r2)[1], n, 0), w) - base
                           for r2 in R2)
                O_second.append((s, best))
    print(f"look times: moving {TM} s, still {TS} s")
    for name, res in (("E: phantom in an empty view (best r)", E_res), ("O: nobody instead of the one person", O_none),
                      ("O: a second person (best r2)", O_second)):
        v = np.array([b for _, b in res])
        q = np.percentile(v, [10, 50, 90, 99]) if len(v) else [np.nan] * 4
        print(f"  {name:40s} blocks {len(v):4d}  q10 {q[0]:+8.1f} q50 {q[1]:+8.1f} q90 {q[2]:+8.1f} q99 {q[3]:+8.1f}"
              f"  P(>0) {np.mean(v > 0):.2f}  P(>3) {np.mean(v > 3):.2f}")
        for s in D:
            vs = np.array([b for ss, b in res if ss == s])
            if len(vs):
                print(f"      {s:14s} n {len(vs):4d}  median {np.median(vs):+8.1f}  P(>0) {np.mean(vs > 0):.2f}  max {vs.max():+8.1f}")
