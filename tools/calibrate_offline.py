"""Calibrate the LD2450s from recordings: a calibration walk or everyday data, also sensors that
hardly overlap with another.

usage: python tools/calibrate_offline.py --config FILE [--recordings DIR] [--from T] [--to T]
                                         [--fit ID,...] [--positions ID,...] [--no-pairs] [--no-plan]
                                         [--bootstrap N] [--profile ID] [--mirrors] [--cache FILE]
                                         [--out FILE] [--pauses FILE]

The model is the app's (tracker/presence_tracker/calibration.py: pairs, handovers, floor plan,
scale prior; MODEL.md 10): the recorded frames go through the same code as in the app's
calibration session. This tool adds what the app doesn't need: other time spans, a block bootstrap
over BLOCK-s blocks next to the robust covariance, the mirror and scale profiles, shifting drawn
positions, and a copy of the config with the result.

--fit: sensors (without presence-) to fit, default all placed with walking measurements.
--positions: these sensors' drawn positions may shift (prior +-0.3 m per axis); it ignores that a
sensor hangs on a wall (check that the result does).
--mirrors: each sensor also fitted with its x axis mirrored (coarse search over the heading); the
log posterior of both, also divided by the variance inflation of the robust covariance.
--profile ID: that sensor's scale held at values from 0.7 to 1.3, the rest refitted.
--pauses: intervals in which learning was paused (private, tools/vacuum_history.py: the vacuum robot);
the switch "Lernen pausieren" recorded in the recordings counts too. No measurements are kept there, as
in the app (MODEL.md 10, "Hintergrundaktivität").
--out: a copy of the config with the fitted values (a private file; never the live config).
"""

import argparse
import collections
import copy
import itertools
import json
import math
import os
import sys
import time

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))
BOOT_BLOCK = 120.0  # s, bootstrap blocks


def parse_time(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def load(config, recordings: str, t0: float, t1: float, pauses=None) -> dict:
    """The measurement rows of the app's calibration session (calibration.track_frame) per sensor
    from the recordings between t0 and t1, none while learning was paused (pauses, and the switch in
    the recordings)."""
    from presence_tracker.calibration import track_frame
    from presence_tracker.frames import SensorClock
    from presence_tracker.pause import Pauses, follow

    pauses = pauses if pauses is not None else Pauses()
    skip = set()

    ids = itertools.count()
    tracks, clocks = {}, collections.defaultdict(SensorClock)
    rows = collections.defaultdict(list)
    t = t0 - t0 % 3600
    while t <= t1:
        path = os.path.join(recordings, time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
        t += 3600
        if not os.path.exists(path):
            continue
        for line in open(path):
            m = json.loads(line)
            if follow(pauses, m) or not m["topic"].endswith("/frame") or not t0 <= m["t"] <= t1:
                continue
            sid = m["topic"].split("/")[1]
            if sid not in config.sensor_by_id:
                continue
            p = m["payload"]
            tt = clocks[sid](m["t"], p.get("uptime_ms"))
            track_frame(tracks, ids, rows, sid, tt, p, pauses.paused(tt), skip)
    return {sid: np.array(r, float).reshape(-1, 6) for sid, r in rows.items()}


def short(sid):
    return sid.replace("presence-", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--recordings", default=os.path.join(TOOLS, "..", "recordings"))
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "tracker"))
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--fit")
    ap.add_argument("--positions", default="")
    ap.add_argument("--no-pairs", action="store_true")
    ap.add_argument("--no-plan", action="store_true")
    ap.add_argument("--bootstrap", type=int, default=0)
    ap.add_argument("--profile")
    ap.add_argument("--mirrors", action="store_true")
    ap.add_argument("--cache", help="npz file for the extracted measurements")
    ap.add_argument("--out")
    ap.add_argument("--pauses", help="intervals in which learning was paused (tools/vacuum_history.py)")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker import calibration as cal
    from presence_tracker.model import Config
    from presence_tracker import pause

    config = Config.from_dict(json.load(open(a.config)))
    placed = [s.id for s in config.sensors if s.placed and s.enabled]
    if a.start:
        t0 = parse_time(a.start)
    else:
        first = min(f for f in os.listdir(a.recordings) if f.endswith(".jsonl"))
        t0 = time.mktime(time.strptime(first[:11], "%Y%m%d-%H"))
    t1 = parse_time(a.end) if a.end else time.time()
    if a.cache and os.path.exists(a.cache):
        data = dict(np.load(a.cache))
    else:
        data = load(config, a.recordings, t0, t1, pause.load(a.pauses))
        if a.cache:
            np.savez(a.cache, **data)
    data = {sid: d[(d[:, cal.T] >= t0) & (d[:, cal.T] <= t1)] for sid, d in data.items() if sid in placed}
    full = lambda x: x if x.startswith("presence-") else "presence-" + x  # noqa: E731
    base = cal.Problem(config, data, [])
    fit = [full(x) for x in a.fit.split(",")] if a.fit else \
        [sid for sid in placed if sid in base.points and len(base.points[sid].z) >= cal.MIN_POINTS]
    positions = [full(x) for x in a.positions.split(",") if x]

    prob = cal.Problem(config, data, fit, positions=positions)
    kinds = collections.Counter()
    for q in prob.pairs:
        kinds[(q.kind, f"{short(q.a)}|{short(q.b)}")] += len(q.za)
    print("walking points (tracks):", {short(k): f"{len(v.z)} ({v.ntracks})" for k, v in prob.points.items()})
    print("pairs:", {k: n for (kind, k), n in kinds.items() if kind == "pair"})
    print("handovers:", {k: n for (kind, k), n in kinds.items() if kind == "handover"})
    F = cal.Fit(prob, not a.no_pairs, not a.no_plan)
    phi0 = prob.start()
    phi = F.run(search=True)
    print(f"log posterior {F.ll:.1f} (drawn {F.loglik(phi0):.1f})")
    print("echo tracks (share of walking tracks):", {short(k): round(v, 2) for k, v in F.eps_plan.items()})
    print(f"LD2450 scales: ln scale ~ N({F.mu:.3f}, {F.tau:.3f}^2) from", [short(x) for x in F.rulers])
    print("wrong pairs / handovers:", {f"{q.kind[0]}:{short(q.a)}|{short(q.b)}": round(F.eps_pair[i], 2)
                                       for i, q in enumerate(prob.pairs)})

    boot = collections.defaultdict(list)
    if a.bootstrap:
        rng = np.random.default_rng(1)
        tmin = min(pt.t.min() for pt in prob.points.values() if len(pt.t))
        tmax = max(pt.t.max() for pt in prob.points.values() if len(pt.t))
        nblocks = int((tmax - tmin) // BOOT_BLOCK) + 1
        for _ in range(a.bootstrap):
            counts = np.bincount(rng.integers(0, nblocks, nblocks), minlength=nblocks).astype(float)
            weights = lambda t: counts[np.clip(((t - tmin) // BOOT_BLOCK).astype(int), 0, nblocks - 1)]  # noqa: E731
            Fb = cal.Fit(prob, not a.no_pairs, not a.no_plan, weights=weights)
            pb = Fb.run(phi)
            for sid in fit:
                boot[sid].append(prob.describe(pb, sid))

    print(f"\n{'sensor':14s} {'heading':>13s} {'sd robust/Laplace/boot':>22s} {'scale':>13s} {'sd robust/Laplace/boot':>24s}"
          f" {'x':>11s} {'y':>11s}  2nd mode")
    result = {}
    for sid in fit:
        s = config.sensor_by_id[sid]
        r = prob.describe(phi, sid)
        sd = F.sd(sid)
        bs = {k: float(np.std([b[k] if k != "heading" else cal.angle_diff(b[k], r[k]) for b in boot[sid]]))
              for k in ("heading", "scale", "x", "y")} if boot[sid] else {}
        alt, gap = F.second_mode(sid)
        r["sd"] = {"heading": round(max(sd["heading"], bs.get("heading", 0)), 3),
                   "scale": round(max(sd["scale"], bs.get("scale", 0)), 4)}
        result[sid] = r
        b = lambda k, nd: f"/{bs[k]:.{nd}f}" if bs else ""  # noqa: E731
        print(f"{short(sid):14s} {s.heading:5.1f}->{r['heading']:5.1f} {sd['heading']:6.2f}/{sd['heading_laplace']:4.2f}{b('heading', 2):6s}"
              f"      {s.scale:.3f}->{r['scale']:.3f} {sd['scale']:7.4f}/{sd['scale_laplace']:.4f}{b('scale', 4):8s}"
              f" {s.x:5.2f}->{r['x']:5.2f} {s.y:5.2f}->{r['y']:5.2f}  {alt:5.0f} ({gap:.1f})")

    o0, o1 = cal.outside(prob, phi0), cal.outside(prob, phi)
    g0, g1 = cal.pair_agreement(prob, phi0), cal.pair_agreement(prob, phi)
    c0, c1 = cal.crossings(prob, phi0), cal.crossings(prob, phi)
    print("\nwalking points more than 0.3 m out of sight, drawn -> fitted:")
    for sid in o0:
        print(f"  {short(sid):14s} {100 * o0[sid]:4.0f} %  ->  {100 * o1[sid]:4.0f} %")
    print("simultaneous pairs within 1 m, their median distance; drawn -> fitted:")
    for k in g0:
        (n, a0, d0), (_, a1, d1) = g0[k], g1[k]
        print(f"  {short(k[0]) + '|' + short(k[1]):26s} n={n:5d}  {a0:5d} {d0:.2f}  ->  {a1:5d} {d1:.2f}")
    print("steps of walking tracks across a wall line: through an opening / all; drawn -> fitted:")
    for sid in c0:
        print(f"  {short(sid):14s} {c0[sid][1]:4d} / {c0[sid][0]:4d}  ->  {c1[sid][1]:4d} / {c1[sid][0]:4d}")

    if a.mirrors:
        print("\nmirrored x axis (log posterior, higher is better; adjusted: divided by the variance inflation):")
        for sid in fit:
            pm = cal.Problem(config, data, fit, mirrors={sid: not prob.mirror[sid]}, positions=positions)
            Fm = cal.Fit(pm, not a.no_pairs, not a.no_plan)
            Fm.run(search=True)
            i = prob.npar * fit.index(sid)
            k = max(F.cov[i, i] / max(F.cov_laplace[i, i], 1e-12), 1.0)
            rm = pm.describe(Fm.phi, sid)
            print(f"  {short(sid):14s} as configured ({'mirrored' if prob.mirror[sid] else 'not mirrored'}) {F.ll:.1f}; "
                  f"flipped {Fm.ll:.1f} (heading {rm['heading']}, scale {rm['scale']}), adjusted {(Fm.ll - F.ll) / k:+.1f}")

    if a.profile:
        sid = full(a.profile)
        print(f"\nprofile of the scale of {short(sid)}, the rest refitted: log posterior relative to the fit, "
              "walking points out of sight, heading")
        for k in np.arange(0.7, 1.31, 0.05):
            Fp = cal.Fit(prob, not a.no_pairs, not a.no_plan, hold={sid: float(k)})
            st = phi.copy()
            st[prob.npar * fit.index(sid) + 1] = math.log(k)
            Fp.run(st)
            out = cal.outside(prob, Fp.phi).get(sid, float("nan"))
            print(f"  {k:.2f}: {Fp.ll - F.ll:8.1f}   out of sight {100 * out:3.0f} %   heading {prob.describe(Fp.phi, sid)['heading']}")

    print("\nthe app's verdict (calibration.solve):")
    res = cal.solve(config, data)
    for sid, r in res.get("sensors", {}).items():
        print(f"  {short(sid):14s} {r['quality']:4s} heading {r['heading']} ± {r['heading_sd']} (plan {r['heading_plan']}, pairs {r['heading_pairs']})"
              f" scale {r['scale']} ± {r['scale_sd']}  {r['reason']}")
    print("  unsolved:", [short(x) for x in res.get("unsolved", [])], res.get("error", ""))

    if a.out:
        out = copy.deepcopy(json.load(open(a.config)))
        for sd in out["sensors"]:
            if sd["id"] in result:
                r = result[sd["id"]]
                sd.update(heading=r["heading"], scale=r["scale"], x=r["x"], y=r["y"], mirror=r["mirror"])
        json.dump(out, open(a.out, "w"), indent=1, ensure_ascii=False)
        print(f"\nwritten {a.out}")


if __name__ == "__main__":
    main()
