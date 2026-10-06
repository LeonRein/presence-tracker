"""Evaluate the tracker on recordings with known truth (the truth database).

usage: python tools/evaluate.py [--db DIR] [--tracker DIR] [--seeds N] [--jobs N] [--only ID ...]

The database (default ~/.config/presence-tracker/truth, private: it holds the floor plan) is a
directory with episodes.json and the configs it names. An episode: the config, what the app had learned by then (optional, in <db>/learned), where the model starts
("people": place names, or null = nothing known, like after a restart), the end, and the truth: from
each listed time on, the people per observed room ({"wohnzimmer": 1, ...}) or only their sum
({"observed": 2}), or null (not scored).

Only the observed rooms are scored: what Home Assistant automations see. Per second, after a grace
time after each truth change (people walk meanwhile):
  wrong      share of seconds where a room shows the wrong number
  over/under person-seconds too many / too few, summed over the rooms
  events     changes of the shown numbers while the truth stays the same (each can switch a light)
  latency    per truth change: seconds until the shown numbers match and stay so (never: they don't)
  log, brier the distribution of the number of people per room against the truth (Gneiting & Raftery
             2007): -ln P(true number), and sum over k of (P(k) - [k = true number])^2, per second
             and summed over the rooms. Finer than "wrong": a room shown right with 51 % counts
             less than one shown right with 99 %
Lights (what the tracker is mostly for): per observed room a light that goes on as soon as the room
shows somebody and off DELAY s after it shows nobody, against an ideal light that follows the truth
with the same delay. Scored for DELAY in OFF_DELAYS (printed: --off-delay):
  false on   seconds the light is on while the ideal one is off (nobody there, not just left), and
             how often it went on there - the worst error
  dark       seconds the light is off while somebody is in the room
  on after   per entry into an empty room: seconds until the light is on (median / max; never: not
             before the next truth change)
Each episode runs with several seeds (the particle filter is random).
"""

import argparse
import collections
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

GRACE = 8.0  # s after a truth change
OFF_DELAYS = (30, 60, 120)  # s
LOG_FLOOR = 1e-6  # P(true number) below this counts as this (one hopeless second must not outweigh all)


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
    if episode.get("learned"):  # what the app had learned by then: it never runs without
        crowd.load_learned(json.load(open(os.path.join(db["dir"], "learned", episode["learned"]))))
    start, end = parse_time(episode["start"]["time"]), parse_time(episode["end"])
    truth = [(parse_time(t), want) for t, want, *_ in episode["truth"]]
    clocks = collections.defaultdict(SensorClock)
    series = []  # (t, index of the truth entry, shown counts per room, P(person k there) per room)
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
            probs = crowd.zone_probabilities()
            series.append((tt, k, {z: st[z].count if z in st else 0 for z in rooms}, {z: probs.get(z, []) for z in rooms}))

    def matches(shown, want):
        if "observed" in want:
            return sum(shown.values()) == want["observed"]
        return all(shown.get(z, 0) == want.get(z, 0) for z in set(shown) | set(want))

    def count_dist(ps):
        dist = [1.0]
        for q in ps:
            dist = [(dist[i] if i < len(dist) else 0.0) * (1 - q) + (dist[i - 1] * q if i else 0.0) for i in range(len(dist) + 1)]
        return dist

    def score(dist, n):
        p = dist[n] if n < len(dist) else 0.0
        return -math.log(max(p, LOG_FLOOR)), sum((q - (i == n)) ** 2 for i, q in enumerate(dist)) + (n >= len(dist))

    acc = collections.Counter()
    prev = None
    for tt, k, shown, probs in series:
        tc, want = truth[k]
        if want is None:
            prev = shown
            continue
        settled = tt - tc >= GRACE
        if settled:
            acc["seconds"] += 1
            acc["wrong"] += not matches(shown, want)
            if "observed" in want:
                n_people = max((len(ps) for ps in probs.values()), default=0)
                ls, bs = score(count_dist([sum(ps[i] for ps in probs.values()) for i in range(n_people)]), want["observed"])
            else:
                ls = bs = 0.0
                for z in rooms:
                    a, b = score(count_dist(probs[z]), want.get(z, 0))
                    ls, bs = ls + a, bs + b
            acc["log"] += ls
            acc["brier"] += bs
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
    lights = {d: light_scores(series, truth, rooms, d) for d in OFF_DELAYS}
    latency = []
    for k, (tc, want) in enumerate(truth):
        rows = [(tt, shown) for tt, kk, shown, _ in series if kk == k]
        if want is None or not rows or k == 0:
            continue
        good = [matches(shown, want) for _, shown in rows]
        first = next((rows[i][0] - tc for i in range(len(rows)) if all(good[i:])), None)
        latency.append(first)
    return {"episode": episode["id"], "seed": seed, **acc, "latency": latency, "lights": lights}


def light_scores(series, truth, rooms, delay):
    """The lights of the observed rooms (module doc) for one switch-off delay."""
    out = {"false_on": 0, "false_switch": 0, "dark": 0, "on_after": []}
    for z in rooms:
        shown_at = truth_at = -math.inf  # last time the room showed / truly had somebody
        light = False
        entry = None  # (truth index, time) of an entry into the empty room not yet lit
        known_empty = True
        for tt, k, shown, _ in series:
            tc, want = truth[k]
            n = None if want is None or "observed" in want else want.get(z, 0)
            if shown[z] > 0:
                shown_at = tt
            was = light
            light = tt - shown_at < delay
            if n is not None:
                if n > 0:
                    if known_empty and entry is None and tt - tc < GRACE + 1:
                        entry = (k, tc)
                    truth_at = tt
                    known_empty = False
                else:
                    known_empty = True
            else:
                known_empty = False  # not known: no entry into an empty room can be scored next
            if entry is not None and (light or entry[0] != k):
                out["on_after"].append(tt - entry[1] if light and entry[0] == k else None)
                entry = None
            if n is None or tt - tc < GRACE:
                continue
            ideal = n > 0 or tt - truth_at < delay
            if light and not ideal:
                out["false_on"] += 1
                out["false_switch"] += not was
            if n > 0 and not light:
                out["dark"] += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.expanduser("~/.config/presence-tracker/truth"))
    ap.add_argument("--tracker", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker"),
                    help="the tracker/ directory of the version to evaluate")
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--only", nargs="*", help="episode ids")
    ap.add_argument("--json", help="also write every run as a JSON line to this file")
    ap.add_argument("--off-delay", type=int, default=60, choices=OFF_DELAYS, help="switch-off delay of the lights printed")
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
    print(f"{'episode':28s} {'wrong %':>16s} {'over':>6s} {'under':>6s} {'events':>7s} {'latency med/max':>16s} {'never':>6s}"
          f" {'log':>6s} {'brier':>6s} | {'false on s':>10s} {'switch':>6s} {'dark s':>6s} {'on after':>12s}")
    for e in episodes:
        rs = [r for r in results if r["episode"] == e["id"]]
        w = [100 * r.get("wrong", 0) / max(r.get("seconds", 0), 1) for r in rs]
        lat = [x for r in rs for x in r["latency"]]
        ok = [x for x in lat if x is not None]
        print(f"{e['id']:28s} {statistics.mean(w):6.2f} ({min(w):4.1f}-{max(w):5.1f}) "
              f"{statistics.mean(r.get('over', 0) for r in rs):6.0f} {statistics.mean(r.get('under', 0) for r in rs):6.0f} "
              f"{statistics.mean(r.get('events', 0) for r in rs):7.1f} "
              + (f"{statistics.median(ok):7.1f}/{max(ok):6.1f}" if ok else f"{'-':>14s}") + f" {sum(x is None for x in lat):6d}"
              + f" {statistics.mean(r.get('log', 0) / max(r.get('seconds', 0), 1) for r in rs):6.3f}"
              + f" {statistics.mean(r.get('brier', 0) / max(r.get('seconds', 0), 1) for r in rs):6.3f}"
              + light_columns([r["lights"][a.off_delay] for r in rs]))


def light_columns(ls):
    on = [x for li in ls for x in li["on_after"] if x is not None]
    never = sum(x is None for li in ls for x in li["on_after"])
    return (f" | {statistics.mean(li['false_on'] for li in ls):10.0f} {statistics.mean(li['false_switch'] for li in ls):6.1f}"
            f" {statistics.mean(li['dark'] for li in ls):6.0f} "
            + (f"{statistics.median(on):5.1f}/{max(on):5.1f}" if on else f"{'-':>11s}") + (f" never {never}" if never else ""))


if __name__ == "__main__":
    main()
