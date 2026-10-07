"""Score the tracker against the truth taken from error reports (MODEL.md 8): the recordings replayed
the way the app ran them (a fresh model at every start of the app, the ghost map, the LD2410C
background and the destination map learned on from the first one, the people's state saved when the app stopped and restored at
its start, MODEL.md 5.3), and per reported moment and room P(somebody there) and P(the reported
number) against what the report
says.

usage: python tools/report_eval.py --config FILE --truth FILE [--recordings DIR] [--patch FILE.py]...
                                   [--only NAME] [--every S] [--trace] [--downtime S] [--forget]

--downtime: the app did not run for this long before each start (the recorder did: those frames are
skipped). --forget: every start with nothing known about the people (as before people.json).

The truth file (private: it describes who was where) holds the app's starts and per report a window
(from the event to the report) and counts per room or region without a sensor, only where the
report says so; a window with "walks": S (e.g. a night, everybody in bed) excuses the moments in which an
LD2450 measured somebody in that room in the last S seconds (somebody walking through). Printed per report and room: the means of P(somebody there) and of P(the reported
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
    ap.add_argument("--downtime", type=float, default=0.0)
    ap.add_argument("--forget", action="store_true")
    ap.add_argument("--trace", action="store_true", help="print the rooms' P(somebody there) and the people of the "
                                                        "most probable hypothesis in the windows")
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
    rooms = [z.id for z in config.observed_rooms()]
    zones = [z for z in config.zones_of("room") if z.id in rooms]
    outside = {z.id for z in config.outside_rooms}  # entry rooms: counts there are not scored
    seen = {}  # room -> last time an LD2450 measured somebody there (for windows that excuse walks)

    tracker = None
    gm = ldb = dm = None
    clocks = collections.defaultdict(SensorClock)
    samples = collections.defaultdict(list)  # report name -> [(t, {room: P(somebody)})]
    next_start = 0
    next_step = next_sample = 0.0
    loglik = 0.0
    segments = []  # log evidence per run of the app
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
            if next_start < len(starts) and starts[next_start] - a.downtime <= m["t"] < starts[next_start]:
                continue  # the app is down
            while next_start < len(starts) and m["t"] >= starts[next_start]:
                # the app starts: a fresh model with what was learned so far and what was known about
                # the people when it stopped
                people = None
                if tracker is not None:
                    loglik += tracker.loglik
                    segments.append(tracker.loglik)
                    gm, ldb = tracker.ghost_map, getattr(tracker, "ld_background", None)
                    dm = getattr(tracker, "dest_map", None)
                    people = None if a.forget else json.loads(json.dumps(tracker.people_state()))
                tracker = Tracker(config)
                if gm is not None:
                    tracker.use_ghost_map(gm)
                if ldb is not None:
                    tracker.use_ld_background(ldb)
                if dm is not None:
                    tracker.use_dest_map(dm)
                if people is not None and not tracker.restore_people(people):
                    print("the saved people do not fit: nothing known")
                clocks.clear()
                next_start += 1
            sid = m["topic"].split("/")[1]
            tt = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
            tracker.process_frame(sid, tt, m["payload"])
            rt = tracker.runtime.get(sid)
            for d in (rt.detections if rt is not None else []):
                if not d.hidden and not d.stale:
                    z = next((z.id for z in zones if z.contains(float(d.pos[0]), float(d.pos[1]))), None)
                    if z is not None:
                        seen[z] = m["t"]
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
                if r.get("walks"):  # nobody stays: where an LD2450 measures somebody, they walk through
                    p = {z: v for z, v in p.items() if m["t"] - seen.get(z, -1e9) > r["walks"]}
                samples[r["name"]].append((m["t"], p))
            if a.trace:
                print(time.strftime("%H:%M:%S", time.localtime(m["t"])) + " "
                      + " ".join(f"{z[:5]} {1 - v[0]:.2f}" for z, v in p.items()) + f" H{len(tracker.hyps)}"
                      + "".join(f" | P{d['id']} {max(d['places'].items(), key=lambda kv: kv[1])[0][:6]}"
                                + ("" if d["x"] is None else f" ({d['x']:.1f},{d['y']:.1f}){' unseen' if d['lost'] else ''}")
                                for d in tracker.persons()), flush=True)
    loglik += tracker.loglik if tracker is not None else 0.0
    segments.append(tracker.loglik if tracker is not None else 0.0)

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
            if room in outside:  # the stairwell is outside (MODEL.md 2): nothing to score
                parts.append(f"{room}={n}: außer Haus, nicht bewertet")
                continue
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
            if r.get("walks") and bad > 0:  # long windows: when the light would have been wrong
                runs, start, last = [], None, None
                for tt_, p_ in ss:
                    if room not in p_:
                        continue
                    wrong = (1 - p_[room][0] > c) != (n > 0)
                    if wrong and start is None:
                        start = tt_
                    if not wrong and start is not None:
                        runs.append((start, last))
                        start = None
                    last = tt_
                if start is not None:
                    runs.append((start, last))
                parts[-1] += f" [{sum(b - a_ for a_, b in runs) / 60:.1f} min: " + ", ".join(
                    time.strftime("%H:%M:%S", time.localtime(a_)) + f" {b - a_:.0f} s" for a_, b in runs if b - a_ >= 10) + "]"
        print("   " + "; ".join(parts))
    print("log evidence per run of the app: " + ", ".join(
        f"{time.strftime('%d. %H:%M', time.localtime(s))} {v:.1f}" for s, v in zip(starts, segments)))
    print(f"observed rooms: light wrongly on {wrong_on:.2f} of {n_off:.0f} empty room-windows, "
          f"wrongly off {wrong_off:.2f} of {n_on:.0f} occupied ones; log evidence {loglik:.1f}; "
          f"CPU {time.process_time() - t_cpu:.0f} s")


if __name__ == "__main__":
    main()
