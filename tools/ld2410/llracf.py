"""How many independent looks do the energies give? The integrated autocorrelation time of the
per-frame log-likelihood-ratio series (the composite likelihood's adjustment: the score's variance
over its expected curvature, which for a time series is ~ tau_int / dt), Arbeitszimmer 22:20-22:50:
(1) Leon alone vs nobody, (2) Leon + a second person at 2.5 m / 4.5 m vs Leon alone. Moving and still
separately. Large tau_int: the evidence persists (situation / model misfit), not noise."""
import numpy as np

from acf import acf, runs
from check import BG, C, P, loglik
from check5 import window
from fit import mean

x, m, rl, w = window("arbeitszimmer", "22:20", "22:50")
idx = np.nonzero(m)[0]
rr = runs(x["t"][idx], np.ones(len(idx), bool), min_len=120)
for kind, key, gs in (("moving", "mg", slice(0, 9)), ("still", "sg", slice(2, 9))):
    p = P[(kind, "stand")]
    a = np.exp(p[6])
    b = BG["arbeitszimmer"][0 if kind == "moving" else 1][gs]
    e = x[key][m][:, gs]
    S1 = mean(p, rl, C[gs], 0 * b)
    l1 = loglik(e, b + S1, a).sum(1)
    series = {"alone vs nobody": l1 - loglik(e, b + 0 * S1, a).sum(1)}
    for r2 in (2.5, 4.5):
        series[f"+{r2} m vs alone"] = loglik(e, b + S1 + mean(p, np.full(len(rl), r2), C[gs], 0 * b), a).sum(1) - l1
    for name, d in series.items():
        rho = acf([d[s:e_] for s, e_ in rr], maxlag=3000)
        k = np.argmax(rho < 0.05) if (rho < 0.05).any() else len(rho)
        tau = 0.089 * (1 + 2 * rho[1:k].sum())
        print(f"{kind:6s} {name:18s} mean/frame {d.mean():+7.3f} sd {d.std():6.2f}  rho(1 s) {rho[11]:.2f} rho(10 s) {rho[112]:.2f}"
              f" rho(60 s) {rho[674]:.2f}  tau_int {tau:6.1f} s")
