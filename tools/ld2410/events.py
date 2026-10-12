"""How strongly do the energies of one sensor ask for a person, second by second, under the filter's
likelihood (presence_tracker.ld2410)? Per 1-s block: the best log-likelihood ratio "one standing
person at slant r on the axis" against "nobody" (r on a 0.25 m grid), with the background = the
gate's median in a quiet reference window.

usage: events.py SENSOR "YYYY-MM-DD HH:MM:SS" "YYYY-MM-DD HH:MM:SS" [--ref "from" "to"]"""
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


def stamp(v):
    return time.mktime(time.strptime(v, "%Y-%m-%d %H:%M:%S"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sensor")
    ap.add_argument("t0")
    ap.add_argument("t1")
    ap.add_argument("--ref", nargs=2, default=["2026-10-07 01:20:00", "2026-10-07 02:20:00"])
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--clutter", type=float, default=0.0,
                    help="also the log-likelihood ratio of a clutter episode against nobody: every gate's "
                         "background times its own texture T, 1/T ~ Gamma(nu, nu), no people")
    a = ap.parse_args()
    D = load(REC)
    x = D[a.sensor]
    m = Model()
    lik = ld2410.Likelihood(m)
    E = np.concatenate([x["mg"], x["sg"][:, 2:]], axis=1)
    r0, r1 = stamp(a.ref[0]), stamp(a.ref[1])
    ref = (x["t"] >= r0) & (x["t"] < r1)
    b = np.maximum(np.median(E[ref], axis=0), 1.0)
    rs = np.arange(0.75, 6.5, 0.25)
    S = ld2410.expected(m, np.zeros(len(rs)), rs, np.ones(len(rs), bool))[STILL]
    t0, t1 = stamp(a.t0), stamp(a.t1)
    sel = np.nonzero((x["t"] >= t0) & (x["t"] < t1))[0]
    t = x["t"][sel]
    dt = np.diff(np.concatenate([[t[0] - 0.09], t]))
    dt = np.where(dt <= 6, dt, 0)
    blk = np.floor(t - t0).astype(int)
    total = 0.0
    out = []
    for k in np.unique(blk):
        st = ld2410.Stats()
        for i in np.nonzero(blk == k)[0]:
            st.add(E[sel[i]].astype(float), dt[i])
        if st.time <= 0:
            continue
        lr = st.log_ratio(b[None, :] + S, b, lik)
        j = int(np.argmax(lr))
        total += lr[j]
        c = clutter_ratio(st, b, lik, a.clutter) if a.clutter else 0.0
        out.append((t0 + k, lr[j], rs[j], c))
    if not a.quiet:
        for tt, v, r, c in out:
            print(time.strftime("%H:%M:%S", time.localtime(tt)), f"best person at {r:.2f} m: {v:+8.2f}"
                  + (f"   clutter {c:+8.2f}   person - clutter {v - c:+8.2f}" if a.clutter else ""))
    v = np.array([o[1] for o in out])
    c = np.array([o[3] for o in out])
    print(f"{a.sensor} {a.t0}..{a.t1}: blocks {len(v)}, sum of the best ratios {v.sum():+.1f}, "
          f"blocks > 0: {np.mean(v > 0):.2f}, max {v.max():+.1f}"
          + (f"; clutter sum {c.sum():+.1f}, person - clutter sum {(v - c).sum():+.1f}, blocks person > clutter "
             f"{np.mean(v > c):.2f}" if a.clutter else ""))


def clutter_ratio(st, b, lik, nu):
    """log l(e | clutter) - log l(e | nobody): per gate the background times a texture T,
    1/T ~ Gamma(nu, nu), integrated exactly (censored frames counted as 100); against the model
    without people (with its common gain)."""
    a, tau = lik.alpha, lik.tau
    N = a * (st.t_unc + st.t_cens) / tau
    A = a * (st.t_e + 100.0 * st.t_cens) / tau / b
    from math import lgamma
    ll = np.array([nu * np.log(nu) - lgamma(nu) + lgamma(n + nu) - (n + nu) * np.log(nu + q) for n, q in zip(N, A)])
    ll -= N * np.log(b)
    return float(ll.sum()) - float(st._mu_part(b[None, :], lik)[0])


if __name__ == "__main__":
    main()
