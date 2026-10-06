"""Time correlation of the gate energies at a constant situation, and whether their spread grows
with their level (linear power: sd ~ mean; a dB scale: sd constant).

ACF: runs of continuous frames (gaps < 0.3 s) of at least 60 s; per run and gate the mean is
removed (a constant situation per run is assumed, so slow changes inflate long lags). Two
situations: (a) background: no LD2450 target within +-1 gate of this gate during the run;
(b) one standing person (Arbeitszimmer 22:20-22:50, Leon at the desk, ~1.5 m slant).
The integrated time (tau_int = dt (1 + 2 sum rho_k), Sokal) says how far apart two frames carry
independent information."""
import time

import numpy as np

from geom import GATE, scales, targets
from load import load

D = load()
SC = scales()
LAGS = [1, 2, 3, 5, 10, 20, 30, 50, 100]
DT = 0.089


def runs(t, ok, min_len=60.0):
    out = []
    gap = np.diff(t) > 0.3
    start = 0
    for i in range(1, len(t) + 1):
        if i == len(t) or gap[i - 1] or not ok[i]:
            if i - start > 1 and t[i - 1] - t[start] >= min_len and ok[start:i].all():
                out.append((start, i))
            start = i + 1 if i < len(t) and not ok[i] else i
    return out


def acf(series_list, maxlag=150):
    num = np.zeros(maxlag + 1)
    den = np.zeros(maxlag + 1)
    for y in series_list:
        y = y - y.mean()
        v = np.dot(y, y) / len(y)
        if v <= 1e-9:
            continue
        for k in range(min(maxlag, len(y) - 1) + 1):
            num[k] += np.dot(y[:len(y) - k], y[k:]) / len(y)
            den[k] += v
    rho = num / np.maximum(den, 1e-12)
    return rho


def tau_int(rho):
    # Sokal's window: stop at the first lag where rho < 0.05
    k = np.argmax(rho < 0.05) if (rho < 0.05).any() else len(rho)
    return DT * (1 + 2 * rho[1:k].sum()), k


if __name__ == "__main__":
    print("(a) background (no LD2450 target within +-1 gate), runs >= 60 s")
    for kind in ("mg", "sg"):
        for g in (0, 1, 2, 4, 6, 8):
            if kind == "sg" and g < 2:
                continue
            ys = []
            for s, x in D.items():
                r, ang, valid, walk = targets(x, SC[s])
                gate = np.floor(r / GATE).astype(int)
                ok = ~(valid & (np.abs(gate - g) <= 1)).any(axis=1)
                for a, b in runs(x["t"], ok):
                    ys.append(x[kind][a:b, g])
            if not ys:
                continue
            rho = acf(ys)
            ti, k = tau_int(rho)
            print(f"  {kind} g{g}: {sum(len(y) for y in ys) * DT / 60:5.0f} min  rho at lags {LAGS}: "
                  + " ".join(f"{rho[l]:.2f}" for l in LAGS) + f"   tau_int {ti:.2f} s")
    print("(b) Arbeitszimmer 22:20-22:50, one standing person at ~1.5 m")
    x = D["arbeitszimmer"]
    t0, t1 = (time.mktime(time.strptime("2026-10-06 " + v, "%Y-%m-%d %H:%M")) for v in ("22:20", "22:50"))
    ok = (x["t"] >= t0) & (x["t"] < t1)
    rr = runs(x["t"], ok)
    for kind in ("mg", "sg"):
        for g in range(9):
            if kind == "sg" and g < 2:
                continue
            ys = [x[kind][a:b, g] for a, b in rr]
            rho = acf(ys)
            ti, k = tau_int(rho)
            print(f"  {kind} g{g}: rho at lags {LAGS}: " + " ".join(f"{rho[l]:.2f}" for l in LAGS) + f"   tau_int {ti:.2f} s")
    print("(c) spread vs level (moving energies, all frames): residual sd about the +-0.5 s running mean, binned by that mean")
    bins = [0, 5, 8, 12, 18, 25, 35, 50, 70, 90]
    acc = {b: [] for b in bins}
    for s, x in D.items():
        cont = np.r_[np.diff(x["t"]) < 0.3, False]
        for g in range(2, 9):
            e = x["mg"][:, g]
            k = np.ones(11) / 11
            mean = np.convolve(e, k, mode="same")
            okc = np.convolve(cont.astype(float), k, mode="same") > 0.999
            res = (e - mean) * np.sqrt(11 / 10)
            sel = okc & (e < 100) & (mean < 95)
            idx = np.digitize(mean[sel], bins) - 1
            for b, v in zip(idx, res[sel]):
                acc[bins[b]].append(v)
    for b in bins:
        v = np.array(acc[b])
        if len(v) > 200:
            print(f"  mean in [{b},..): n={len(v):7d}  sd {v.std():5.1f}")
