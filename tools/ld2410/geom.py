"""LD2450 targets of the same housing as reference for the LD2410C gates: slant distance (with the
sensor's calibrated scale), angle off the axis, gate, walking or standing; time weights that undo
the firmware's thinning of empty frames (one heartbeat per 5 s when nothing is there)."""
import json
import os

import numpy as np

CONFIG = os.path.expanduser("~/.config/presence-tracker/dev/config5.json")
GATE = 0.75


def scales():
    c = json.load(open(CONFIG))
    return {s["id"].replace("presence-", ""): s.get("scale", 1.0) for s in c["sensors"]}


def targets(x, scale):
    """(r, angle_deg, valid, walking) each (n, 3)."""
    tg = x["tg"]
    r = scale * np.hypot(tg[..., 0], tg[..., 1])
    ang = np.degrees(np.arctan2(tg[..., 0], np.maximum(tg[..., 1], 1e-6)))
    valid = tg[..., 3] > 0
    walking = valid & (np.abs(tg[..., 2]) > 0.05)
    return r, np.abs(ang), valid, walking


def weights(t, cap=5.5):
    """Time each frame stands for: until the next frame, at most cap (longer: lost data)."""
    dt = np.diff(t, append=t[-1])
    return np.where(dt <= cap, dt, 0.0)


def since_last(t, event, horizon=1e9):
    """Seconds since the last frame where event was true (inf if never)."""
    last = np.where(event, t, -np.inf)
    last = np.maximum.accumulate(last)
    return t - last


def until_next(t, event):
    nxt = np.where(event, t, np.inf)
    nxt = np.minimum.accumulate(nxt[::-1])[::-1]
    return nxt - t


def wquantile(v, w, qs):
    o = np.argsort(v)
    v, w = v[o], w[o]
    c = np.cumsum(w)
    if c[-1] <= 0:
        return np.full(len(qs), np.nan)
    c /= c[-1]
    return np.array([v[min(np.searchsorted(c, q), len(v) - 1)] for q in qs])
