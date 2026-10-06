"""Score the tracker against the truth taken from error reports (MODEL.md 8): the recordings replayed
the way the app ran them (a fresh model at every start of the app, the ghost map learned on from the
first one), and per reported moment and room P(somebody there) and P(the reported number) against what the report
says.

usage: python tools/report_eval.py --config FILE --truth FILE [--recordings DIR] [--patch FILE.py]...
                                   [--only NAME] [--every S] [--trace]

The truth file (private: it describes who was where) holds the app's starts and per report a window
(from the event to the report) and counts per room or region without a sensor, only where the
report says so. Printed per report and room: the means of P(somebody there) and of P(the reported
number of people) over the window, and the
share of the window in which the light would be wrong (on without anybody, off with somebody; the
threshold of MODEL.md 6). --patch: a Python file run before the replay that changes the model (for
comparing variants: it may change presence_tracker's classes in place).
"""

import argparse
import collections
import json
import os
import runpy
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))


def parse_time(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--recordings", default=os.path.join(TOOLS, "..", "recordings"))
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "tracker"))
    ap.add_argument("--patch", action="append", default=[])
    ap.add_argument("--only")
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--trace", action="store_true", help="print the rooms' P(somebody there) in the windows")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    for p in a.patch:
        runpy.run_path(p)
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(a.config)))
    truth = json.load(open(a.truth))
    reports = [r for r in truth["reports"] if not a.only or a.only in r["name"]]
    starts = sorted(parse_time(s) for s in truth["app_starts"])
    end = max(parse_time(r["to"]) for r in reports)
    windows = [(parse_time(r["from"]), parse_time(r["to"]), r) for r in reports]
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    rooms = [z.id for z in config.zones_of("room") if not any(z.id in r["rooms"] for r in config.regions.values())]

    tracker = None
    gm = None
    clocks = collections.defaultdict(SensorClock)
    samples = collections.defaultdict(list)  # report name -> [(t, {room: P(somebody)})]
    next_start = 0
    next_step = next_sample = 0.0
    loglik = 0.0
    t_cpu = time.process_time()
    t = starts[0] - starts[0] % 3600
    while t <= end:
        path = os.path.join(a.recordings, time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
        t += 3600
        if not os.path.exists(path):
            continue
        for line in open(path):
            m = json.loads(line)
            if not m["topic"].endswith("/frame") or m["t"] < starts[0]:
                continue
            if m["t"] > end:
                break
            while next_start < len(starts) and m["t"] >= starts[next_start]:
                # the app starts: a fresh model with what was learned so far
                if tracker is not None:
                    loglik += tracker.loglik
                    gm = tracker.ghost_map
                tracker = Tracker(config)
                if gm is not None:
                    tracker.use_ghost_map(gm)
                clocks.clear()
                next_start += 1
            sid = m["topic"].split("/")[1]
            tt = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
            tracker.process_frame(sid, tt, m["payload"])
            if tt >= next_step:
                tracker.step(tt)
                next_step = tt + 0.2
            if tt < next_sample:
                continue
            inside = [r for t0, t1, r in windows if t0 <= m["t"] <= t1]
            if not inside:
                continue
            next_sample = tt + a.every
            counts = tracker.count_distribution()
            places = tracker.place_distribution()
            p = {z: counts[z] for z in rooms}
            p.update({rid: places[rid] for rid in config.regions})
            for r in inside:
                samples[r["name"]].append((m["t"], p))
            if a.trace:
                print(time.strftime("%H:%M:%S", time.localtime(m["t"])) + " "
                      + " ".join(f"{z[:5]} {1 - v[0]:.2f}" for z, v in p.items()), flush=True)
    loglik += tracker.loglik if tracker is not None else 0.0

    # per report and room: mean P(somebody there), share of the time the light would be wrong
    wrong_on = wrong_off = n_on = n_off = 0.0
    for _, _, r in windows:
        ss = samples.get(r["name"], [])
        print(f"{r['name']}: {r['text'][:110]}")
        if not ss:
            print("   no data")
            continue
        parts = []
        for room, n in r["rooms"].items():
            dists = [p[room] for _, p in ss if room in p]
            if not dists:
                continue
            ps = [1 - d[0] for d in dists]
            mean = sum(ps) / len(ps)
            right = sum(d[n] if n < len(d) else 0.0 for d in dists) / len(dists)
            observed = room in rooms
            bad = sum((q > c) != (n > 0) for q in ps) / len(ps)
            if observed:
                if n > 0:
                    wrong_off += bad
                    n_on += 1
                else:
                    wrong_on += bad
                    n_off += 1
            parts.append(f"{room}{'' if observed else '(ohne Sensor)'}={n}: P {mean:.2f} P(={n}) {right:.2f}{' FALSCH %.0f%%' % (100 * bad) if bad > 0 else ''}")
        print("   " + "; ".join(parts))
    print(f"observed rooms: light wrongly on {wrong_on:.2f} of {n_off:.0f} empty room-windows, "
          f"wrongly off {wrong_off:.2f} of {n_on:.0f} occupied ones; log evidence {loglik:.1f}; "
          f"CPU {time.process_time() - t_cpu:.0f} s")


if __name__ == "__main__":
    main()
