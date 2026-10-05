"""Replay recordings through the tracker and print what it shows, for looking into a situation.

usage: python tools/replay.py --config FILE --from "YYYY-MM-DD HH:MM:SS" --to "..." [--show FROM]
                              [--every S] [--seed N] [--learned FILE] [--db DIR] [--tracker DIR]

The model starts at --from with nothing known (like after a restart). From --show on (default: --from)
it prints every --every seconds the count per observed room and per person where it most probably is
(room or place, probability) and the time each sensor has not seen them. --config / --learned: files
in <db>/configs and <db>/learned of the truth database (private: they hold the floor plan).
"""

import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluate import parse_time  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.expanduser("~/.config/presence-tracker/truth"))
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
    from presence_tracker.crowd import Crowd
    from presence_tracker.frames import SensorClock
    from presence_tracker.model import Config

    db = json.load(open(os.path.join(a.db, "episodes.json")))
    config = Config.from_dict(json.load(open(os.path.join(a.db, "configs", a.config))))
    crowd = Crowd(config, seed=a.seed)
    if a.learned:
        crowd.load_learned(json.load(open(os.path.join(a.db, "learned", a.learned))))
    rooms = [z.id for z in config.zones_of("room") if not any(z.id in r["rooms"] for r in config.regions.values())]
    names = {z.id: z.name for z in config.zones}
    start, end = parse_time(a.start), parse_time(a.end)
    show = parse_time(a.show) if a.show else start
    clocks = collections.defaultdict(SensorClock)
    next_step = next_print = 0.0
    t = start - start % 3600
    while t <= end:
        path = os.path.join(db["recordings"], time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
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
            for p in crowd.people:
                d = crowd._display(p)
                c = p.cloud
                w = c.weights()
                unseen = "/".join(f"{tt - float(w @ c.last_hit[:, i]):.0f}" for i in range(len(crowd.sensors)))
                where = max(d["places"].items(), key=lambda kv: kv[1])
                pos = "" if d["x"] is None else f" ({d['x']:.1f},{d['y']:.1f}){' unseen' if d['lost'] else ''}"
                line += f" | P{p.pid} {where[0]} {where[1]:.2f}{pos} τ {unseen}"
            print(line, flush=True)


if __name__ == "__main__":
    main()
