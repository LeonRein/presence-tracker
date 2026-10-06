"""LD2410C basics: update rate of the gate energies, saturation, still gates 0/1, and whether the
moving/still flags are thresholds on the gate energies (firmware thresholds read from Home
Assistant 6.10.2026; esszimmer has its own)."""
import numpy as np

from load import load

TH = {"*": (np.array([50, 50, 40, 30, 20, 15, 15, 15, 15]), np.array([0, 0, 40, 40, 30, 30, 20, 20, 20]), 8),
      "esszimmer": (np.array([50, 50, 40, 30, 20, 15, 15, 15, 15]), np.array([0, 0, 40, 20, 18, 35, 35, 35, 35]), 4)}


def thresholds(s):
    return TH.get(s, TH["*"])


if __name__ == "__main__":
    D = load()
    for s, x in D.items():
        mg, sg = x["mg"], x["sg"]
        same_m = np.all(mg[1:] == mg[:-1], axis=1)
        same_s = np.all(sg[1:] == sg[:-1], axis=1)
        print(s, f"identical consecutive: moving {same_m.mean():.3f} still {same_s.mean():.3f}",
              "still g0/g1 max", sg[:, :2].max(axis=0))
        print("   frac moving ==100", (mg == 100).mean(axis=0).round(3))
        print("   frac still  ==100", (sg == 100).mean(axis=0).round(3))
        mt, st, mx = thresholds(s)
        pm = (mg[:, :mx + 1] >= mt[:mx + 1]).any(axis=1)
        ps = (sg[:, 2:mx + 1] >= st[2:mx + 1]).any(axis=1)
        for name, pred, flag in (("moving", pm, x["moving"]), ("still", ps, x["still"])):
            print(f"   {name}: flag {flag.mean():.3f} pred {pred.mean():.3f} agree {(pred == flag).mean():.3f}"
                  f"  flag&~pred {(flag & ~pred).mean():.3f}  pred&~flag {(pred & ~flag).mean():.3f}")
        for name, on, dist, e, g in (("moving", x["moving"], x["md"], x["me"], mg), ("still", x["still"], x["sd"], x["se"], sg)):
            gi = np.clip(np.floor(dist[on] / 0.75).astype(int), 0, 8)
            am = np.argmax(g[on], axis=1)
            print(f"   {name}: distance gate == argmax {np.mean(gi == am):.3f}  |gate-argmax|<=1 {np.mean(abs(gi - am) <= 1):.3f}"
                  f"  energy == max gate {np.mean(e[on] == g[on].max(1)):.3f}")
