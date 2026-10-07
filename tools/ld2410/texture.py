"""The background's texture (compound-Gaussian / K-distributed clutter, Ward 1981; Ward, Tough & Watts
2006): energy = texture x speckle, the speckle fast (Gamma, shape alpha), the texture a slowly varying
Gamma level per gate. Measured on an empty night (everybody in bed; seconds with any LD2450 target
in any sensor within +-60 s left out).

Per sensor, kind and gate, on 1-s blocks (the filter's evaluation unit):
  - the block mean over the gate's long mean (the night's median per gate),
  - the spread of its log after taking out the speckle's share (the variance of the mean of n
    Gamma(alpha) frames is 1/(n alpha); with correlated frames less independent ones - estimated
    from the within-block variance),
  - the autocorrelation of the log level at lags 1-300 s (the texture's correlation time),
  - the common part across gates (what the shared gain already covers).
Also lists the blocks in which one gate is far above its level (the events the texture must explain).

usage: texture.py FROM TO   (local times "YYYY-MM-DD HH:MM", e.g. "2026-10-06 22:56" "2026-10-07 06:00")"""
import sys
import time

import numpy as np

from load import load

REC = "/home/leon/checkout/presence-tracker/recordings/*.jsonl"
t0, t1 = (time.mktime(time.strptime(v, "%Y-%m-%d %H:%M")) for v in sys.argv[1:3])
D = load(REC)
LAGS = [1, 2, 5, 10, 20, 30, 60, 120, 300]

# seconds with a target anywhere (any sensor), widened by 60 s
busy = set()
for s, x in D.items():
    m = (x["t"] >= t0 - 60) & (x["t"] < t1 + 60) & (x["tg"][..., 3] > 0).any(axis=1)
    for t in np.floor(x["t"][m]).astype(int):
        busy.update(range(t - 60, t + 61))

for s, x in D.items():
    m = (x["t"] >= t0) & (x["t"] < t1)
    t = x["t"][m]
    if len(t) < 100:
        continue
    E = np.concatenate([x["mg"][m], x["sg"][m][:, 2:]], axis=1)
    sec = np.floor(t).astype(int)
    ok = np.array([q not in busy for q in sec])
    t, E, sec = t[ok], E[ok], sec[ok]
    # the firmware sends only a heartbeat every 5 s when nothing is above its thresholds: per
    # second at most a few frames; blocks are the seconds with frames
    u, inv, cnt = np.unique(sec, return_inverse=True, return_counts=True)
    means = np.stack([np.bincount(inv, weights=E[:, j]) / cnt for j in range(16)], axis=1)
    level = np.median(means, axis=0)
    lr = np.log(np.maximum(means, 0.5) / np.maximum(level, 0.5))
    print(f"\n{s}: {len(u)} seconds with frames ({len(u) / ((t1 - t0)):.2f} of the time), "
          f"median frames per such second {np.median(cnt):.0f}")
    common_m = lr[:, :9].mean(axis=1)
    common_s = lr[:, 9:].mean(axis=1)
    print("  gate        level  sd(log)  sd(log - common)  q99 ratio   ACF of log level at lags", LAGS, "s")
    for j in range(16):
        name = f"{'mv' if j < 9 else 'st'}{j if j < 9 else j - 7}"
        resid = lr[:, j] - (common_m if j < 9 else common_s)
        # autocorrelation on the seconds grid (missing seconds skipped)
        series = np.full(u[-1] - u[0] + 1, np.nan)
        series[u - u[0]] = lr[:, j]
        acf = []
        for L in LAGS:
            a, b = series[:-L], series[L:]
            k = ~np.isnan(a) & ~np.isnan(b)
            acf.append(np.corrcoef(a[k], b[k])[0, 1] if k.sum() > 30 else np.nan)
        print(f"  {name:5s} {level[j]:8.1f} {lr[:, j].std():7.2f} {resid.std():12.2f}      {np.exp(np.percentile(lr[:, j], 99)):6.1f}    "
              + " ".join(f"{v:5.2f}" for v in acf))
    # the events: seconds in which some gate is > 4x its level
    big = np.nonzero((np.exp(lr) > 4).any(axis=1))[0]
    if len(big):
        print(f"  seconds with a gate > 4x its level: {len(big)}; first ones:",
              ", ".join(time.strftime("%H:%M:%S", time.localtime(u[i])) for i in big[:8]))
