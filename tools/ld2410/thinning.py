"""What does a frame stand for, when the firmware sends only a heartbeat every 5 s while it has
nothing to report (no LD2450 target, both LD2410C flags off; esphome/packages/radar-combo.yaml)?

1. Are the flags thresholds on the energies (could the silence be "every gate below its threshold",
   a censored observation)? P(gate >= threshold | flag off) per gate.
2. The thinning simulated on full-rate stretches (frames flow because the LD2450 has a target, the
   LD2410C's flags mostly off): the time-weighted mean energy per cell of the full stream against
   what the frames the firmware would have sent give with
     back   each frame for the time since the previous one (the filter until 2eea738),
     fwd    each frame until the next one,
     split  each frame for one frame, the silence before it for the frame before it (the filter now),
     cens   each frame for one frame, the silence "every gate below its threshold" (Gamma ML with
            censoring, the shapes of MODEL.md 4.3).
3. The background (no LD2450 target in this housing +-30 s) with back and split, all and the night.

usage: thinning.py [GLOB] [NIGHT_FROM NIGHT_TO]   (default: all recordings, 2026-10-07 00:00-06:00)"""
import sys
import time

import numpy as np
from scipy import optimize, special

from basics import thresholds
from geom import since_last, until_next
from load import load

REC = sys.argv[1] if len(sys.argv) > 1 else "/home/leon/checkout/presence-tracker/recordings/*.jsonl"
NIGHT = [time.mktime(time.strptime(v, "%Y-%m-%d %H:%M")) for v in
         (sys.argv[2:4] if len(sys.argv) > 3 else ("2026-10-07 00:00", "2026-10-07 06:00"))]
IDLE, HEARTBEAT, FRAME, W = 6.0, 5.0, 0.089, 30.0
ALPHA = np.r_[np.full(9, 2.5), np.full(7, 2.0)]


def stretches(t, maxdt=0.2, minlen=30.0):
    br = np.nonzero(np.diff(t) > maxdt)[0]
    return [(a, b) for a, b in zip(np.r_[0, br + 1], np.r_[br + 1, len(t)]) if t[b - 1] - t[a] >= minlen]


def thin(t, empty):
    """Which frames the firmware sends: all that are not empty, the first empty one, then a heartbeat."""
    sent = np.zeros(len(t), bool)
    last, last_empty = -1e9, True
    for k in range(len(t)):
        if empty[k] and last_empty and t[k] - last < HEARTBEAT:
            continue
        sent[k], last, last_empty = True, t[k], empty[k]
    return sent


def split_weights(back):
    """Each frame one frame, the rest of the gap before it to the frame before it."""
    frame = np.minimum(back, FRAME)
    return frame + np.r_[(back - frame)[1:], 0.0]


def cens_mle(w, e, w_cens, th, a):
    e = np.maximum(e, 0.5)

    def nll(lm):
        mu = np.exp(lm)
        ll = (w * (-a * lm - a * e / mu)).sum()
        if w_cens > 0:
            ll += w_cens * np.log(max(special.gammainc(a, a * th / mu), 1e-300))
        return -ll
    return np.exp(optimize.minimize_scalar(nll, bounds=(np.log(0.3), np.log(200)), method="bounded").x)


def fmt(v):
    return "moving " + " ".join(f"{x:5.1f}" for x in v[:9]) + " | still " + " ".join(f"{x:5.1f}" for x in v[9:])


D = load(REC)
for s, x in D.items():
    t_all = x["t"]
    E_all = np.c_[x["mg"], x["sg"][:, 2:]]
    mt, st, mx = thresholds(s)
    th = np.r_[mt, st[2:]]
    inside = np.r_[np.arange(9) <= mx, np.arange(2, 9) <= mx]
    print(f"\n{s}: {len(t_all)} frames, {(t_all[-1] - t_all[0]) / 3600:.1f} h, frame period at full rate "
          f"{np.median(np.diff(t_all)[np.diff(t_all) < 0.3]):.4f} s")
    # 1. flags and thresholds
    am = x["mg"][:, :mx + 1] >= mt[:mx + 1]
    a_s = x["sg"][:, 2:mx + 1] >= st[2:mx + 1]
    print("  P(gate >= threshold | flag off): moving " + " ".join(f"{v:.3f}" for v in am[~x["moving"]].mean(0))
          + f" (any {am[~x['moving']].any(1).mean():.3f}); still " + " ".join(f"{v:.3f}" for v in a_s[~x["still"]].mean(0))
          + f" (any {a_s[~x['still']].any(1).mean():.3f})")
    print(f"  P(flag on | no gate >= threshold): moving {x['moving'][~am.any(1)].mean():.3f} still {x['still'][~a_s.any(1)].mean():.3f}")
    # 2. thinning simulated
    acc = {k: [] for k in ("truth", "back", "fwd", "split")}
    wc, cw, ce, T, n, N, ns = 0.0, [], [], 0.0, 0, 0, 0
    for a, b in stretches(t_all):
        t, E = t_all[a:b], E_all[a:b]
        empty = ~x["moving"][a:b] & ~x["still"][a:b]
        if empty.mean() < 0.5:
            continue
        sent = thin(t, empty)
        dt = np.diff(t, prepend=t[0] - FRAME)
        ts, Es = t[sent], E[sent]
        back = np.diff(ts, prepend=ts[0] - FRAME)
        frame = np.minimum(back, dt[sent])
        acc["truth"].append((dt, E))
        acc["back"].append((back, Es))
        acc["fwd"].append((np.diff(ts, append=t[-1] + FRAME), Es))
        acc["split"].append((split_weights(back), Es))
        cw.append(frame), ce.append(Es)
        wc += (back - frame).sum()
        T, n, N, ns = T + t[-1] - t[0], n + 1, N + len(t), ns + sent.sum()
    if n:
        out = {}
        for k, v in acc.items():
            w, E = np.concatenate([q[0] for q in v]), np.concatenate([q[1] for q in v])
            out[k] = (w[:, None] * E).sum(0) / w.sum()
        w, E = np.concatenate(cw), np.concatenate(ce)
        out["cens"] = np.array([cens_mle(w, E[:, j], wc if inside[j] else 0.0, th[j], ALPHA[j]) for j in range(16)])
        print(f"  thinning simulated on {n} quiet full-rate stretches ({T / 60:.0f} min, {ns / N:.2f} of the frames sent):")
        for k in out:
            r = out[k] / out["truth"]
            print(f"    {k:5s} {fmt(out[k])}" + ("" if k == "truth" else
                  f"   /truth moving {np.exp(np.log(r[:9]).mean()):.3f} still {np.exp(np.log(r[9:]).mean()):.3f}"))
    # 3. background, old and new weighting
    t = t_all
    back = np.diff(t, prepend=-1e9)
    back = np.where(back <= IDLE, back, 0.0)
    split = split_weights(back)
    anyt = (x["tg"][..., 3] > 0).any(axis=1)
    clear = (since_last(t, anyt) > W) & (until_next(t, anyt) > W)
    night = (t >= NIGHT[0]) & (t < NIGHT[1])
    for name, m in (("clear", clear), ("night", night)):
        mb = (back[m, None] * E_all[m]).sum(0) / back[m].sum()
        ms = (split[m, None] * E_all[m]).sum(0) / split[m].sum()
        print(f"  {name} ({back[m].sum() / 3600:.1f} h) back  {fmt(mb)}\n  {'':{len(name) + 9}s}split {fmt(ms)}"
              f"   back/split: moving {mb[:9].sum() / ms[:9].sum():.3f} still {mb[9:].sum() / ms[9:].sum():.3f} all {mb.sum() / ms.sum():.3f}")
