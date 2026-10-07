"""What amplitude2.py and shape2.py share: the profile fitted by fit.py, the posterior of a gain on it,
and per frame whether the LD2450 of the housing measures exactly one person the way the filter counts
it (not held, not behind a wall)."""
import json
import os
import sys

import numpy as np
from scipy.special import gammaincc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tracker"))
from presence_tracker.model import Config  # noqa: E402

from geom import CONFIG  # noqa: E402

CFG = Config.from_dict(json.load(open(CONFIG)))
P = {(k, m): np.load(os.path.expanduser(f"~/.cache/presence-tracker/fit_{k}_{m}.npy"))
     for k in ("moving", "still") for m in ("stand", "walk")}
ALPHA = {"moving": 2.5, "still": 2.0}
TAU = {"moving": 4.0, "still": 13.0}
GATES = {"moving": slice(0, 9), "still": slice(2, 9)}
C = (np.arange(9) + 0.5) * 0.75
LG = np.linspace(-4, 4, 321)


def mean(p, r, c):
    """The profile (fit.py) of a person at slant r (k,) in the gates with centres c, (k, len(c))."""
    logA, n, delta, logs, ltau, logl = p[:6]
    tau = 1 / (1 + np.exp(-ltau))
    Dl = c[None, :] - r[:, None] - delta
    main = np.exp(-0.5 * (Dl / np.exp(logs)) ** 2)
    k = np.where(Dl <= 0, main, (1 - tau) * main + tau * np.exp(-np.maximum(Dl, 0) / np.exp(logl)))
    return np.exp(logA) * r[:, None] ** (-n) * k


def post(kind, mode, r, e, b, dt):
    """(mean, sd) of log g on the profile for the frames (r (k,), e (k, cells), b (cells,), dt (k,)),
    flat prior, frames tempered by dt / tau, 100 censored; and the profile's maximum."""
    S = mean(P[(kind, mode)], r, C[GATES[kind]])
    a = ALPHA[kind]
    sat = e >= 99.5
    ee = np.maximum(e, 0.5)
    w = (dt / TAU[kind])[:, None]
    ll = np.zeros(len(LG))
    for i, lg in enumerate(LG):
        mu = np.maximum(b[None, :] + np.exp(lg) * S, 0.3)
        lk = a * np.log(a / mu) - a * ee / mu
        ls = np.log(np.maximum(gammaincc(a, a * 99.5 / mu), 1e-300))
        ll[i] = np.sum(w * np.where(sat, ls, lk))
    p = np.exp(ll - ll.max())
    p /= p.sum()
    m = float(p @ LG)
    return m, float(np.sqrt(p @ (LG - m) ** 2)), float(S.max())


def frame_info(x, s):
    """Per frame: exactly one target that is a measurement (not held) and visible (ok); held; the
    number of targets; its slant r, angle, walking; world position."""
    sc = next(c for c in CFG.sensors if c.id == "presence-" + s)
    tg = x["tg"]
    t = x["t"]
    n = len(t)
    valid = tg[..., 3] > 0
    nvalid = valid.sum(axis=1)
    i = np.argmax(valid, axis=1)
    k = np.arange(n)
    xy = tg[k, i, :2]
    sp = tg[k, i, 2]
    # held: the same coordinates as the previous frame's (frozen) or >= 3 frames with the same nonzero
    # speed (coasting), as sensortracks.py
    same = np.concatenate([[False], np.all(xy[1:] == xy[:-1], axis=1)])
    run = np.ones(n, int)
    for j in range(1, n):
        if sp[j] != 0 and sp[j] == sp[j - 1]:
            run[j] = run[j - 1] + 1
    held = same | (run >= 3)
    r = sc.scale * np.hypot(xy[:, 0], xy[:, 1])
    ang = np.abs(np.degrees(np.arctan2(xy[:, 0], np.maximum(xy[:, 1], 1e-6))))
    hidden = np.zeros(n, bool)
    pos = np.zeros((n, 2))
    for j in np.nonzero(nvalid == 1)[0]:
        wx, wy, ground, _ = sc.to_world(xy[j, 0], xy[j, 1], CFG.params.target_height)
        u = np.array([wx - sc.x, wy - sc.y]) / max(ground, 1e-3)
        pos[j] = (wx, wy)
        hidden[j] = CFG.hidden(sc, (wx, wy), u, CFG.params.wall_margin)
    ok = (nvalid == 1) & ~held & ~hidden
    return ok, held, nvalid, r, ang, np.abs(sp) > 0.05, pos
