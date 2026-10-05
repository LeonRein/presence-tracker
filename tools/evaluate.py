"""Evaluate the tracker on recordings with known truth (the truth database).

usage: python tools/evaluate.py [--db DIR] [--tracker DIR] [--seeds N] [--jobs N] [--only ID ...]

The database (default ~/.config/presence-tracker/truth, private: it holds the floor plan) is a
directory with episodes.json and the configs it names. An episode: the config, where the model starts
("people": place names, or null = nothing known, like after a restart), the end, and the truth: from
each listed time on, the people per observed room ({"wohnzimmer": 1, ...}) or only their sum
({"observed": 2}), or null (not scored).

Only the observed rooms are scored: what Home Assistant automations see. Per second, after a grace
time after each truth change (people walk meanwhile):
  wrong      share of seconds where a room shows the wrong number
  over/under person-seconds too many / too few, summed over the rooms
  events     changes of the shown numbers while the truth stays the same (each can switch a light)
  latency    per truth change: seconds until the shown numbers match and stay so (never: they don't)
Each episode runs with several seeds (the particle filter is random).
"""

import argparse
import collections
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

GRACE = 8.0  # s after a truth change


def parse_time(s: str) -> float:
    """Local time, "YYYY-MM-DD HH:MM:SS" with optional fractions of a second."""
    whole, _, frac = s.partition(".")
    return time.mktime(time.strptime(whole, "%Y-%m-%d %H:%M:%S")) + (float("0." + frac) if frac else 0.0)


def run(args):
    db, tracker_dir, episode, seed = args
    sys.path.insert(0, tracker_dir)
    from presence_tracker.crowd import Crowd
    from presence_tracker.model import Config
    try:
        from presence_tracker.frames import SensorClock
    except ImportError:  # versions before 0.6.11
        from presence_tracker.tracker import SensorClock

    config = Config.from_dict(json.load(open(os.path.join(db["dir"], "configs", episode["config"]))))
    rooms = [z.id for z in config.zones_of("room") if not any(z.id in r["rooms"] for r in config.regions.values())]
    kw = {"seed": seed}
    if episode["start"]["people"] is not None:
        kw["people"] = episode["start"]["people"]
    crowd = Crowd(config, **kw)
    start, end = parse_time(episode["start"]["time"]), parse_time(episode["end"])
    truth = [(parse_time(t), want) for t, want, *_ in episode["truth"]]
    clocks = collections.defaultdict(SensorClock)
    series = []  # (t, index of the truth entry, shown counts per room)
    next_score, next_step = truth[0][0], 0.0
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
            if tt < next_score:
                continue
            next_score += 1.0
            k = max(i for i, (tc, _) in enumerate(truth) if tc <= tt)
            st = crowd.zone_states()
            series.append((tt, k, {z: st[z].count if z in st else 0 for z in rooms}))

    def matches(shown, want):
        if "observed" in want:
            return sum(shown.values()) == want["observed"]
        return all(shown.get(z, 0) == want.get(z, 0) for z in set(shown) | set(want))

    acc = collections.Counter()
    prev = None
    for tt, k, shown in series:
        tc, want = truth[k]
        if want is None:
            prev = shown
            continue
        settled = tt - tc >= GRACE
        if settled:
            acc["seconds"] += 1
            acc["wrong"] += not matches(shown, want)
            if "observed" in want:
                diff = sum(shown.values()) - want["observed"]
                acc["over"] += max(diff, 0)
                acc["under"] += max(-diff, 0)
            else:
                for z in set(shown) | set(want):
                    diff = shown.get(z, 0) - want.get(z, 0)
                    acc["over"] += max(diff, 0)
                    acc["under"] += max(-diff, 0)
            if prev is not None and shown != prev:
                acc["events"] += 1
        prev = shown
    latency = []
    for k, (tc, want) in enumerate(truth):
        rows = [(tt, shown) for tt, kk, shown in series if kk == k]
        if want is None or not rows or k == 0:
            continue
        good = [matches(shown, want) for _, shown in rows]
        first = next((rows[i][0] - tc for i in range(len(rows)) if all(good[i:])), None)
        latency.append(first)
    return {"episode": episode["id"], "seed": seed, **acc, "latency": latency}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.expanduser("~/.config/presence-tracker/truth"))
    ap.add_argument("--tracker", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker"),
                    help="the tracker/ directory of the version to evaluate")
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--only", nargs="*", help="episode ids")
    ap.add_argument("--json", help="also write every run as a JSON line to this file")
    a = ap.parse_args()
    db = json.load(open(os.path.join(a.db, "episodes.json")))
    db["dir"] = a.db
    episodes = [e for e in db["episodes"] if not a.only or e["id"] in a.only]
    tasks = [(db, os.path.abspath(a.tracker), e, s) for e in episodes for s in range(1, a.seeds + 1)]
    with ProcessPoolExecutor(a.jobs) as pool:
        results = list(pool.map(run, tasks))
    if a.json:
        with open(a.json, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
    print(f"{'episode':28s} {'wrong %':>16s} {'over':>6s} {'under':>6s} {'events':>7s} {'latency med/max':>16s} {'never':>6s}")
    for e in episodes:
        rs = [r for r in results if r["episode"] == e["id"]]
        w = [100 * r.get("wrong", 0) / max(r.get("seconds", 0), 1) for r in rs]
        lat = [x for r in rs for x in r["latency"]]
        ok = [x for x in lat if x is not None]
        print(f"{e['id']:28s} {statistics.mean(w):6.2f} ({min(w):4.1f}-{max(w):5.1f}) "
              f"{statistics.mean(r.get('over', 0) for r in rs):6.0f} {statistics.mean(r.get('under', 0) for r in rs):6.0f} "
              f"{statistics.mean(r.get('events', 0) for r in rs):7.1f} "
              + (f"{statistics.median(ok):7.1f}/{max(ok):6.1f}" if ok else f"{'-':>14s}") + f" {sum(x is None for x in lat):6d}")


if __name__ == "__main__":
    main()
