"""Do the gates of a sensor move together without anybody (a common gain: broadband interference,
the blips of the moving flag)? Background frames (no LD2450 target in the sensor for +-30 s), 1-s
blocks: per block and cell the mean energy over the background mean; the mean pairwise correlation
between cells, and the variance of a common log-gain (between-cell covariance)."""
import numpy as np

from background import clear_masks
from load import load

D = load()
for s, x in D.items():
    none_all, _ = clear_masks(x, s)
    t = x["t"][none_all]
    if len(t) < 600:
        continue
    blk = np.floor(t - t[0]).astype(int)
    for kind, E, cells in (("moving", x["mg"][none_all], slice(0, 9)), ("still", x["sg"][none_all], slice(2, 9))):
        E = E[:, cells]
        n = np.bincount(blk)
        ok = n >= 5
        means = np.stack([np.bincount(blk, weights=E[:, j]) for j in range(E.shape[1])], axis=1)[ok] / n[ok, None]
        r = means / means.mean(axis=0)
        lr = np.log(np.maximum(r, 1e-3))
        C = np.corrcoef(lr.T)
        off = C[~np.eye(len(C), dtype=bool)]
        cov = np.cov(lr.T)
        common = cov[~np.eye(len(cov), dtype=bool)].mean()
        # bursts: blocks in which most cells are 2x their mean
        burst = np.mean((r > 2).mean(axis=1) > 0.6)
        g = np.exp(lr.mean(axis=1))
        edges = [0, 0.75, 1.25, 2.0, 3.2, 100]
        h = np.histogram(g, edges)[0] / len(g)
        print(f"{s:14s} {kind:6s} common gain per block in {edges}: " + " ".join(f"{v:.3f}" for v in h))
        print(f"{s:14s} {kind:6s} blocks {ok.sum():5d}: mean corr between cells {off.mean():+.2f}, "
              f"common log-gain var {common:.3f} (sd {np.sqrt(max(common, 0)):.2f}), per-cell log var {np.diag(cov).mean():.3f}, "
              f"bursts (>60 % of cells 2x) {burst:.3f}")
