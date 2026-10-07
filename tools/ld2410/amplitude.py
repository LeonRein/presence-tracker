"""The amplitude of one person per stay (MODEL.md 4.3, 9): how much more or less energy than the
average profile does one person put into the gates, constant over a stay? And is it the LD2450's
detectability kappa (4.1) - does a person the LD2450 finds again often also give off more energy?

Stays: one-person frames (profile.single: the LD2450 of the housing sees exactly one target, no
second within +-10 s), standing, < 45 degrees off the axis, the target within 0.7 m of where the stay
began; a stay ends when the target walks for > 1 s, moves away, or is gone for > 120 s (gaps without
a target are part of the stay: the LD2450 lost it). Walks: walking one-person frames, gaps < 1 s,
>= 3 s long. Per stay and kind (moving gates 0-8 / still 2-8) the gain g on the person's profile
(fit.py, background fixed): posterior of log g on a grid (flat prior), the frames tempered by
dt / tau as in the filter (tau 4 s moving / 13 s still), Gamma shapes 2.5 / 2.0, 100 censored.

Then: the spread of log g between stays (minus the estimation noise) -> the Gamma shape beta of a
gain Gamma(beta, beta), var log g = trigamma(beta); how moving and still gains go together; the
correlation of log g between the two halves of a stay (persistence); and with the LD2450's
re-acquisition rate in the stay (its kappa, 4.1).

usage: amplitude.py [min_stay_s]"""
import sys

import numpy as np
from scipy.special import gammaincc, polygamma
from scipy.stats import spearmanr

from fit import BG, C, D, SC, mean
from profile import single
from geom import weights

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
P = {(k, m): np.load(f"/home/leon/.cache/presence-tracker/fit_{k}_{m}.npy") for k in ("moving", "still")
     for m in ("stand", "walk")}
ALPHA = {"moving": 2.5, "still": 2.0}
TAU = {"moving": 4.0, "still": 13.0}
GATES = {"moving": slice(0, 9), "still": slice(2, 9)}
LG = np.linspace(-3, 3, 241)  # log g


def post(kind, mode, r, e, b, dt):
    """(mean, sd) of log g for the frames (r (k,), e (k, cells), b (cells,), dt (k,))."""
    c = C[GATES[kind]]
    S = mean(P[(kind, mode)], r, c, np.zeros_like(b))
    a = ALPHA[kind]
    sat = e >= 99.5
    ee = np.maximum(e, 0.5)
    w = (dt / TAU[kind])[:, None]
    ll = np.zeros(len(LG))
    for i, lg in enumerate(LG):
        mu = np.maximum(b[None, :] + np.exp(lg) * S, 0.3)
        l = a * np.log(a / mu) - a * ee / mu
        ls = np.log(np.maximum(gammaincc(a, a * 99.5 / mu), 1e-300))
        ll[i] = np.sum(w * np.where(sat, ls, l))
    p = np.exp(ll - ll.max())
    p /= p.sum()
    m = float(p @ LG)
    return m, float(np.sqrt(p @ (LG - m) ** 2)), float(S.max())


def segments(x, s):
    t = x["t"]
    ok, r, ang, walk = single(x, s)
    tg = x["tg"]
    i = np.argmax(tg[..., 3] > 0, axis=1)
    k = np.arange(len(t))
    pos = SC[s] * tg[k, i, :2]
    dt = weights(t, cap=1.0)
    stays, walks = [], []
    # stays
    cur, start, last_seen, walk_since = None, None, None, None
    for j in range(len(t)):
        if ok[j] and walk[j]:
            walk_since = t[j] if walk_since is None else walk_since
        elif ok[j]:
            walk_since = None
        far = cur is not None and ok[j] and np.hypot(*(pos[j] - start)) > 0.7
        gone = cur is not None and last_seen is not None and t[j] - last_seen > 120
        walked = cur is not None and walk_since is not None and t[j] - walk_since > 1.0
        other = not ok[j] and (tg[j, :, 3] > 0).sum() >= 2
        if cur is not None and (far or gone or walked or other):
            stays.append(cur)
            cur = None
        if ok[j] and not walk[j] and ang[j] < 45 and r[j] < 7.5:
            if cur is None:
                cur, start = [], pos[j].copy()
            cur.append(j)
            last_seen = t[j]
        elif cur is not None and not (tg[j, :, 3] > 0).any():
            cur.append(-j - 1)  # a frame without a target within the stay
    if cur:
        stays.append(cur)
    # walks
    cur, last = [], -1e9
    for j in np.nonzero(ok & walk & (ang < 45) & (r < 7.5))[0]:
        if t[j] - last > 1.0 and cur:
            walks.append(cur)
            cur = []
        cur.append(j)
        last = t[j]
    if cur:
        walks.append(cur)
    return t, r, dt, stays, walks


def within(v, lg, A, sens):
    """Spearman of v and log g within groups of stays of one sensor at similar r (1 m bins): takes out
    what the distance and the place do to both."""
    xs, ys = [], []
    for s in set(sens):
        j = np.nonzero(sens == s)[0]
        for r0 in np.arange(0.5, 8, 1.0):
            g = j[(A[j, 2] >= r0) & (A[j, 2] < r0 + 1.0)]
            if len(g) < 3:
                continue
            xs += list(v[g] - v[g].mean())
            ys += list(lg[g] - lg[g].mean())
    return spearmanr(xs, ys)[0] if len(xs) > 3 else np.nan, len(xs)


rows, wrows = [], []
for s, x in D.items():
    t, r, dt, stays, walks = segments(x, s)
    for st in stays:
        seen = np.array([j for j in st if j >= 0])
        lost = np.array([-j - 1 for j in st if j < 0], dtype=int)
        span = t[st[-1] if st[-1] >= 0 else -st[-1] - 1] - t[st[0]]
        if dt[seen].sum() < MIN:
            continue
        # the LD2450: how often it found the person again after losing it (per s lost)
        valid = np.zeros(len(t), bool)
        valid[seen] = True
        idx = np.sort(np.concatenate([seen, lost]))
        v = valid[idx]
        # the LD2450 holds a lost still target (bit-identical coordinates): lost = frozen frames,
        # found again = a frozen run ending
        xy = x["tg"][seen][:, :, :2].reshape(len(seen), -1)
        frozen = np.concatenate([[False], np.all(xy[1:] == xy[:-1], axis=1) & (np.diff(t[seen]) < 1.0)])
        finds = int(np.sum(frozen[:-1] & ~frozen[1:])) + int(np.sum(v[1:] & ~v[:-1]))
        t_lost = float(dt[lost].sum() + dt[seen][frozen].sum())
        out = [s, t[st[0]], span, float(dt[seen].sum()), float(np.median(r[seen])), finds, t_lost]
        for kind, key in (("moving", "mg"), ("still", "sg")):
            g = GATES[kind]
            e = x[key][seen][:, g]
            b = BG[s][0 if kind == "moving" else 1][g]
            out += list(post(kind, "stand", r[seen], e, b, dt[seen]))
            h = len(seen) // 2
            out += [post(kind, "stand", r[seen[:h]], e[:h], b, dt[seen[:h]])[0],
                    post(kind, "stand", r[seen[h:]], e[h:], b, dt[seen[h:]])[0]]
        rows.append(out)
    for wk in walks:
        wk = np.array(wk)
        if dt[wk].sum() < 3.0:
            continue
        out = [s, t[wk[0]], float(dt[wk].sum()), float(np.median(r[wk]))]
        for kind, key in (("moving", "mg"), ("still", "sg")):
            g = GATES[kind]
            out += list(post(kind, "walk", r[wk], x[key][wk][:, g], BG[s][0 if kind == "moving" else 1][g], dt[wk]))
        wrows.append(out)


def shape(lg, sd):
    """beta of Gamma(beta, beta) with var log g = trigamma(beta), the estimation noise taken out."""
    v = np.var(lg) - np.mean(sd ** 2)
    if v <= 0:
        return np.inf, v
    lo, hi = 0.05, 1e4
    for _ in range(100):
        mid = np.sqrt(lo * hi)
        if polygamma(1, mid) > v:
            lo = mid
        else:
            hi = mid
    return mid, v


import time  # noqa: E402

print(f"stays >= {MIN:.0f} s seen: {len(rows)}")
print(" sensor          start    span  seen   r    finds lost | moving: log g  sd  Smax  1st 2nd | still: log g  sd  Smax  1st 2nd")
for o in rows:
    print(f" {o[0]:14s} {time.strftime('%H:%M:%S', time.localtime(o[1]))} {o[2]:5.0f} {o[3]:5.0f} {o[4]:4.1f} {o[5]:5d} {o[6]:5.0f} |"
          f" {o[7]:+6.2f} {o[8]:4.2f} {o[9]:5.1f} {o[10]:+5.2f} {o[11]:+5.2f} | {o[12]:+6.2f} {o[13]:4.2f} {o[14]:5.1f} {o[15]:+5.2f} {o[16]:+5.2f}")
A = np.array([o[2:] for o in rows], dtype=float)
sens = np.array([o[0] for o in rows])
# only stays whose profile is well above the background: the gain is identified there
for name, c0, smin in (("moving", 5, 3.0), ("still", 10, 3.0)):
    k = A[:, c0 + 2] > smin
    lg, sd = A[k, c0], A[k, c0 + 1]
    beta, v = shape(lg, sd)
    print(f"\n{name}: {k.sum()} stays with S_max > {smin}: mean log g {lg.mean():+.2f}, sd {lg.std():.2f}, "
          f"noise sd {np.sqrt(np.mean(sd ** 2)):.2f} -> true sd {np.sqrt(max(v, 0)):.2f}, Gamma shape {beta:.2f}")
    q = np.exp(np.quantile(lg, [0.1, 0.5, 0.9]))
    print(f"   g quantiles 10/50/90 %: {q.round(2)}")
    print(f"   halves: Spearman {spearmanr(A[k, c0 + 3], A[k, c0 + 4])[0]:+.2f} (persistence within a stay)")
    for s in sorted(set(sens[k])):
        j = sens[k] == s
        print(f"   {s:14s} n {j.sum():3d} mean log g {lg[j].mean():+.2f} sd {lg[j].std():.2f}")
    # LD2450 detectability: finds per s lost (kappa ~ re-acquisition rate)
    kk = k & (A[:, 4] >= 10)
    rate = np.log((A[kk, 3] + 0.5) / (A[kk, 4] + 1.0))
    print(f"   LD2450 re-acquisition rate (finds per s lost) vs log g, {kk.sum()} stays with >= 10 s lost: "
          f"Spearman {spearmanr(rate, A[kk, c0])[0]:+.2f}; within sensor and 1 m of r: "
          "%+.2f (n %d)" % within(rate, A[kk, c0], A[kk], sens[kk]))
k = (A[:, 7] > 3) & (A[:, 12] > 3)
print(f"\nmoving vs still log g per stay ({k.sum()}): Spearman {spearmanr(A[k, 5], A[k, 10])[0]:+.2f}, "
      f"Pearson {np.corrcoef(A[k, 5], A[k, 10])[0, 1]:+.2f}")

W = np.array([o[2:] for o in wrows], dtype=float)
print(f"\nwalks >= 3 s: {len(W)}")
for name, c0 in (("moving", 2), ("still", 5)):
    k = W[:, c0 + 2] > 3
    beta, v = shape(W[k, c0], W[k, c0 + 1])
    print(f"  {name}: n {k.sum()} mean log g {W[k, c0].mean():+.2f} sd {W[k, c0].std():.2f} noise "
          f"{np.sqrt(np.mean(W[k, c0 + 1] ** 2)):.2f} -> true sd {np.sqrt(max(v, 0)):.2f}, Gamma shape {beta:.2f}")
k = (W[:, 4] > 3) & (W[:, 7] > 3)
print(f"  moving vs still per walk: Spearman {spearmanr(W[k, 2], W[k, 5])[0]:+.2f}")
