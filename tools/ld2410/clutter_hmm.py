"""Prototype: non-person sources of LD2410C energy as discrete "echo sources" (the LD2410C's ghosts):
per sensor off, or on with the profile of a standing person at slant r (0.75-6.5 m) times an
amplitude level; they begin at rate rho, live an Erlang(K) time with mean L (a few tens of seconds:
the event of 7.10. 01:08 in the kitchen lasted 40 s) and then end. The energies' likelihood is the
filter's (presence_tracker.ld2410, with the common gain). Compared per window:
  nobody | echo source only | a person at the best constant r (+ echo sources possible)
as log evidences. What the filter must weigh: log BF(person + echo / echo only) against its prior
log odds for an unseen person there.

usage: clutter_hmm.py SENSOR FROM TO [--rate PER_S] [--life S] [--stages K]"""
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
    ap.add_argument("--rate", type=float, default=1 / 1200)
    ap.add_argument("--life", type=float, default=40.0)
    ap.add_argument("--stages", type=int, default=4)
    ap.add_argument("--amps", type=float, nargs="+", default=[0.3, 1.0, 3.0])
    ap.add_argument("--ref", nargs=2, default=["2026-10-07 01:20:00", "2026-10-07 02:20:00"])
    a = ap.parse_args()
    D = load(REC)
    x = D[a.sensor]
    m = Model()
    lik = ld2410.Likelihood(m)
    E = np.concatenate([x["mg"], x["sg"][:, 2:]], axis=1).astype(float)
    ref = (x["t"] >= stamp(a.ref[0])) & (x["t"] < stamp(a.ref[1]))
    b = np.maximum(np.median(E[ref], axis=0), 1.0)
    rs = np.arange(0.75, 6.6, 0.5)
    prof = ld2410.expected(m, np.zeros(len(rs)), rs, np.ones(len(rs), bool))[STILL]
    src = np.concatenate([amp * prof for amp in a.amps])  # (J, 16)
    J = len(src)
    t0, t1 = stamp(a.t0), stamp(a.t1)
    sel = np.nonzero((x["t"] >= t0) & (x["t"] < t1))[0]
    t = x["t"][sel]
    dt = np.diff(np.concatenate([[t[0] - 0.09], t]))
    dt = np.where(dt <= 6, dt, 0)
    blk = np.floor(t - t0).astype(int)
    stats = []
    for k in range(blk.max() + 1):
        st = ld2410.Stats()
        for i in np.nonzero(blk == k)[0]:
            st.add(E[sel[i]], dt[i])
        stats.append(st)
    K = a.stages
    nu = K / a.life  # per stage

    def run(person, trace=None):
        """log evidence with the echo HMM; person: (16,) added to every state (trace: list to which
        the running log evidence is appended per block)."""
        # states: 0 off, then (stage k, source j)
        p = np.zeros(1 + K * J)
        p[0] = 1.0
        tot = 0.0
        for st in stats:
            T = max(st.time, 1e-3)
            q = np.zeros_like(p)
            # off -> on (stage 0, uniform over sources)
            on = p[0] * -np.expm1(-a.rate * T)
            q[0] = p[0] - on
            q[1:1 + J] += on / J
            # stages: each moves on with nu (one step per block at most, T << 1/nu)
            go = -np.expm1(-nu * T)
            for k in range(K):
                blkk = p[1 + k * J:1 + (k + 1) * J]
                q[1 + k * J:1 + (k + 1) * J] += blkk * (1 - go)
                if k + 1 < K:
                    q[1 + (k + 1) * J:1 + (k + 2) * J] += blkk * go
                else:
                    q[0] += blkk.sum() * go
            mu = np.vstack([b + person] + [b + person + src] * K)
            if st.time <= 0:
                p = q
                continue
            ll = st._mu_part(mu, lik)
            top = ll.max()
            w = q * np.exp(ll - top)
            z = w.sum()
            tot += top + np.log(z)
            p = w / z
            if trace is not None:
                trace.append(tot)
        return tot

    def plain(person):
        return sum(float(st._mu_part((b + person)[None, :], lik)[0]) for st in stats if st.time > 0)

    e_nobody = plain(np.zeros(16))
    e_echo = run(np.zeros(16))
    best_p = max((plain(prof[i]) - e_nobody, rs[i]) for i in range(len(rs)))
    best_pe = max((run(prof[i]) - e_echo, rs[i]) for i in range(len(rs)))
    # the running log Bayes factor person+echo / echo at the best r of the whole window
    i = int(np.argmin(abs(rs - best_pe[1])))
    tr_e, tr_p = [], []
    run(np.zeros(16), tr_e)
    run(prof[i], tr_p)
    d = np.array(tr_p) - np.array(tr_e)
    print("running log BF person+echo / echo every 5 s:", " ".join(f"{v:+.1f}" for v in d[::5]), f"max {d.max():+.1f}")
    print(f"{a.sensor} {a.t0}..{a.t1} ({len(stats)} s): echo / nobody {e_echo - e_nobody:+8.1f}; "
          f"person / nobody {best_p[0]:+8.1f} (r {best_p[1]:.2f}); person+echo / echo {best_pe[0]:+8.1f} (r {best_pe[1]:.2f})")


if __name__ == "__main__":
    main()
