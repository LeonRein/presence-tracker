"""Shape of the background distribution of the gate energies (no LD2450 target for +-W s):
mean, standard deviation, coefficient of variation, histogram; compared with exponential (CV 1),
Rayleigh (CV 0.52) and a dB scale (energy ~ log of an exponential power: sd 5.6 dB)."""
import sys

import numpy as np

from background import clear_masks
from geom import weights
from load import load

D = load()
if __name__ == "__main__":
    for s, x in D.items():
        w = weights(x["t"])
        none_all, ring = clear_masks(x, s)
        m = none_all & (w > 0)
        if w[m].sum() < 600:
            continue
        print(f"\n{s} ({w[m].sum() / 60:.0f} min)")
        for kind, E in (("moving", x["mg"]), ("still", x["sg"])):
            for g in range(9):
                e = E[m, g]
                ww = w[m]
                if e.max() == 0:
                    continue
                mu = np.average(e, weights=ww)
                sd = np.sqrt(np.average((e - mu) ** 2, weights=ww))
                h = np.bincount(e.astype(int), weights=ww, minlength=101)[:30] / ww.sum()
                print(f"  {kind:6s} g{g} mean {mu:5.1f} sd {sd:4.1f} CV {sd / mu:4.2f}  hist 0..29:",
                      " ".join(f"{int(round(100 * v)):2d}" for v in h))
