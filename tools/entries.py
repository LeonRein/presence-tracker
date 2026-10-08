"""How early does the light come on before somebody enters a room, and how often in vain? "Wird betreten"
(approaching, p_enter) and "Ziel" (target) of the tracker against the door crossings in the recordings
(MODEL.md 6, 10).

Truth: the door crossings of the LD2450's own tracks (measured points only, in the world of --truth-config,
the best calibration), independent of the tracker:
  1. within one track: it passes a door opening (TOL beyond each side) from >= DEEP m on one side
     (within WIN s before) to >= DEEP m on the other (within WIN s after);
  2. hand-over: a track ends within NEAR m of a door on one side, moving towards it, and a track of
     another sensor starts within NEAR m on the other side within [-1, +3] s and moves away;
  3. into / out of where no sensor follows: a track's last (first) second moves towards (away from) a
     door within NEAR m of it (sensors within 4.5 m of the door only).
Events of one door and direction within 2.5 s merge; simultaneous ones from one room into two rooms are
ambiguous (a within-track crossing wins, else none). A divider counts like a door (an open boundary;
"Ziel" does not predict across one, MODEL.md 6). Against the hand-switched ceiling lights this found 74 %
of the entries, 0.45 s before the switch (MODEL.md 10).

Replay: the recordings through the tracker the way the app ran them (a new model at every --start,
what was learned carried on, the people's state restored), stepped every 0.2 s, with the configuration
of each period (--config, --config-at), learning the destination map from nothing (or from --learned).

Scored, per room and overall:
  - entries: reliable crossings (within a track, hand-over, into where no sensor follows) into a room the
    model observed, which was dark (P(occupied) at most its threshold) 4-6 s before; lead = the entry
    time - the moment the light came on and stayed on (gaps < 1 s bridged; negative: late);
    distance = where the person then was (their track) to the door;
  - false ons: the signal on while the room is dark, with no entry into it (any kind) from 0.5 s before
    until max(start + lead_time, end) + 3 s, and the room not occupied then; per hour, mean length;
  - calibration: the probability in bins, while the room is dark, against an entry within 5 s.
Per hour since the start of the replay (the learning curve) the same in short.

usage: python tools/entries.py --from "2026-10-06 18:00:00" --to "2026-10-07 20:00:00"
           --config FILE [--config-at "YYYY-mm-dd HH:MM:SS=FILE"]... [--start "YYYY-mm-dd HH:MM:SS"]...
           [--truth-config FILE] [--log OUT.npz] [--from-log IN.npz] [--learned FILE] [--no-learning]
           [--prior WALKS] [--threshold C] [--room-threshold ROOM=C]... [--hourly]
           [--score-from "YYYY-mm-dd HH:MM:SS"] [--score-to "..."] [--pauses FILE] [--no-pauses]
Entries into a room before it had a sensor (only inferred from a track ending at its door) are not
scored; their number is printed.
Lernen pausieren (MODEL.md 10, "Hintergrundaktivität"): nothing is learned where the app's switch was on in
the recordings (tools/record.py records it) or in the intervals of --pauses (private, e.g. the vacuum robot's
history before the switch: tools/vacuum_history.py), and nothing is scored there (entries, false ons,
calibration). --no-pauses: neither, for comparing.
--from-log scores a replay logged before (--log) again, e.g. with another --prior or thresholds: the
decision of "Ziel" is recomputed from what was logged (p_enter, the map's probability and its walks).
"""

import argparse
import collections
import itertools
import json
import math
import os
import sys
import time

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))
TOL, DEEP, WIN, NEAR = 0.3, 0.3, 4.0, 1.2
HOLD = 1.0  # s: gaps in a signal shorter than this are bridged
STEP = 0.2  # s
RELIABLE = {"track", "both", "handoff", "into_unseen"}


def parse_time(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def hhmm(t: float) -> str:
    return time.strftime("%d. %H:%M", time.localtime(t))


def frames(recordings: str, t0: float, t1: float, pauses=None):
    """(t, sensor id, payload) of the frames in [t0, t1]; pauses (pause.Pauses): follows the switch
    "Lernen pausieren" in the recordings."""
    from presence_tracker.pause import follow
    h = t0 - t0 % 3600
    while h <= t1:
        path = os.path.join(recordings, time.strftime("%Y%m%d-%H", time.localtime(h)) + ".jsonl")
        h += 3600
        if not os.path.exists(path):
            continue
        for line in open(path):
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if pauses is not None and follow(pauses, m):
                continue
            if not m["topic"].endswith("/frame") or m["t"] < t0:
                continue
            if m["t"] > t1:
                return
            yield m["t"], m["topic"].split("/")[1], m["payload"]


# ------------------------------------------------------------------ truth: door crossings

class Plan:
    """Rooms (a raster of room ids) and doors (centre, along, normal, width, the rooms on both sides)
    of a configuration; dividers count as doors."""

    def __init__(self, config):
        from presence_tracker.floorplan import wall_pieces
        from presence_tracker.geometry import distance_to_segment
        from presence_tracker.world import World
        self.config = config
        w = self.world = World(config)
        self.rooms = [z.id for z in config.zones_of("room")]
        self.raster = np.full((w.nx, w.ny), -1, dtype=np.int16)
        for k, z in enumerate(config.zones_of("room")):
            self.raster[w.zone_mask(z) & (self.raster < 0)] = k
        pieces = wall_pieces(config.walls)
        walls = [p for p in pieces if p[2] == "wall"]
        self.doors = []
        for d in config.doors:
            c = np.array([d["x"], d["y"]])
            piece = min(walls, key=lambda p: distance_to_segment(c[0], c[1], p[0], p[1]), default=None)
            if piece is not None:
                self._add(str(d["id"]), c, piece, float(d.get("width", 0.9)))
        for k, piece in enumerate(p for p in pieces if p[2] == "divider"):
            (ax, ay), (bx, by), _ = piece
            self._add(f"divider{k}", np.array([(ax + bx) / 2, (ay + by) / 2]), piece, math.hypot(bx - ax, by - ay))

    def _add(self, did, c, piece, width):
        (ax, ay), (bx, by), _ = piece
        u = np.array([bx - ax, by - ay], dtype=float)
        u /= max(np.linalg.norm(u), 1e-9)
        n = np.array([-u[1], u[0]])
        pos, neg = (self.room_at(c + s * 0.3 * n) for s in (1, -1))
        self.doors.append({"id": did, "c": c, "u": u, "n": n, "w": width, "pos": pos, "neg": neg})

    def room_at(self, p) -> str:
        i, j = self.world.cell_of(np.asarray(p, dtype=float).reshape(1, 2))
        k = int(self.raster[i[0], j[0]])
        return self.rooms[k] if k >= 0 else ""


def track_points(config, recordings, t0, t1):
    """The LD2450 tracks' measured points in the world of config: rows (t, sensor index, seg, x, y)."""
    from presence_tracker.frames import SensorClock, detections
    from presence_tracker.sensortracks import SensorTracks
    sens = {s.id: s for s in config.sensors}
    sidx = {s.id: i for i, s in enumerate(config.sensors)}
    ids = itertools.count(1)
    tracks = {s: SensorTracks(s, ids) for s in sens}
    clocks = collections.defaultdict(SensorClock)
    rows = []
    for t_rec, sid, p in frames(recordings, t0, t1):
        if sid not in sens:
            continue
        t = clocks[sid](t_rec, p.get("uptime_ms"))
        ev = tracks[sid].update(t, p, detections(config, sens[sid], p))
        for seg, d in ev.born + ev.measured:
            rows.append((t, sidx[sid], seg, d.pos[0], d.pos[1]))
    return np.array(rows, dtype=float).reshape(-1, 5)


def crossings(plan, rows, sensors):
    """The door crossings (module doc) from the track points."""
    R = rows[np.lexsort((rows[:, 0], rows[:, 2]))]
    segs = np.split(R, np.flatnonzero(np.diff(R[:, 2])) + 1) if len(R) else []
    spos = {i: np.array([s.x, s.y]) for i, s in enumerate(plan.config.sensors)}

    def side(d, P):
        q = P - d["c"]
        return q @ d["n"], q @ d["u"]

    events, info = [], []
    for S in segs:
        t, P = S[:, 0], S[:, 3:5]
        sensor, seg = int(S[0, 1]), int(S[0, 2])
        info.append((seg, sensor, t[0], t[-1], P[:3].mean(0), P[-3:].mean(0)))
        if len(S) < 3:
            continue
        for d in plan.doors:
            s, a = side(d, P)
            for i in np.flatnonzero(np.sign(s[:-1]) != np.sign(s[1:])):
                f = s[i] / (s[i] - s[i + 1]) if s[i] != s[i + 1] else 0.5
                along = a[i] + f * (a[i + 1] - a[i])
                if abs(along) > d["w"] / 2 + TOL or abs(s[i] - s[i + 1]) > 1.0:
                    continue
                tc = t[i] + f * (t[i + 1] - t[i])
                sg = np.sign(s[i + 1])
                before, after = (t >= tc - WIN) & (t <= tc), (t >= tc) & (t <= tc + WIN)
                if not (np.any(-sg * s[before] >= DEEP) and np.any(sg * s[after] >= DEEP)):
                    continue
                k = (t >= tc - 1) & (t <= tc + 1)
                v = np.polyfit(t[k] - tc, P[k], 1)[0] if k.sum() >= 3 else np.array([np.nan, np.nan])
                events.append({"t": float(tc), "door": d["id"], "from": d["neg"] if sg > 0 else d["pos"],
                               "to": d["pos"] if sg > 0 else d["neg"], "how": "track", "sensors": [sensor],
                               "segs": [seg], "speed": float(np.hypot(*v)), "along": float(along)})
    starts = sorted(info, key=lambda x: x[2])
    st_t = np.array([x[2] for x in starts])
    for seg, sensor, t0, t1, pa, pz in info:
        for d in plan.doors:
            if d["id"].startswith("divider"):
                continue
            s1, a1 = (float(x[0]) for x in side(d, pz[None, :]))
            if abs(s1) > NEAR or abs(a1) > d["w"] / 2 + 0.5:
                continue
            lo, hi = np.searchsorted(st_t, [t1 - 1.0, t1 + 3.0])
            for seg2, sensor2, u0, u1, qa, qz in starts[lo:hi]:
                if sensor2 == sensor or seg2 == seg:
                    continue
                s2, a2 = (float(x[0]) for x in side(d, qa[None, :]))
                if np.sign(s2) == np.sign(s1) or abs(s2) > NEAR or abs(a2) > d["w"] / 2 + 0.5 or u1 - u0 < 1.0:
                    continue
                if abs(float(side(d, qz[None, :])[0][0])) < abs(s2) + 0.2:
                    continue
                sg = np.sign(s2)
                events.append({"t": float(t1 + (u0 - t1) * abs(s1) / (abs(s1) + abs(s2))), "door": d["id"],
                               "from": d["neg"] if sg > 0 else d["pos"], "to": d["pos"] if sg > 0 else d["neg"],
                               "how": "handoff", "sensors": [sensor, sensor2], "segs": [seg, seg2],
                               "speed": float("nan"), "along": float((a1 + a2) / 2)})
    unseen = []
    for S in segs:
        if len(S) < 6 or S[-1, 0] - S[0, 0] < 1.5:
            continue
        sensor, seg = int(S[0, 1]), int(S[0, 2])
        t, P = S[:, 0], S[:, 3:5]
        for first in (True, False):
            k = (t <= t[0] + 1.0) if first else (t >= t[-1] - 1.0)
            if k.sum() < 4 or t[k][-1] - t[k][0] < 0.4:
                continue
            v = np.polyfit(t[k] - t[k][0], P[k], 1)[0]
            p, te = (P[k][0], t[0]) if first else (P[k][-1], t[-1])
            sp = float(np.hypot(*v))
            if sp < 0.25:
                continue
            for d in plan.doors:
                if d["id"].startswith("divider") or np.linalg.norm(spos[sensor] - d["c"]) > 4.5:
                    continue
                s, a = (float(x[0]) for x in side(d, p[None, :]))
                if abs(s) > NEAR or abs(a) > d["w"] / 2 + 0.4:
                    continue
                vn = float(v @ d["n"])
                towards = -np.sign(s) * vn
                if (first and towards > -0.2) or (not first and towards < 0.2):
                    continue
                dt = abs(s) / max(abs(vn), 0.2)
                if dt > 2.5:
                    continue
                here, there = (d["pos"], d["neg"]) if s > 0 else (d["neg"], d["pos"])
                unseen.append({"t": float(te - dt if first else te + dt), "door": d["id"],
                               "from": there if first else here, "to": here if first else there,
                               "how": "from_unseen" if first else "into_unseen", "sensors": [sensor],
                               "segs": [seg], "speed": sp, "along": a})
    for u in unseen:
        if not any(abs(e["t"] - u["t"]) < 3 and e["door"] == u["door"] and e["to"] == u["to"] for e in events):
            events.append(u)
    events.sort(key=lambda e: e["t"])
    merged = []
    for e in events:
        hit = None
        for m in merged[::-1]:
            if e["t"] - m["t"] > 2.5:
                break
            if m["door"] == e["door"] and m["to"] == e["to"]:
                hit = m
                break
        if hit is None:
            merged.append({**e, "ts": [e["t"]]})
            continue
        hit["ts"].append(e["t"])
        hit["how"] = hit["how"] if hit["how"] == e["how"] else "both"
        hit["sensors"] = sorted(set(hit["sensors"]) | set(e["sensors"]))
        hit["segs"] = sorted(set(hit["segs"]) | set(e["segs"]))
        if np.isnan(hit["speed"]):
            hit["speed"] = e["speed"]
    for m in merged:
        m["t"] = float(np.median(m.pop("ts")))
    drop = set()
    for i, a in enumerate(merged):
        for j in range(i + 1, len(merged)):
            b = merged[j]
            if b["t"] - a["t"] > 1.5:
                break
            if a["from"] == b["from"] and a["to"] != b["to"]:
                ra, rb = a["how"] in ("track", "both"), b["how"] in ("track", "both")
                drop.update([j] if ra and not rb else [i] if rb and not ra else [i, j])
    return [m for k, m in enumerate(merged) if k not in drop and m["from"] and m["to"]]


# ------------------------------------------------------------------ replay

def replay(a, configs, starts, rooms):
    """Step the tracker through the recordings like the app; per step per room: P(occupied), its
    threshold decision, p_enter, approaching, and "Ziel" with its parts; and the pauses of learning."""
    from presence_tracker import pause
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    pauses = pause.load(None if a.no_pauses else a.pauses)
    t0, t1 = parse_time(a.__dict__["from"]), parse_time(a.to)
    R = len(rooms)
    cols = ("p", "occ", "pe", "app", "pt", "tgt", "qmap", "walks", "lam")
    log = {k: [] for k in ("t",) + cols}
    log["cpu"] = []
    tracker, learned, people = None, None, None
    clocks = collections.defaultdict(SensorClock)
    starts = sorted(s for s in starts if t0 < s <= t1) + [math.inf]
    nxt_start, nstep = 0, 0.0
    cfg_of = lambda t: [c for tc, c in configs if tc <= t][-1]
    walks0, cpu_targets = 0.0, 0.0
    t_cpu = time.process_time()
    for t_rec, sid, payload in frames(a.recordings, t0, t1, None if a.no_pauses else pauses):
        if tracker is None or t_rec >= starts[nxt_start]:
            if tracker is not None:
                learned = (tracker.ghost_map, tracker.ld_background, tracker.dest_map)
                people = json.loads(json.dumps(tracker.people_state()))
                nxt_start += 1
            tracker = Tracker(cfg_of(t_rec))
            if learned is not None:
                tracker.use_ghost_map(learned[0])
                tracker.use_ld_background(learned[1])
                tracker.use_dest_map(learned[2])
            elif a.learned:
                tracker.load_learned(json.load(open(a.learned)))
            if people is not None:
                tracker.restore_people(people)
            tracker.learn_dest = not a.no_learning
            tracker.pauses = pauses
            clocks.clear()
            print(f"{hhmm(t_rec)} start, destination map {tracker.dest_map.walks:.0f} walks", flush=True)
        cfg = cfg_of(t_rec)
        if cfg is not tracker.config:
            print(f"{hhmm(t_rec)} new configuration, start over: {tracker.reconfigure(cfg)}", flush=True)
        tt = clocks[sid](t_rec, payload.get("uptime_ms"))
        tracker.process_frame(sid, tt, payload)
        if tracker.start is None or tt < nstep:
            continue
        tracker.step(tt)
        nstep = tt + STEP
        tracker.entering()  # computed for "wird betreten" anyway
        c0 = time.process_time()
        tracker.targets()
        cpu_targets = time.process_time() - c0
        st = tracker.zone_states()
        row = {k: np.full(R, np.nan) for k in cols}
        for j, z in enumerate(rooms):
            s = st.get(z)
            if s is None or s.probability is None or z not in tracker.rooms:
                continue
            e = tracker.targets().get(z)
            row["p"][j], row["occ"][j] = s.probability, float(s.occupied)
            row["pe"][j], row["app"][j] = s.p_enter or 0.0, float(s.approaching)
            if e is not None:
                row["pt"][j], row["tgt"][j] = e["p"], float(e["on"])
                row["qmap"][j], row["walks"][j], row["lam"][j] = e["map"], e["walks"], e["weight"]
        log["t"].append(tt)
        for k in cols:
            log[k].append(row[k])
        log["cpu"].append(cpu_targets)
        if len(log["t"]) % 18000 == 0:
            print(f"{hhmm(tt)} {len(log['t'])} steps, map {tracker.dest_map.walks:.0f} walks, CPU {time.process_time() - t_cpu:.0f} s",
                  flush=True)
    out = {k: np.array(v, dtype=float) for k, v in log.items()}
    out["rooms"] = np.array(rooms)
    p = tracker.p
    out["params"] = np.array([p.approach_cost / (p.approach_cost + 1), p.light_cost / (p.light_cost + 1), p.lead_time,
                              tracker.m.dest_prior_walks])
    out["thresholds"] = np.array([tracker.threshold(z) for z in rooms])
    out["map_walks"] = np.array([tracker.dest_map.walks])
    out["pauses"] = np.array([[s, np.inf if e is None else e] for s, e in pauses.between(-math.inf)], dtype=float).reshape(-1, 2)
    if a.save_learned:
        json.dump(tracker.learned(), open(a.save_learned, "w"))
    print(f"replayed, CPU {time.process_time() - t_cpu:.0f} s", flush=True)
    return out


def decide(log, prior, thresholds, c_v):
    """ "Ziel" again from what was logged, for another prior weight or thresholds (filter.Tracker.targets)."""
    pe, qmap, n = log["pe"], log["qmap"], log["walks"]
    c = np.clip(thresholds[None, :], 1e-6, 1 - 1e-6)
    lam = np.where(n > 0, n / (n + prior), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        o = pe / (1 - pe) * (c / (1 - c)) / (c_v / (1 - c_v))
        qm = np.where(pe <= 0, 0.0, np.where(pe >= 1, 1.0, o / (1 + o)))
    prob = np.where(lam > 0, (1 - lam) * qm + lam * qmap, qm)
    on = np.where(lam > 0, prob >= c, pe > c_v)
    return np.where(np.isnan(pe), np.nan, prob), np.where(np.isnan(pe), np.nan, on), lam


# ------------------------------------------------------------------ scoring

def runs(sig):
    s = np.concatenate([[False], sig, [False]]).astype(int)
    d = np.diff(s)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def bridge(sig, t, hold=HOLD):
    out = sig.copy()
    rr = runs(sig)
    for (a0, a1), (b0, _) in zip(rr[:-1], rr[1:]):
        if t[b0] - t[a1 - 1] < hold:
            out[a1:b0] = True
    return out


def score(log, cross, rows, plan, signals, starts, warmup, lead_time, window=None):
    t = log["t"]
    rooms = list(log["rooms"])
    observed = ~np.isnan(log["p"])  # (N, R): the model observed the room then
    valid = np.ones(len(t), bool)
    for s in starts:
        valid &= ~((t >= s) & (t < s + warmup))
    if window is not None:  # only this period is scored
        valid &= (t >= window[0]) & (t < window[1])
    for s, e in log.get("pauses", np.zeros((0, 2))):  # learning paused (background activity): not scored
        valid &= ~((t >= s) & (t < e))
    gap = np.diff(t, prepend=t[0])
    hours = float(np.sum(gap[(gap <= 5) & valid])) / 3600
    base = {z: np.nan_to_num(log["occ"][:, j]) > 0.5 for j, z in enumerate(rooms)}
    any_entry = collections.defaultdict(list)
    for c in cross:
        any_entry[c["to"]].append(c["t"])
    any_entry = {k: np.array(sorted(v)) for k, v in any_entry.items()}
    idx = lambda tq: min(int(np.searchsorted(t, tq)), len(t) - 1)
    fresh = []
    unseen = collections.Counter()  # entries into a room before it had a sensor (not scored)
    for c in cross:
        if c["to"] not in rooms or c["how"] not in RELIABLE or not (t[0] + 60 < c["t"] < t[-1] - 30):
            continue
        i0, j = idx(c["t"]), rooms.index(c["to"])
        if valid[i0] and not observed[i0, j]:
            unseen[c["to"]] += 1
        if not (valid[i0] and valid[idx(c["t"] - 20)] and observed[i0, j]):
            continue
        a, b = np.searchsorted(t, [c["t"] - 6, c["t"] - 4])
        if not base[c["to"]][a:b].any():
            fresh.append(c)
    door = {d["id"]: d for d in plan.doors}
    order = np.lexsort((rows[:, 0], rows[:, 2]))
    rows = rows[order]
    seg_lo = {}
    if len(rows):
        sg = rows[:, 2]
        cut = np.flatnonzero(np.diff(sg)) + 1
        for a, b in zip(np.r_[0, cut], np.r_[cut, len(sg)]):
            seg_lo[int(sg[a])] = (a, b)

    def lead_of(s, c):
        i0 = idx(c["t"])
        if s[i0]:
            j = i0
            while j > 0 and s[j - 1] and t[i0] - t[j - 1] < 60:
                j -= 1
            return c["t"] - t[j]
        k = np.flatnonzero(s[i0:np.searchsorted(t, c["t"] + 30)])
        return c["t"] - t[i0 + k[0]] if len(k) else -np.inf

    def dist_at(c, lead):
        if not np.isfinite(lead) or lead < 0:
            return -1.0
        tq = c["t"] - lead
        cp = door[c["door"]]["c"] + door[c["door"]]["u"] * c["along"]
        for s in c["segs"]:
            if s in seg_lo:
                a, b = seg_lo[s]
                k = a + int(np.argmin(np.abs(rows[a:b, 0] - tq)))
                if abs(rows[k, 0] - tq) <= 0.25:
                    return float(np.hypot(*(rows[k, 3:5] - cp)))
        sp = c["speed"] if np.isfinite(c.get("speed", np.nan)) and c["speed"] > 0 else 0.85
        return lead * sp

    res = {}
    for name, (sig, prob) in signals.items():
        lit = {z: bridge(base[z] | sig[z], t) for z in rooms}
        leads = np.array([lead_of(lit[c["to"]], c) for c in fresh])
        dists = np.array([dist_at(c, l) for c, l in zip(fresh, leads)])
        false = []  # (t, room, length)
        for j, z in enumerate(rooms):
            spec = lit[z] & ~base[z] & valid & observed[:, j]
            ae = any_entry.get(z, np.array([]))
            for a, b in runs(spec):
                ta, tb = t[a], t[b - 1] + STEP
                hi = max(ta + lead_time, tb) + 3.0
                if np.any((ae >= ta - 0.5) & (ae <= hi)) or base[z][a:np.searchsorted(t, hi)].any():
                    continue
                false.append((ta, z, tb - ta))
        # calibration: the probability while the room is dark against an entry within 5 s
        cal = []
        for j, z in enumerate(rooms):
            pz = np.nan_to_num(prob[z])
            ae = any_entry.get(z, np.array([]))
            sel = np.flatnonzero((pz >= 0.05) & ~base[z] & valid & observed[:, j])
            k = np.searchsorted(ae, t[sel] - 0.2)
            nxt = np.where(k < len(ae), ae[np.minimum(k, max(len(ae) - 1, 0))] if len(ae) else np.inf, np.inf)
            cal.append((pz[sel], nxt <= t[sel] + 5.0))
        res[name] = {"leads": leads, "dists": dists, "false": false,
                     "cal": (np.concatenate([c[0] for c in cal]), np.concatenate([c[1] for c in cal]))}
    return fresh, res, hours, valid, unseen


def report(log, cross, rows, plan, a, starts):
    rooms = list(log["rooms"])
    c_v, _, lead_time, prior0 = log["params"]
    thr = log["thresholds"].copy()
    if a.threshold is not None:
        thr[:] = a.threshold
    for item in a.room_threshold:
        z, v = item.split("=")
        thr[rooms.index(z)] = float(v)
    prior = a.prior if a.prior is not None else prior0
    if a.prior is not None or a.threshold is not None or a.room_threshold:
        prob, on, _ = decide(log, prior, thr, c_v)
    else:
        prob, on = log["pt"], log["tgt"]
    col = lambda arr: {z: np.nan_to_num(arr[:, j]) > 0.5 for j, z in enumerate(rooms)}
    pcol = lambda arr: {z: arr[:, j] for j, z in enumerate(rooms)}
    signals = {"wird betreten": (col(log["app"]), pcol(log["pe"])), "Ziel": (col(on), pcol(prob))}
    window = (parse_time(a.score_from) if a.score_from else -math.inf, parse_time(a.score_to) if a.score_to else math.inf)
    fresh, res, hours, valid, unseen = score(log, cross, rows, plan, signals, starts, a.warmup, lead_time,
                                             window if a.score_from or a.score_to else None)
    t = log["t"]
    print(f"\n{hhmm(t[0])} - {hhmm(t[-1])}: {hours:.1f} h scored; {len(fresh)} entries into dark rooms "
          f"(lead_time {lead_time:g} s, approach threshold {c_v:.3f}, prior {prior:g} walks, thresholds "
          + ", ".join(f"{z} {c:g}" for z, c in zip(rooms, thr)) + ")")

    def line(name, sel=None, room=None):
        r = res[name]
        L, D = r["leads"], r["dists"]
        if sel is not None:
            L, D = L[sel], D[sel]
        fl = [f for f in r["false"] if room is None or f[1] == room]
        n = len(L)
        pct = lambda m: f"{100 * np.mean(m):3.0f}" if n else "  -"
        fin = L[np.isfinite(L)]
        return (f"n {n:3d} | lead >= 0.5/1/2 s {pct(L >= 0.5)}/{pct(L >= 1)}/{pct(L >= 2)} % | >= 1/2 m {pct(D >= 1)}/{pct(D >= 2)} % | "
                f"median {np.median(fin) if len(fin) else np.nan:5.2f} s | false {len(fl) / max(hours, 1e-9):5.2f}/h, "
                f"mean {np.mean([f[2] for f in fl]) if fl else 0:4.1f} s")
    for name in signals:
        print(f"  {name:14s} {line(name)}")
    if unseen:
        print("not scored: entries into rooms the model did not observe then (a sensor came later; inferred from a "
              "track ending at the door): " + ", ".join(f"{z} {n}" for z, n in sorted(unseen.items())))
    # where the map knows nothing for the walkers (weight 0), "Ziel" is "wird betreten" (MODEL.md 6)
    obs = ~np.isnan(log["pe"])
    _, _, lam = decide(log, prior, thr, c_v)
    blind = obs & (np.nan_to_num(lam) == 0)
    differ = blind & ((np.nan_to_num(on) > 0.5) != (np.nan_to_num(log["app"]) > 0.5))
    print(f"\nroom-steps where the map's weight is 0: {blind.sum()} of {obs.sum()}; "
          f"Ziel differs from wird betreten in {differ.sum()} of them")
    print("\nper room (entries into it; false ons there):")
    for z in rooms:
        sel = np.array([c["to"] == z for c in fresh], dtype=bool)
        for name in signals:
            print(f"  {z:14s} {name:14s} {line(name, sel, z)}")
    print("\nper door (lead >= 1 s / >= 2 s):")
    doors = collections.Counter(f"{c['from']}->{c['to']}" for c in fresh)
    for k, n in doors.most_common():
        sel = np.array([f"{c['from']}->{c['to']}" == k for c in fresh], dtype=bool)
        print(f"  {k:28s} n {n:3d} | " + " | ".join(
            f"{name} {100 * np.mean(res[name]['leads'][sel] >= 1):3.0f} / {100 * np.mean(res[name]['leads'][sel] >= 2):3.0f} %"
            for name in signals))
    print("\ncalibration: P in bin while the room is dark -> entry within 5 s (share, n)")
    bins = [0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 1.01]
    for name in signals:
        p, y = res[name]["cal"]
        b = np.digitize(p, bins) - 1
        print(f"  {name:14s} " + " ".join(f"[{bins[q]:.2f},{min(bins[q + 1], 1):.2f}) {100 * y[b == q].mean() if (b == q).any() else 0:3.0f}% ({int((b == q).sum())})"
                                         for q in range(len(bins) - 1)))
    if a.hourly:
        print("\nper hour since the start (learning curve): entries, lead >= 1 s / >= 2 s / >= 1 m, false ons")
        t0 = t[0]
        h_of = lambda tq: int((tq - t0) // 3600)
        H = h_of(t[-1]) + 1
        ent_h = np.array([h_of(c["t"]) for c in fresh])
        gap = np.diff(t, prepend=t[0])
        hrs = np.bincount(np.array([h_of(x) for x in t]), weights=np.where((gap <= 5) & valid, gap, 0) / 3600, minlength=H)
        lam = np.nanmax(np.where(np.isnan(log["lam"]), 0, log["lam"]), axis=1)
        for h in range(H):
            sel = ent_h == h
            in_h = (t >= t0 + 3600 * h) & (t < t0 + 3600 * (h + 1))
            parts = []
            for name in signals:
                L, D = res[name]["leads"][sel], res[name]["dists"][sel]
                nf = sum(1 for f in res[name]["false"] if h_of(f[0]) == h)
                pc = lambda m: 100 * np.mean(m) if len(m) else 0
                parts.append(f"{name} {pc(L >= 1):3.0f}/{pc(L >= 2):3.0f}/{pc(D >= 1):3.0f} %, "
                             f"{nf / max(hrs[h], 1e-9):5.2f}/h")
            busy = lam[in_h & (lam > 0)]
            print(f"  {h:3d} {hhmm(t0 + 3600 * h)} {hrs[h]:4.2f} h, {sel.sum():3d} entries | " + " | ".join(parts)
                  + f" | map weight when asked: median {np.median(busy) if len(busy) else 0:.2f}")
    if "cpu" in log and len(log["cpu"]):
        print(f"\nCPU of Tracker.targets per step: mean {1000 * np.mean(log['cpu']):.3f} ms, p99 {1000 * np.percentile(log['cpu'], 99):.2f} ms")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--config", required=True, help="the configuration (from the start)")
    ap.add_argument("--config-at", action="append", default=[], help='"YYYY-mm-dd HH:MM:SS=FILE": from then on')
    ap.add_argument("--start", action="append", default=[], help="a start of the app (a new model)")
    ap.add_argument("--truth-config", help="the configuration the tracks are placed with (default: the last one)")
    ap.add_argument("--recordings", default=os.path.join(TOOLS, "..", "recordings"))
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "tracker"))
    ap.add_argument("--learned", help="what was learned (the app's learned state, e.g. from an error report)")
    ap.add_argument("--save-learned", help="write what was learned at the end here")
    ap.add_argument("--no-learning", action="store_true", help="the destination map learns nothing (stays as loaded)")
    ap.add_argument("--log", help="write the replay's log here (npz)")
    ap.add_argument("--from-log", help="score this log instead of replaying")
    ap.add_argument("--truth-cache", help="crossings and track points: read from here if it exists, else written (npz)")
    ap.add_argument("--prior", type=float, help="the map's prior weight in walks (recomputes Ziel)")
    ap.add_argument("--threshold", type=float, help="the threshold of Ziel in all rooms (recomputes Ziel)")
    ap.add_argument("--room-threshold", action="append", default=[], help="ROOM=C (recomputes Ziel)")
    ap.add_argument("--pauses", help="intervals in which learning was paused (tools/vacuum_history.py)")
    ap.add_argument("--no-pauses", action="store_true", help="learn everywhere, score everywhere")
    ap.add_argument("--warmup", type=float, default=60.0, help="s after each start not scored")
    ap.add_argument("--hourly", action="store_true", help="the learning curve per hour")
    ap.add_argument("--score-from", help="score only from this time on (YYYY-mm-dd HH:MM:SS); the replay is the same")
    ap.add_argument("--score-to", help="score only up to this time")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker.model import Config
    t0, t1 = parse_time(a.__dict__["from"]), parse_time(a.to)
    configs = [(-math.inf, Config.from_dict(json.load(open(a.config))))]
    for item in a.config_at:
        when, path = item.split("=", 1)
        configs.append((parse_time(when), Config.from_dict(json.load(open(path)))))
    configs.sort(key=lambda x: x[0])
    starts = [t0] + [parse_time(s) for s in a.start]
    truth_cfg = Config.from_dict(json.load(open(a.truth_config))) if a.truth_config else configs[-1][1]
    plan = Plan(truth_cfg)
    if a.truth_cache and os.path.exists(a.truth_cache):
        tc = np.load(a.truth_cache, allow_pickle=False)
        rows, cross = tc["rows"], json.loads(str(tc["cross"]))
    else:
        rows = track_points(truth_cfg, a.recordings, t0 - 30, t1 + 30)
        cross = crossings(plan, rows, truth_cfg.sensors)
        if a.truth_cache:
            np.savez_compressed(a.truth_cache, rows=rows, cross=json.dumps(cross))
    print(f"{len(cross)} door crossings, " + ", ".join(f"{k} {v}" for k, v in collections.Counter(c["how"] for c in cross).items()))
    if a.from_log:
        log = dict(np.load(a.from_log))
        if a.no_pauses:
            log.pop("pauses", None)
        elif a.pauses:  # a log of before, or more pauses
            from presence_tracker import pause
            ps = pause.load(a.pauses)
            for s0, e0 in log.get("pauses", np.zeros((0, 2))):
                ps.add(float(s0), None if np.isinf(e0) else float(e0))
            log["pauses"] = np.array([[s0, np.inf if e0 is None else e0] for s0, e0 in ps.between(-math.inf)], dtype=float).reshape(-1, 2)
    else:
        rooms = [z.id for z in truth_cfg.zones_of("room") if not z.entry]
        log = replay(a, configs, starts, rooms)
        if a.log:
            np.savez_compressed(a.log, **log)
    report(log, cross, rows, plan, a, starts)


if __name__ == "__main__":
    main()
