"""Light on in an empty room? Replays recordings through the tracker and finds, per observed room,
the stretches in which no LD2450 of any sensor had a target in the room (none within +-W s) while
the tracker said P(occupied) > the light threshold (MODEL.md 6) for longer than D s. Without truth:
a person the LD2450 suppresses (sitting) also counts here, so read the episodes, not only the sum.

usage: phantom.py --config FILE --recordings DIR [--tracker DIR] [--from [YYYY-MM-DD ]HH:MM] [--to ...]
                  [--window 30] [--min 30] [--every 1] [--patch FILE.py]..."""
import argparse
import bisect
import collections
import glob
import json
import os
import runpy
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--recordings", required=True)
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "..", "tracker"))
    ap.add_argument("--day", default="2026-10-06")
    ap.add_argument("--from", dest="t_from", default="00:00")
    ap.add_argument("--to", dest="t_to", default="23:59")
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--min", dest="min_len", type=float, default=30.0)
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--patch", action="append", default=[], help="Python file run before the replay (as in report_eval.py)")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    for p in a.patch:
        runpy.run_path(p)
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(a.config)))
    t0, t1 = (time.mktime(time.strptime(v if " " in v else f"{a.day} {v}", "%Y-%m-%d %H:%M")) for v in (a.t_from, a.t_to))
    tracker = Tracker(config)
    rooms = [z for z in config.zones_of("room") if z.id in tracker.rooms]
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    seen = collections.defaultdict(list)  # room -> times of LD2450 targets in it

    def listen(kind, data):
        if kind == "frame":
            s, t, dets = data
            for d in dets:
                if d.hidden:
                    continue
                z = next((z for z in rooms if z.contains(*d.pos)), None)
                if z is not None:
                    seen[z.id].append(t)

    tracker.listeners.append(listen)
    clocks = collections.defaultdict(SensorClock)
    samples = []  # (t, {room: P(occupied)})
    nxt = nstep = 0.0
    cpu = time.process_time()
    for path in sorted(glob.glob(os.path.join(a.recordings, "*.jsonl"))):
        for line in open(path):
            m = json.loads(line)
            if not m["topic"].endswith("/frame") or not t0 <= m["t"] <= t1:
                continue
            sid = m["topic"].split("/")[1]
            tt = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
            tracker.process_frame(sid, tt, m["payload"])
            if tt >= nstep:
                tracker.step(tt)
                nstep = tt + 0.2
            if tt >= nxt:
                nxt = tt + a.every
                counts = tracker.count_distribution()
                samples.append((tt, {r: 1 - counts[r][0] for r in tracker.rooms}))
    total = collections.Counter()
    empty = collections.Counter()
    print(f"light threshold {c:.2f}; empty = no LD2450 target in the room within +-{a.window:.0f} s; "
          f"episodes longer than {a.min_len:.0f} s")
    for r in tracker.rooms:
        ts = sorted(seen[r])
        run = None
        episodes = []
        for t, p in samples:
            i = bisect.bisect_left(ts, t - a.window)
            is_empty = not (i < len(ts) and ts[i] <= t + a.window)
            if is_empty:
                empty[r] += a.every
            on = is_empty and p[r] > c
            if on and run is None:
                run = [t, t, p[r]]
            elif on:
                run[1], run[2] = t, max(run[2], p[r])
            elif run is not None:
                if run[1] - run[0] >= a.min_len:
                    episodes.append(run)
                run = None
        if run is not None and run[1] - run[0] >= a.min_len:
            episodes.append(run)
        total[r] = sum(e[1] - e[0] for e in episodes)
        print(f"  {r:14s} empty {empty[r] / 60:6.1f} min: light wrongly on {len(episodes):3d} times, {total[r] / 60:6.1f} min")
        for e in episodes:
            print(f"      {time.strftime('%H:%M:%S', time.localtime(e[0]))}-{time.strftime('%H:%M:%S', time.localtime(e[1]))}"
                  f" ({e[1] - e[0]:5.0f} s, max P {e[2]:.2f})")
    print(f"all rooms: {sum(total.values()) / 60:.1f} min wrongly on of {sum(empty.values()) / 60:.0f} min empty; "
          f"CPU {time.process_time() - cpu:.0f} s")


if __name__ == "__main__":
    main()
