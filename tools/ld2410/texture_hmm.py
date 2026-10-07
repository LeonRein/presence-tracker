"""Prototype: the background texture as a slowly varying state per gate (compound-Gaussian clutter,
Ward 1981; Ward, Tough & Watts 2006): energy ~ Gamma(alpha, mean T b + S), T on a grid of levels, a
Markov chain that keeps its level with exp(-dt / tau_T) and else draws a new one from the prior
(measured on the empty night). Per gate a forward filter (exact on the grid), gates independent.

Compares, over a whole window, "nobody" against "one standing person at slant r on the axis"
(best constant r), with the texture integrated out in both - the log Bayes factor the filter would
have to overcome with its prior of an unseen person.

usage: texture_hmm.py SENSOR FROM TO [--tau-t S] [--prior night] [--ref FROM TO]
       (times "YYYY-MM-DD HH:MM:SS")"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tracker"))
from presence_tracker import ld2410  # noqa: E402
from presence_tracker.filtermodel import STILL, Model  # noqa: E402

from load import load  # noqa: E402

REC = "/home/leon/checkout/presence-tracker/recordings/*.jsonl"
LEVELS = np.exp(np.linspace(np.log(0.4), np.log(40.0), 24))


def stamp(v):
    return time.mktime(time.strptime(v, "%Y-%m-%d %H:%M:%S"))


def blocks(x, t0, t1):
    """Per 1-s block the tempered statistics (looks uncensored, looks x energy, looks censored) (n, 16)."""
    m = Model()
    lik = ld2410.Likelihood(m)
    sel = np.nonzero((x["t"] >= t0) & (x["t"] < t1))[0]
    t = x["t"][sel]
    E = np.concatenate([x["mg"][sel], x["sg"][sel][:, 2:]], axis=1).astype(float)
    dt = np.diff(np.concatenate([[t[0] - 0.09], t]))
    dt = np.where(dt <= 6, dt, 0)
    blk = np.floor(t - t0).astype(int)
    n = blk.max() + 1
    cens = E >= ld2410.CAP
    w = dt[:, None] / lik.tau[None, :]
    nu = np.zeros((n, 16)); ne = np.zeros((n, 16)); nc = np.zeros((n, 16)); bt = np.zeros(n)
    for j in range(16):
        nu[:, j] = np.bincount(blk, w[:, j] * ~cens[:, j], n)
        ne[:, j] = np.bincount(blk, w[:, j] * np.where(cens[:, j], 0, np.maximum(E[:, j], 0.5)), n)
        nc[:, j] = np.bincount(blk, w[:, j] * cens[:, j], n)
    bt = np.bincount(blk, dt, n)
    return nu, ne, nc, bt, lik


def gate_ll(nu, ne, nc, mu, alpha):
    """(blocks, levels) log-likelihood of one gate given means mu (blocks, levels)."""
    out = -alpha * nu[:, None] * np.log(mu) - alpha * ne[:, None] / mu
    c = nc > 0
    if c.any():
        out[c] += nc[c][:, None] * ld2410.log_censored(np.full(mu[c].shape, alpha), mu[c])
    return out


def forward(ll, prior, stay):
    """log evidence of a Markov chain over levels: keep with stay (blocks,), else redraw from prior."""
    p = prior.copy()
    tot = 0.0
    for k in range(len(ll)):
        p = stay[k] * p + (1 - stay[k]) * prior
        top = ll[k].max()
        q = p * np.exp(ll[k] - top)
        z = q.sum()
        tot += top + np.log(z)
        p = q / z
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sensor")
    ap.add_argument("t0")
    ap.add_argument("t1")
    ap.add_argument("--tau-t", type=float, default=30.0)
    ap.add_argument("--ref", nargs=2, default=["2026-10-07 01:20:00", "2026-10-07 02:20:00"])
    ap.add_argument("--prior-from", nargs=2, default=["2026-10-06 22:56:00", "2026-10-07 05:30:00"])
    ap.add_argument("--tail", type=float, default=1.0, help="scale the prior's weight above level 2")
    ap.add_argument("--own", action="store_true", help="the texture prior from this sensor alone (per gate)")
    a = ap.parse_args()
    D = load(REC)
    x = D[a.sensor]
    E = np.concatenate([x["mg"], x["sg"][:, 2:]], axis=1)
    ref = (x["t"] >= stamp(a.ref[0])) & (x["t"] < stamp(a.ref[1]))
    b = np.maximum(np.median(E[ref], axis=0), 1.0)
    sensors = [a.sensor] if a.own else None
    prior = texture_prior(D, stamp(a.prior_from[0]), stamp(a.prior_from[1]), a.tail, sensors)
    nu, ne, nc, bt, lik = blocks(x, stamp(a.t0), stamp(a.t1))
    stay = np.exp(-np.maximum(bt, 1e-3) / a.tau_t)
    m = Model()
    rs = np.arange(0.75, 6.5, 0.25)
    S = ld2410.expected(m, np.zeros(len(rs)), rs, np.ones(len(rs), bool))[STILL]

    def evidence(Sr):
        tot = 0.0
        for g in range(16):
            mu = LEVELS[None, :] * b[g] + Sr[g]
            mu = np.broadcast_to(mu, (len(nu), len(LEVELS)))
            tot += forward(gate_ll(nu[:, g], ne[:, g], nc[:, g], mu, lik.alpha[g]), prior, stay)
        return tot

    e0 = evidence(np.zeros(16))
    best = max((evidence(S[i]) - e0, rs[i]) for i in range(len(rs)))
    # the same without texture (level 1 always)
    flat = np.zeros(len(LEVELS)); flat[np.argmin(abs(np.log(LEVELS)))] = 1.0
    prior_keep = prior

    def evidence_flat(Sr):
        tot = 0.0
        for g in range(16):
            mu = np.full(len(nu), b[g] + Sr[g])
            tot += float(gate_ll(nu[:, g], ne[:, g], nc[:, g], mu[:, None], lik.alpha[g]).sum())
        return tot
    f0 = evidence_flat(np.zeros(16))
    best_flat = max((evidence_flat(S[i]) - f0, rs[i]) for i in range(len(rs)))
    print(f"{a.sensor} {a.t0}..{a.t1} ({len(nu)} s): log Bayes factor person (best r) / nobody: "
          f"with texture {best[0]:+8.1f} (r {best[1]:.2f}), without {best_flat[0]:+8.1f} (r {best_flat[1]:.2f})")


_PRIOR = {}


def texture_prior(D, t0, t1, tail, sensors=None):
    """Weights of the levels: the histogram of block means over the gate's median, pooled over the
    sensors, gates and the empty night (seconds with an LD2450 target anywhere +-60 s left out),
    smoothed a little; floor 1e-4."""
    key = (t0, t1, tail, tuple(sensors or ()))
    if key in _PRIOR:
        return _PRIOR[key]
    busy = set()
    for s, x in D.items():
        m = (x["t"] >= t0 - 60) & (x["t"] < t1 + 60) & (x["tg"][..., 3] > 0).any(axis=1)
        for t in np.floor(x["t"][m]).astype(int):
            busy.update(range(t - 60, t + 61))
    ratios = []
    edges = np.sqrt(LEVELS[1:] * LEVELS[:-1])
    for s, x in D.items():
        if sensors and s not in sensors:
            continue
        m = (x["t"] >= t0) & (x["t"] < t1)
        t = x["t"][m]
        E = np.concatenate([x["mg"][m], x["sg"][m][:, 2:]], axis=1)
        sec = np.floor(t).astype(int)
        ok = np.array([q not in busy for q in sec])
        E, sec = E[ok], sec[ok]
        u, inv, cnt = np.unique(sec, return_inverse=True, return_counts=True)
        means = np.stack([np.bincount(inv, weights=E[:, j]) / cnt for j in range(16)], axis=1)
        level = np.maximum(np.median(means, axis=0), 1.0)
        ratios.append((np.maximum(means, 0.5) / level).ravel())
    r = np.concatenate(ratios)
    h = np.bincount(np.searchsorted(edges, r), minlength=len(LEVELS)).astype(float)
    h = np.convolve(h, [0.25, 0.5, 0.25], mode="same") + 1e-4 * len(r)
    h[LEVELS > 2] *= tail
    h /= h.sum()
    _PRIOR[key] = h
    print("texture prior (level: weight):", " ".join(f"{l:.1f}:{w:.3f}" for l, w in zip(LEVELS, h)))
    return h


if __name__ == "__main__":
    main()
