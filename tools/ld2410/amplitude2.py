"""The amplitude of one person per stay, again (MODEL.md 4.3, 10), with what the filter counts as a
measurement only: amplitude.py took the LD2450 target of the housing as the person also while the
LD2450 held it (frozen: bit-identical coordinates; coasting: >= 3 frames with the same speed) and
also behind walls (Detection.hidden: its echoes, e.g. the kitchen's beyond the outer wall). A held
target is often nobody any more (the LD2450 keeps a frozen target 35 s after the person left), and
those stays had the lowest gains. Here a stay is built from measured, visible frames only.

Per stay and kind the gain g on the average profile (as amplitude.py), the median slant r and angle
off the axis; then where the misfit lies: per sensor, over the distance, over the angle (a
regression log g ~ sensor + log r + angle, stays weighted by 1 / (sd^2 + spread^2)).

usage: uv run --project ../../tracker --with scipy python amplitude2.py [min_stay_s] [pattern...]"""
import os
import sys

import numpy as np
from scipy.special import polygamma
from scipy.stats import spearmanr

from background import clear_masks
from geom import weights
from load import REC, load
from stays import GATES, frame_info, post

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
PATTERNS = sys.argv[2:] or [REC]
AMAX = float(os.environ.get("AMAX", 65))


def stays_of(x, s):
    t = x["t"]
    ok, held, nvalid, r, ang, walk, pos = frame_info(x, s)
    dt = weights(t, cap=1.0)
    out, cur, start, last_seen, walk_since = [], None, None, None, None
    for j in range(len(t)):
        if ok[j] and walk[j]:
            walk_since = t[j] if walk_since is None else walk_since
        elif ok[j]:
            walk_since = None
        far = cur is not None and ok[j] and np.hypot(*(pos[j] - start)) > 0.7
        gone = cur is not None and t[j] - last_seen > 30
        walked = cur is not None and walk_since is not None and t[j] - walk_since > 1.0
        other = nvalid[j] >= 2
        if cur is not None and (far or gone or walked or other):
            out.append(cur)
            cur = None
        if ok[j] and not walk[j] and ang[j] < AMAX and r[j] < 7.5:
            if cur is None:
                cur, start = [], pos[j].copy()
            cur.append(j)
            last_seen = t[j]
    if cur:
        out.append(cur)
    return t, r, ang, dt, out, held


rows = []
for pat in PATTERNS:
    D = load(pat)
    for s, x in D.items():
        none_all, _ = clear_masks(x, s)
        w = weights(x["t"])
        m = none_all & (w > 0)
        if w[m].sum() < 300:
            continue
        bg = (np.average(x["mg"][m], axis=0, weights=w[m]), np.average(x["sg"][m], axis=0, weights=w[m]))
        t, r, ang, dt, stays, held = stays_of(x, s)
        for st in stays:
            st = np.array(st)
            if dt[st].sum() < MIN:
                continue
            span = t[st[-1]] - t[st[0]]
            out = [s, t[st[0]], span, float(dt[st].sum()), float(np.median(r[st])), float(np.median(ang[st]))]
            for kind, key in (("moving", "mg"), ("still", "sg")):
                g = GATES[kind]
                out += list(post(kind, "stand", r[st], x[key][st][:, g], bg[0 if kind == "moving" else 1][g], dt[st]))
            # the LD2450's detectability in the stay (4.1): how often it found its held target again
            # (a held run that ends in a measured frame), per s held
            span_idx = np.arange(st[0], st[-1] + 1)
            h = held[span_idx]
            out += [int(np.sum(h[:-1] & ~h[1:])), float(dt[span_idx][h].sum())]
            rows.append(out)

import time  # noqa: E402

print(f"stays >= {MIN:.0f} s measured (not held, not hidden), angle < {AMAX:.0f}: {len(rows)}")
print(" sensor          start     span  seen   r   angle | moving: log g  sd  Smax | still: log g  sd  Smax")
for o in sorted(rows, key=lambda o: (o[0], o[1])):
    print(f" {o[0]:14s} {time.strftime('%d. %H:%M:%S', time.localtime(o[1]))} {o[2]:5.0f} {o[3]:5.0f} {o[4]:4.1f} {o[5]:5.0f} |"
          f" {o[6]:+6.2f} {o[7]:4.2f} {o[8]:5.1f} | {o[9]:+6.2f} {o[10]:4.2f} {o[11]:5.1f}")
A = np.array([o[2:] for o in rows], dtype=float)
sens = np.array([o[0] for o in rows])
names = sorted(set(sens))


def shape(v):
    if v <= 0:
        return np.inf
    lo, hi = 0.05, 1e4
    for _ in range(100):
        mid = np.sqrt(lo * hi)
        if polygamma(1, mid) > v:
            lo = mid
        else:
            hi = mid
    return mid


def regress(lg, sd, X, tau2):
    """Weighted least squares, weights 1 / (sd^2 + tau2); tau2 the spread between stays (iterated)."""
    for _ in range(50):
        wt = 1 / (sd ** 2 + tau2)
        beta = np.linalg.solve(X.T @ (wt[:, None] * X), X.T @ (wt * lg))
        res = lg - X @ beta
        tau2 = max(float(np.sum(wt * (res ** 2 - sd ** 2)) / np.sum(wt)), 1e-4)
    cov = np.linalg.inv(X.T @ ((1 / (sd ** 2 + tau2))[:, None] * X))
    return beta, np.sqrt(np.diag(cov)), tau2, res


for name, c0 in (("moving", 4), ("still", 7)):
    k = A[:, c0 + 2] > 3.0
    lg, sd, r, ang, sn = A[k, c0], A[k, c0 + 1], A[k, 2], A[k, 3], sens[k]
    print(f"\n{name}: {k.sum()} stays with S_max > 3")
    for s in names:
        j = sn == s
        if j.sum():
            print(f"   {s:14s} n {j.sum():3d} mean log g {lg[j].mean():+.2f} sd {lg[j].std():.2f}  r {np.median(r[j]):.1f}  angle {np.median(ang[j]):.0f}")
    for lo, hi in ((0, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 4.5), (4.5, 8)):
        j = (r >= lo) & (r < hi)
        if j.sum():
            print(f"   r {lo:.1f}-{hi:.1f}: n {j.sum():3d} mean log g {lg[j].mean():+.2f}")
    for lo, hi in ((0, 15), (15, 30), (30, 45), (45, 55), (55, 90)):
        j = (ang >= lo) & (ang < hi)
        if j.sum():
            print(f"   angle {lo:2d}-{hi:2d}: n {j.sum():3d} mean log g {lg[j].mean():+.2f}")
    present = [s for s in names if (sn == s).any()]
    S = np.stack([(sn == s).astype(float) for s in present], axis=1)
    for label, X in (("sensor", S), ("sensor + log r", np.hstack([S, np.log(r)[:, None]])),
                     ("sensor + angle/10", np.hstack([S, ang[:, None] / 10])),
                     ("sensor + log r + angle/10", np.hstack([S, np.log(r)[:, None], ang[:, None] / 10])),
                     ("none", np.ones((len(lg), 1)))):
        beta, se, tau2, res = regress(lg, sd, X, 0.3)
        extra = " ".join(f"{b:+.2f}±{e:.2f}" for b, e in zip(beta[len(present):], se[len(present):])) if label != "none" else f"{beta[0]:+.2f}"
        per = " ".join(f"{s[:4]} {b:+.2f}" for s, b in zip(present, beta)) if label != "none" else ""
        print(f"   {label:26s} spread sd {np.sqrt(tau2):.2f} (Gamma shape {shape(tau2):.1f}) | {per} | {extra}")
k = (A[:, 6] > 3) & (A[:, 9] > 3)
print(f"\nmoving vs still log g per stay ({k.sum()}): Pearson {np.corrcoef(A[k, 4], A[k, 7])[0, 1]:+.2f}")

# the LD2450's detectability (re-acquisitions per s held) against log g, within sensor and 1 m of r
for name, c0 in (("moving", 4), ("still", 7)):
    k = (A[:, c0 + 2] > 3) & (A[:, 11] >= 10)
    rate = np.log((A[k, 10] + 0.5) / (A[k, 11] + 1.0))
    xs, ys = [], []
    for s in names:
        for r0 in np.arange(0.5, 8, 1.0):
            j = (sens[k] == s) & (A[k, 2] >= r0) & (A[k, 2] < r0 + 1)
            if j.sum() >= 3:
                xs += list(rate[j] - rate[j].mean())
                ys += list(A[k, c0][j] - A[k, c0][j].mean())
    print(f"{name}: LD2450 re-acquisition rate vs log g, {k.sum()} stays with >= 10 s held: Spearman "
          f"{spearmanr(rate, A[k, c0])[0]:+.2f}; within sensor and 1 m: "
          f"{spearmanr(xs, ys)[0] if len(xs) > 3 else float('nan'):+.2f} (n {len(xs)})")
