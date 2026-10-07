"""Learn where each sensor starts ghost tracks (MODEL.md 4.2) from recordings without truth.

usage: python tools/ghostmap.py --config FILE --window FROM TO [--window ...] --out FILE
                                [--iterations N] [--jobs N] [--recordings DIR]

EM (Kantas et al. 2015, sec. 5): run the filter with the current map over the windows; every track
that ends counts at its first position with the filter's probability that it was a ghost (without
what the map itself said there, MODEL.md 4.2); the time
each sensor watched is the exposure (Luber 2014, ch. 6: a Poisson process per cell with a Gamma
prior). The next map is these counts; repeat. The windows are cut into pieces of an hour, run in
parallel, each from "nothing known"; the first WARMUP s of a piece are not counted.
"""

import argparse
import collections
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

TRACKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker")
RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recordings")
PIECE = 3600.0
WARMUP = 300.0


def parse_time(s: str) -> float:
    """Local time, "YYYY-MM-DD HH:MM:SS" with optional fractions of a second."""
    whole, _, frac = s.partition(".")
    return time.mktime(time.strptime(whole, "%Y-%m-%d %H:%M:%S")) + (float("0." + frac) if frac else 0.0)


def pieces(windows):
    """The windows in pieces of at most PIECE s."""
    out = []
    for x, y in windows:
        t = x
        while y - t > WARMUP + 60:
            out.append((t, min(t + PIECE, y)))
            t += PIECE - WARMUP
    return out


def run(args):
    recordings, config_path, gm_dict, piece = args
    sys.path.insert(0, os.path.abspath(TRACKER))
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.ghostmap import GhostMap, pose_of
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(config_path)))
    tr = Tracker(config)
    tr.use_ghost_map(GhostMap.from_dict(gm_dict))
    tr.learn_ghosts = False  # batch EM: the map stays fixed within an iteration
    start, end = piece
    births, watch = [], collections.Counter()

    def listen(kind, data):
        if tr.now < start + WARMUP:
            return
        if kind == "track_end":
            sid, z0, p, life, lost = data
            births.append((sid, [float(z0[0]), float(z0[1])], p, life, lost))
        elif kind == "watch":
            sid, dt = data
            watch[sid] += dt
    tr.listeners.append(listen)
    clocks = collections.defaultdict(SensorClock)
    t = start - start % 3600
    while t <= end:
        path = os.path.join(recordings, time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
        t += 3600
        if not os.path.exists(path):
            continue
        for line in open(path):
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not m["topic"].endswith("/frame") or m["t"] < start:
                continue
            if m["t"] > end:
                break
            sid = m["topic"].split("/")[1]
            tt = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
            tr.process_frame(sid, tt, m["payload"])
    return births, dict(watch), tr.loglik


def fit_lives(lives, types, rounds=200) -> tuple:
    """Weighted EM for a mixture of exponentials: lives [(s, weight)], start from types ((share, mean),
    ...). Returns ((share, mean), ...)."""
    import numpy as np
    x = np.array([max(l, 0.089) for l, _ in lives])
    w = np.array([p for _, p in lives])
    pi = np.array([s for s, _ in types], dtype=float)
    pi /= pi.sum()
    mu = np.array([m for _, m in types], dtype=float)
    for _ in range(rounds):
        r = pi[None, :] / mu[None, :] * np.exp(-x[:, None] / mu[None, :])
        r /= np.maximum(r.sum(axis=1, keepdims=True), 1e-300)
        rw = r * w[:, None]
        pi = rw.sum(axis=0) / w.sum()
        mu = (rw * x[:, None]).sum(axis=0) / np.maximum(rw.sum(axis=0), 1e-300)
    return tuple((float(p), float(m)) for p, m in zip(pi, mu))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recordings", default=RECORDINGS)
    ap.add_argument("--config", required=True, help="the app's config (private: it holds the floor plan)")
    ap.add_argument("--window", nargs=2, action="append", required=True, metavar=("FROM", "TO"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(TRACKER))
    from presence_tracker.filter import Tracker
    from presence_tracker.ghostmap import GhostMap, pose_of
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(a.config)))
    tr = Tracker(config)
    work = pieces([(parse_time(x), parse_time(y)) for x, y in a.window])
    print(f"{len(work)} pieces, {sum(y - x for x, y in work) / 3600:.1f} h", flush=True)
    m = tr.m
    gm = GhostMap.for_world(tr.world, sum(rate for rate, _ in m.ghost_types), m.ghost_prior_ghosts)
    gm.poses = {s.id: pose_of(s) for s in config.sensors}
    kinds = m.ghost_types  # how long ghosts live: printed for the model (filtermodel.ghost_types), not in the map
    for it in range(a.iterations):
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(run, [(a.recordings, a.config, gm.to_dict(), p) for p in work]))
        new = GhostMap(gm.x0, gm.y0, gm.nx, gm.ny, gm.prior_rate, gm.prior_time)
        new.poses = gm.poses
        loglik = 0.0
        lives = []
        for births, watch, ll in results:
            loglik += ll
            for sid, z0, p, life, lost in births:
                new.add_birth(sid, z0, p)
                if not lost and p > 0:
                    lives.append((life, p))
            for sid, dt in watch.items():
                new.add_watch(sid, dt)
        line = f"iteration {it + 1}: log evidence {loglik:.1f};"
        for sid in sorted(new.time):
            n = float(new.count[sid].sum()) if sid in new.count else 0.0
            line += f" {sid}: {n:.1f} ghosts in {new.time[sid] / 3600:.1f} h"
        kinds = fit_lives(lives, kinds) if lives else kinds
        line += " | kinds (share, mean life s): " + ", ".join(f"({s:.2f}, {mu:.1f})" for s, mu in kinds)
        print(line, flush=True)
        gm = new
    gm.save(a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()
