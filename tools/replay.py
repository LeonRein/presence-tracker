"""Replay recordings through the tracker and print what it shows, for looking into a situation.

usage: python tools/replay.py --config FILE --from "YYYY-MM-DD HH:MM:SS" --to "..." [--show FROM]
                              [--every S] [--seed N] [--learned FILE] [--recordings DIR] [--tracker DIR]
       python tools/replay.py --report FILE.jsonl.gz [--from ...] [--to ...] [--show ...] [--every S]

The model starts at --from with nothing known (like after a restart). From --show on (default: --from)
it prints every --every seconds the count per observed room and per person where it most probably is
(room or place, probability) and the time each sensor has not seen them. --config: the app's config
(private: it holds the floor plan); --learned: what the app had learned. --report: an error report
of the app ("Fehler melden"): its config, what was learned and the sensor data; shown from 2 min before
the reported moment unless --show says otherwise. The replay starts where the app's model last started
from nothing known if that is in the report, else at the report's first frame (what the model knew
before is not in the report). Below each line what the app showed then ("app"), and at the end how far
the replay is from it.
"""

import argparse
import collections
import gzip
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
    ap.add_argument("--config")
    ap.add_argument("--learned")
    ap.add_argument("--report")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--show")
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker import code_hash
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.model import Config

    if a.report:
        lines = gzip.open(a.report, "rt").read().splitlines()
        meta = json.loads(lines[0])
        messages = [json.loads(x) for x in lines[1:]]
        shown = [(m["t"], m["payload"]) for m in messages if m["topic"] == "app/shown"]
        config = Config.from_dict(meta["config"])
        rep = meta["report"]
        print(f"report: {rep['kind']} in {rep['room'] or 'the house'} at "
              f"{time.strftime('%H:%M:%S', time.localtime(rep['t_event']))}: {rep['text']}")
        if rep.get("code", code_hash()) != code_hash():
            print(f"made with other code ({rep['version']}, {rep['code']}; here {code_hash()}): the replay may "
                  "differ from what the app believed")
    elif not (a.config and a.start and a.end):
        ap.error("--config, --from and --to (or --report)")
    else:
        config = Config.from_dict(json.load(open(a.config)))
    crowd = Tracker(config, seed=a.seed)
    if a.report:
        crowd.load_learned(meta)  # its ghost map and LD2410C background
    if a.learned:
        crowd.load_learned(json.load(open(a.learned)))
    rooms = [z.id for z in config.zones_of("room") if not any(z.id in r["rooms"] for r in config.regions.values())]
    names = {z.id: z.name for z in config.zones}
    if a.report:
        first = min(m["t"] for m in messages if m["topic"].endswith("/frame"))
        start = parse_time(a.start) if a.start else max(first, rep.get("model_start") or first)
        if not a.start:
            hm = time.strftime("%H:%M:%S", time.localtime(start))
            if rep.get("model_start") is None:
                print(f"the report does not say when the app's model started: the replay starts at {hm} with nothing known")
            elif rep["model_start"] >= first - 1:
                print(f"the app's model started from nothing known at {hm}, as the replay does")
            else:
                print(f"the app's model ran since before the report's data: the replay starts at {hm} with "
                      "nothing known and may differ from it at first (MODEL.md 8)")
        end = parse_time(a.end) if a.end else messages[-1]["t"]
        show = parse_time(a.show) if a.show else max(start, rep["t_event"] - 120)
    else:
        start, end = parse_time(a.start), parse_time(a.end)
        show = parse_time(a.show) if a.show else start
    clocks = collections.defaultdict(SensorClock)
    next_step = next_print = 0.0
    shown = shown if a.report else []
    k_shown = 0
    diff = {}  # room -> largest |P(somebody there)| replay - app

    def sources():
        if a.report:
            yield messages
            return
        t = start - start % 3600
        while t <= end:
            path = os.path.join(a.recordings, time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
            t += 3600
            if os.path.exists(path):
                yield (json.loads(line) for line in open(path) if line.strip())

    for source in sources():
        for m in source:
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
                f"{names.get(z, z)}={st[z].count}{'*' if st[z].occupied else ''}" for z in rooms if z in st)
            dist = crowd.count_distribution()
            line += " [" + " ".join(f"{names.get(z, z)[:4]} " + "/".join(f"{q:.2f}" for q in dist[z]) for z in rooms) + f"] H{len(crowd.hyps)}"
            for d in crowd.persons():
                where = max(d["places"].items(), key=lambda kv: kv[1])
                pos = "" if d["x"] is None else f" ({d['x']:.1f},{d['y']:.1f}){' unseen' if d['lost'] else ''}"
                line += f" | P{d['id']} {where[0]} {where[1]:.2f}{pos}"
            print(line, flush=True)
            while k_shown + 1 < len(shown) and shown[k_shown + 1][0] <= tt:
                k_shown += 1
            if shown and abs(shown[k_shown][0] - tt) <= 1.5:  # what the app showed then
                zs = shown[k_shown][1]["zones"]
                line = "    app  " + " ".join(f"{names.get(z, z)}={zs[z][0]}{'*' if zs[z][2] else ''}"
                                         for z in rooms if z in zs)
                line += " [" + " ".join(f"{names.get(z, z)[:4]} {zs[z][1]:.2f}" for z in rooms
                                        if z in zs and zs[z][1] is not None) + "]"
                for i, place, p, x, y, lost in shown[k_shown][1]["persons"]:
                    line += f" | P{i} {place} {p:.2f}" + ("" if x is None else f" ({x:.1f},{y:.1f}){' unseen' if lost else ''}")
                print(line, flush=True)
                for z in rooms:
                    if z in zs and zs[z][1] is not None:
                        diff[z] = max(diff.get(z, 0.0), abs(1 - dist[z][0] - zs[z][1]))
    if diff:
        print("largest |P(somebody there)| replay - app: " + " ".join(f"{names.get(z, z)} {v:.2f}" for z, v in diff.items()))


if __name__ == "__main__":
    main()
