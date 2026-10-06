"""Replay recordings through the tracker and print what it shows, for looking into a situation.

usage: python tools/replay.py --config FILE --from "YYYY-MM-DD HH:MM:SS" --to "..." [--show FROM]
                              [--every S] [--seed N] [--learned FILE] [--recordings DIR] [--tracker DIR]

The model starts at --from with nothing known (like after a restart). From --show on (default: --from)
it prints every --every seconds the count per observed room and per person where it most probably is
(room or place, probability) and the time each sensor has not seen them. --config: the app's config
(private: it holds the floor plan); --learned: what the app had learned.
"""

import argparse
import collections
import json
import os
import sys
import time

RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recordings")


def parse_time(s: str) -> float:
    """Local time, "YYYY-MM-DD HH:MM:SS" with optional fractions of a second."""
    whole, _, frac = s.partition(".")
    return time.mktime(time.strptime(whole, "%Y-%m-%d %H:%M:%S")) + (float("0." + frac) if frac else 0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recordings", default=RECORDINGS)
    ap.add_argument("--tracker", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker"))
    ap.add_argument("--config", required=True)
    ap.add_argument("--learned")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--show")
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(a.config)))
    crowd = Tracker(config, seed=a.seed)
    if a.learned:
        crowd.load_learned(json.load(open(a.learned)))
    rooms = [z.id for z in config.zones_of("room") if not any(z.id in r["rooms"] for r in config.regions.values())]
    names = {z.id: z.name for z in config.zones}
    start, end = parse_time(a.start), parse_time(a.end)
    show = parse_time(a.show) if a.show else start
    clocks = collections.defaultdict(SensorClock)
    next_step = next_print = 0.0
    t = start - start % 3600
    while t <= end:
        path = os.path.join(a.recordings, time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
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
            crowd.process_frame(sid, tt, m["payload"])
            if tt >= next_step:
                crowd.step(tt)
                next_step = tt + 0.2
            if tt < show or tt < next_print:
                continue
            next_print = tt + a.every
            st = crowd.zone_states()
            line = time.strftime("%H:%M:%S", time.localtime(tt)) + " " + " ".join(
                f"{names.get(z, z)}={st[z].count}" for z in rooms if z in st)
            dist = crowd.count_distribution()
            line += " [" + " ".join(f"{names.get(z, z)[:4]} " + "/".join(f"{q:.2f}" for q in dist[z]) for z in rooms) + f"] H{len(crowd.hyps)}"
            for d in crowd.persons():
                where = max(d["places"].items(), key=lambda kv: kv[1])
                pos = "" if d["x"] is None else f" ({d['x']:.1f},{d['y']:.1f}){' unseen' if d['lost'] else ''}"
                line += f" | P{d['id']} {where[0]} {where[1]:.2f}{pos}"
            print(line, flush=True)


if __name__ == "__main__":
    main()
