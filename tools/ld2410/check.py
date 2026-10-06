"""Checks of the fitted gate-energy model (fit.py must have run):
(a) predicted vs measured mean energy per gate for one standing person in slant bins;
(b) Arbeitszimmer 22:20-22:50 (Leon alone at the desk, ~1.5 m): mean log-likelihood ratio per frame
    of "Leon + a second standing person at slant r2 (in the beam)" against "Leon alone", and of
    "nobody" against "Leon alone" - does the far-gate energy ask for a second person?
(c) the same per time with the frames tempered by tau (1 s moving, 8 s still)."""
import time

import numpy as np
from scipy.special import gammaincc, gammaln

from excess import backgrounds, collect
from fit import C, mean
from geom import scales, targets, weights
from load import load

D = load()
SC = scales()
BG = backgrounds()
P = {(k, m): np.load(f"/home/leon/.cache/presence-tracker/fit_{k}_{m}.npy") for k in ("moving", "still") for m in ("walk", "stand")}
TAU = {"moving": 1.0, "still": 8.0}


def loglik(e, mu, alpha):
    mu = np.maximum(mu, 0.3)
    ee = np.maximum(e, 0.5)
    ll = alpha * np.log(alpha / mu) + (alpha - 1) * np.log(ee) - alpha * ee / mu - gammaln(alpha)
    ls = np.log(np.maximum(gammaincc(alpha, alpha * 99.5 / mu), 1e-300))
    return np.where(e >= 99.5, ls, ll)


if __name__ == "__main__":
    print("(a) one standing person, angle < 30: measured | predicted mean energy (background of the arbeitszimmer)")
    R, A, EM, ES, WT, S = collect("stand", 0, 30)
    for kind, E, gs in (("moving", EM, slice(0, 9)), ("still", ES, slice(2, 9))):
        b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
        for lo in (0.9, 1.3, 1.7, 2.1, 2.9, 3.3, 4.0, 4.7, 5.4):
            m = (R >= lo) & (R < lo + 0.4)
            if WT[m].sum() < 30:
                continue
            meas = np.average(E[m], axis=0, weights=WT[m])[gs] + b
            pred = mean(P[(kind, "stand")], np.array([lo + 0.2]), C[gs], b)[0]
            print(f"  {kind:6s} r {lo:.1f}-{lo + 0.4:.1f}: " + " ".join(f"{v:3.0f}" for v in meas) + "  |  "
                  + " ".join(f"{v:3.0f}" for v in pred))
    print("\n(b,c) Arbeitszimmer 22:20-22:50: log-likelihood ratio against 'Leon alone at his LD2450 slant'")
    x = D["arbeitszimmer"]
    t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M")) for v in ("22:20", "22:50"))
    m = (x["t"] >= t0) & (x["t"] < t1)
    r, ang, valid, walk = targets(x, SC["arbeitszimmer"])
    rl = np.where(valid[m].any(1), np.max(np.where(valid[m], r[m], 0), axis=1), 1.5)
    rl = np.where(rl > 0, rl, 1.5)
    w = weights(x["t"], cap=1.0)[m]
    T = w.sum()
    for kind, key, gs in (("moving", "mg", slice(0, 9)), ("still", "sg", slice(2, 9))):
        p = P[(kind, "stand")]
        alpha = np.exp(p[6])
        b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
        e = x[key][m][:, gs]
        mu1 = mean(p, rl, C[gs], b)
        l1 = loglik(e, mu1, alpha).sum(1)
        l0 = loglik(e, np.broadcast_to(b, e.shape), alpha).sum(1)
        out = [f"nobody {np.sum(w * (l0 - l1)) / TAU[kind]:+9.0f}"]
        for r2 in (2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0):
            mu2 = mu1 + mean(p, np.full(len(rl), r2), C[gs], 0 * b)
            l2 = loglik(e, mu2, alpha).sum(1)
            out.append(f"+{r2:.1f} m {np.sum(w * (l2 - l1)) / TAU[kind]:+7.0f}")
        print(f"  {kind:6s} over {T / 60:.0f} min, tempered (sum dt/tau): " + ", ".join(out))
        print(f"  {kind:6s} fraction of 1-s blocks where a second person at 4.5 m is preferred: ", end="")
        mu2 = mu1 + mean(p, np.full(len(rl), 4.5), C[gs], 0 * b)
        d = loglik(e, mu2, alpha).sum(1) - l1
        blk = np.floor((x["t"][m] - t0)).astype(int)
        s = np.bincount(blk, weights=w * d)
        print(f"{np.mean(s > 0):.3f}")
