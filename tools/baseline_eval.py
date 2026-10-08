"""Simple baselines for room occupancy against the filter, scored like tools/report_eval.py on the truth
from error reports, plus the light the Node-RED rule would make of each (on at occupancy, off after
RUN_ON s of "not occupied").

Baselines (no model, per observed room, on a grid of 1 s):
  B1  raw LD2450 + hold: occupied if an LD2450 target lies in the room polygon within the last H s
      (world coordinates with the configuration's poses, behind-a-wall / outside-all-rooms / at the
      mount dropped as in frames.py). "m": measured targets only (sensortracks.py: not coasted or
      frozen), "r": also the held ones (what the raw frames show); "..d": only targets at least DEEP
      inside the room (not at its walls, where a position error puts a person into the next room),
      "..o": only the room's own sensor (the one named like the room).
  B2  B1 or the room's own LD2410C within the last H s: "f" its presence flags (moving or still),
      "e" its energies: a ring within the room's reach at 3x its background or more (mean over 1 s;
      the background per sensor and ring the median of the seconds without an LD2450 target in the
      sensor's room for 60 s on both sides; 3x as in MODEL.md 4.3, echo sources).
  B3  B1 (measured) with a latch: occupied from a measured target in the room until a track leaves
      through a door of the room (a door crossing of tools/entries.py out of it, and no other target
      in the room within 3 s after it), at most HMAX s after the last target.
  F   the filter: P(occupied) > light_cost / (light_cost + 1) (MODEL.md 6), replayed the way the app
      ran it (as tools/report_eval.py: a fresh model at each app start, the learned maps carried on,
      the people's state restored), cached in --filter-cache; "F.5": P(occupied) > 0.5 (equal costs).
  Hybrids: F or a baseline ("F|B1m5": the filter, never off while the LD2450 measured somebody in
      the room in the last 5 s).

Scored per truth window and observed room as report_eval (samples every 1 s; a window with "walks"
excuses the seconds after an LD2450 measured somebody there; "score": false and windows in a pause
are skipped): the share of the window in which the decision is wrong, summed over the empty and the
occupied room-windows; and in minutes. The light: the decision with the run-on, minutes lit in empty
rooms and dark with somebody there (with "walks" the run-on after a walk is excused too; in an empty
window only the light from a decision within it counts: what was on before may have been right).

usage: python tools/baseline_eval.py --config FILE --truth FILE [--config-at "YYYY-mm-dd HH:MM:SS=FILE"]...
           [--recordings DIR] [--pauses FILE] [--filter-cache FILE.npz] [--data-cache FILE.npz]
           [--json OUT] [--episodes]
--filter-cache: the filter's P(occupied) per room and second; replayed (minutes of CPU) if the file
does not exist. --data-cache: the extracted LD2450 targets and LD2410C seconds. --episodes: list the
stretches in which the filter and B1 disagree within the scored windows. --json: the numbers per window,
and every decision per second in OUT_dec.npz (for further analysis).
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
RUN_ON = 120  # s: the Node-RED rule switches off after this long without occupancy
HOLDS = (2, 5, 10, 30, 60)
HMAX = (300, 600, 1800, 3600)
MOUNT = 0.3  # m (filter.MOUNT_RADIUS)
DEEP = 0.3  # m: "d" variants count a target only this far inside the room (not at its walls)


def parse_time(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def hms(t: float) -> str:
    return time.strftime("%d. %H:%M:%S", time.localtime(t))


def messages(recordings, t0, t1):
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
            if m["t"] > t1:
                return
            yield m


# ------------------------------------------------------------------ the data: LD2450 targets, LD2410C

def extract(a, config_of, rooms, g0, T, pauses):
    """LD2450 targets in the observed rooms: rows (t, room, measured, sensor, seg, x, y), seg the
    sensor track (sensortracks.py) or -1; and per sensor and second the LD2410C flags and mean
    energies (moving 0..8, still 2..8)."""
    from presence_tracker.frames import detections
    from presence_tracker.sensortracks import SensorTracks
    from presence_tracker import pause
    cfg0 = config_of(g0)
    sids = [s.id for s in cfg0.sensors]
    ids = itertools.count(1)
    tracks = {sid: SensorTracks(sid, ids) for sid in sids}
    zones = {}
    rows = []
    S = len(sids)
    flag = np.zeros((S, T, 2), dtype=bool)
    esum = np.zeros((S, T, 16), dtype=np.float32)
    n = np.zeros((S, T), dtype=np.int32)
    t_cpu = time.process_time()
    for m in messages(a.recordings, g0, g0 + T):
        if pause.follow(pauses, m) or not m["topic"].endswith("/frame") or m["t"] < g0:
            continue
        sid = m["topic"].split("/")[1]
        if sid not in tracks:
            continue
        cfg = config_of(m["t"])
        if id(cfg) not in zones:
            zones[id(cfg)] = [z for z in cfg.zones_of("room") if z.id in rooms]
        s = cfg.sensor_by_id.get(sid)
        if s is None or not s.enabled or not s.placed:
            continue
        p = m["payload"]
        dets = detections(cfg, s, p)
        for d in dets:
            if math.hypot(d.pos[0] - s.x, d.pos[1] - s.y) < MOUNT:
                d.hidden = True
        ev = tracks[sid].update(m["t"], p, dets)
        measured = {id(d): seg for seg, d in ev.born + ev.measured}
        held = {}
        for seg, kind, d in ev.lost:
            held[id(d)] = seg
        for seg, d in ev.held:
            held[id(d)] = seg
        si = sids.index(sid)
        for d in dets:
            if d.hidden:
                continue
            z = next((k for k, z in enumerate(zones[id(cfg)]) if z.contains(float(d.pos[0]), float(d.pos[1]))), None)
            if z is None:
                continue
            room = rooms.index(zones[id(cfg)][z].id)
            meas = id(d) in measured
            seg = measured.get(id(d), held.get(id(d), -1))
            rows.append((m["t"], room, meas, si, seg, float(d.pos[0]), float(d.pos[1])))
        ld = p.get("ld2410")
        if ld:
            k = int(m["t"] - g0)
            if 0 <= k < T:
                flag[si, k, 0] |= bool(ld.get("moving"))
                flag[si, k, 1] |= bool(ld.get("still"))
                mg, sg = ld.get("move_gates"), ld.get("still_gates")
                if mg and sg and len(mg) >= 9 and len(sg) >= 9:
                    esum[si, k, :9] += mg[:9]
                    esum[si, k, 9:] += sg[2:9]
                    n[si, k] += 1
    print(f"extracted {len(rows)} targets in rooms, CPU {time.process_time() - t_cpu:.0f} s", file=sys.stderr)
    e = np.where(n[..., None] > 0, esum / np.maximum(n, 1)[..., None], np.nan).astype(np.float32)
    return np.array(rows, dtype=float).reshape(-1, 7), flag, e, np.array(sids)


# ------------------------------------------------------------------ the filter

def filter_series(a, config_of, configs, truth, rooms, g0, T, pauses):
    """P(occupied) per room and second (sampled at the first frame of each second), the replay of
    tools/report_eval.py."""
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker import pause
    starts = sorted(parse_time(s) for s in truth["app_starts"])
    fresh = {parse_time(s) for s in truth.get("fresh_starts", [])}
    P = np.full((T, len(rooms)), np.nan, dtype=np.float32)
    tracker = None
    gm = ldb = dm = None
    clocks = collections.defaultdict(SensorClock)
    next_start, next_step, last_k = 0, 0.0, -1
    t_cpu = time.process_time()
    for m in messages(a.recordings, starts[0], g0 + T):
        if pause.follow(pauses, m):
            continue
        if not m["topic"].endswith("/frame") or m["t"] < starts[0]:
            continue
        while next_start < len(starts) and m["t"] >= starts[next_start]:
            people = None
            if tracker is not None:
                gm, ldb = tracker.ghost_map, getattr(tracker, "ld_background", None)
                dm = getattr(tracker, "dest_map", None)
                people = None if starts[next_start] in fresh else json.loads(json.dumps(tracker.people_state()))
            tracker = Tracker(config_of(m["t"]))
            tracker.pauses = pauses
            if gm is not None:
                tracker.use_ghost_map(gm)
            if ldb is not None:
                tracker.use_ld_background(ldb)
            if dm is not None:
                tracker.use_dest_map(dm)
            if people is not None and not tracker.restore_people(people):
                print("the saved people do not fit: nothing known", file=sys.stderr)
            clocks.clear()
            next_start += 1
        if config_of(m["t"]) is not tracker.config:
            tracker.reconfigure(config_of(m["t"]))
        sid = m["topic"].split("/")[1]
        tt = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
        tracker.process_frame(sid, tt, m["payload"])
        if tt >= next_step:
            tracker.step(tt)
            next_step = tt + 0.2
        k = int(m["t"] - g0)
        if k == last_k or not 0 <= k < T:
            continue
        counts = tracker.count_distribution()
        P[last_k + 1 if last_k >= 0 else k:k + 1] = [1 - counts[r][0] if r in counts else np.nan for r in rooms]
        last_k = k
        if k % 3600 == 0:
            print(f"filter {hms(m['t'])} CPU {time.process_time() - t_cpu:.0f} s", file=sys.stderr, flush=True)
    return P


# ------------------------------------------------------------------ the baselines

def hold(x, H):
    """x (T, R) bool: true where x was true within the last H samples (this one included)."""
    if H <= 1:
        return x.copy()
    c = np.concatenate([np.zeros((1, x.shape[1]), dtype=np.int64), np.cumsum(x, axis=0)])
    T = x.shape[0]
    lo = np.maximum(np.arange(1, T + 1) - H, 0)
    return (c[1:] - c[lo]) > 0


def baselines(rows, flag, e, sids, config, rooms, g0, T, plan, crossings_fn):
    R = len(rooms)
    k = np.floor(rows[:, 0] - g0).astype(int)
    ok = (k >= 0) & (k < T)
    seen = {}
    zone = {z.id: z for z in config.zones_of("room")}
    names = [s.name for s in config.sensors]  # the order of sids (the configuration's sensors)
    deep = np.array([zone[rooms[int(r)]].contains(x, y, -DEEP) for r, x, y in rows[:, [1, 5, 6]]], dtype=bool)
    own = np.array([names[int(si)] == rooms[int(r)] for r, si in rows[:, [1, 3]]], dtype=bool)
    meas = rows[:, 2] > 0
    for name, sel in (("m", meas), ("r", np.ones(len(rows), dtype=bool)), ("md", meas & deep), ("rd", deep),
                      ("mo", meas & own), ("ro", own)):
        x = np.zeros((T, R), dtype=bool)
        x[k[ok & sel], rows[ok & sel, 1].astype(int)] = True
        seen[name] = x
    out = {}
    for H in HOLDS:
        for name, x in seen.items():
            out[f"B1{name}{H}"] = hold(x, H)
    # the room's own LD2410C
    sens = {s.id: s for s in config.sensors}
    own = {}
    for r, rid in enumerate(rooms):
        cand = [i for i, sid in enumerate(sids) if sens.get(sid) is not None and sens[sid].name == rid]
        if cand:
            own[r] = cand[0]
    ldf = np.zeros((T, R), dtype=bool)
    lde = np.zeros((T, R), dtype=bool)
    busy = hold(seen["m"], 60)[::-1]
    busy = hold(busy, 60)[::-1]  # an LD2450 target in the room within 60 s on either side
    centre = np.concatenate([(np.arange(9) + 0.5) * 0.75, (np.arange(2, 9) + 0.5) * 0.75])
    bg_info = {}
    for r, si in own.items():
        s = sens[sids[si]]
        ldf[:, r] = flag[si, :, 0] | flag[si, :, 1]
        pts = zone[rooms[r]].geometry.points if zone[rooms[r]].geometry.points else []
        reach = max((math.hypot(px - s.x, py - s.y) for px, py in pts), default=6.0)
        reach = math.hypot(reach, s.height - 1.0) + 0.75
        cells = (centre <= reach)
        cells[:2] = False  # moving rings 0, 1: the sensor's own noise (MODEL.md 4.3)
        E = e[si]
        quiet = ~busy[:, r] & ~np.isnan(E[:, 0])
        b = np.nanmedian(E[quiet], axis=0) if quiet.sum() > 600 else np.full(16, np.nan)
        b = np.where(np.isfinite(b), np.maximum(b, 3.0), np.array([13, 9] + [4.5] * 7 + [5] * 7))
        bg_info[rooms[r]] = (b.round(1).tolist(), int(quiet.sum()))
        hot = (E[:, cells] >= 3 * b[cells]).any(axis=1)
        hot &= ~np.isnan(E[:, 0])
        # >= 3 s in a row (MODEL.md 4.3: echo events were counted from 3 s on)
        run = hot.copy()
        for d in (1, 2):
            run[d:] &= hot[:-d]
            run[:d] = False
        lde[:, r] = hold(run[:, None], 3)[:, 0]
    for H in HOLDS:
        out[f"B2f{H}"] = out[f"B1m{H}"] | hold(ldf, H)
        out[f"B2e{H}"] = out[f"B1m{H}"] | hold(lde, H)
    # B3: the latch
    exits = collections.defaultdict(list)
    for c in crossings_fn():
        if c["from"] in rooms:
            exits[rooms.index(c["from"])].append((c["t"], set(c["segs"])))
    meas = rows[rows[:, 2] > 0]
    clear = np.zeros((T, R), dtype=bool)
    for r in range(R):
        mr = meas[meas[:, 1] == r]
        for te, segs in exits.get(r, []):
            later = mr[(mr[:, 0] > te) & (mr[:, 0] <= te + 3)]
            kk = int(te - g0) + 1
            if (not len(later) or set(later[:, 4].astype(int)) <= segs) and 0 <= kk < T:
                clear[kk, r] = True
    sm = seen["m"]
    for Hm in HMAX:
        x = np.zeros((T, R), dtype=bool)
        for r in range(R):
            on, last = False, -1e9
            cl, col = clear[:, r], sm[:, r]
            for g in range(T):
                if cl[g]:
                    on = False
                elif col[g]:
                    on, last = True, g
                elif on and g - last > Hm:
                    on = False
                x[g, r] = on
        out[f"B3_{Hm // 60}min"] = x
    return out, bg_info


# ------------------------------------------------------------------ scoring

def score(dec, windows, rooms, g0, T, seen_m, pauses):
    """report_eval's numbers for one decision (T, R): wrongly on (sum of shares) of the empty
    room-windows, wrongly off of the occupied ones, the same in minutes; and the light with its run-on
    in minutes."""
    # the last second each room was occupied by this decision (for the light's run-on)
    T_, R_ = dec.shape
    idx = np.where(dec, np.arange(T_)[:, None], -10**9)
    last = np.maximum.accumulate(idx, axis=0)
    lit = last >= np.arange(T_)[:, None] - (RUN_ON - 1)
    res = dict(on=0.0, n_off=0, off=0.0, n_on=0, min_on=0.0, min_empty=0.0, min_off=0.0, min_occ=0.0,
               lit_empty=0.0, dark_occ=0.0)
    per = []
    for t0, t1, r in windows:
        if pauses.paused(t0, t1):
            continue
        lo, hi = int(math.ceil(t0 - g0)), int(math.floor(t1 - g0)) + 1
        lo, hi = max(lo, 0), min(hi, T)
        if hi <= lo:
            continue
        for room, n in r["rooms"].items():
            if room not in rooms:
                continue
            j = rooms.index(room)
            d, l_ = dec[lo:hi, j], lit[lo:hi, j]
            if n == 0:  # lit by an occupancy from before the window: maybe right (unknown), not counted
                l_ = l_ & (last[lo:hi, j] >= lo)
            keep = np.ones(hi - lo, dtype=bool)
            keep_l = keep.copy()
            if r.get("walks"):
                keep = ~hold(seen_m[:, j:j + 1], r["walks"])[lo:hi, 0]
                keep_l = ~hold(seen_m[:, j:j + 1], r["walks"] + RUN_ON)[lo:hi, 0]
            if not keep.any():
                continue
            wrong = (d != (n > 0))[keep]
            wl = (l_ != (n > 0))[keep_l]
            if n > 0:
                res["off"] += wrong.mean()
                res["n_on"] += 1
                res["min_off"] += wrong.sum() / 60
                res["min_occ"] += keep.sum() / 60
                res["dark_occ"] += wl.sum() / 60
            else:
                res["on"] += wrong.mean()
                res["n_off"] += 1
                res["min_on"] += wrong.sum() / 60
                res["min_empty"] += keep.sum() / 60
                res["lit_empty"] += wl.sum() / 60
            per.append((r["name"], room, n, float(wrong.mean()), float(wrong.sum() / 60), float(wl.sum() / 60)))
    return res, per


def runs_of(x):
    """[(start, end)) of the true stretches of a bool vector."""
    d = np.diff(np.concatenate([[0], x.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--config-at", action="append", default=[])
    ap.add_argument("--recordings", default=os.path.join(TOOLS, "..", "recordings"))
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "tracker"))
    ap.add_argument("--pauses")
    ap.add_argument("--filter-cache")
    ap.add_argument("--data-cache")
    ap.add_argument("--json")
    ap.add_argument("--episodes", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    sys.path.insert(0, TOOLS)
    from presence_tracker.model import Config
    from presence_tracker import pause

    config = Config.from_dict(json.load(open(a.config)))
    configs = [(-float("inf"), config)]
    for item in a.config_at:
        when, path = item.split("=", 1)
        configs.append((parse_time(when), Config.from_dict(json.load(open(path)))))
    configs.sort(key=lambda x: x[0])

    def config_of(t):
        return [c for tc, c in configs if tc <= t][-1]

    truth = json.load(open(a.truth))
    reports = [r for r in truth["reports"] if r.get("score", True)]
    windows = [(parse_time(r["from"]), parse_time(r["to"]), r) for r in reports]
    starts = sorted(parse_time(s) for s in truth["app_starts"])
    g0 = int(starts[0])
    T = int(max(t1 for _, t1, _ in windows) - g0) + 2
    rooms = [z.id for z in config.observed_rooms()]
    c = config.params.light_cost / (config.params.light_cost + 1.0)

    if a.data_cache and os.path.exists(a.data_cache):
        z = np.load(a.data_cache)
        rows, flag, e, sids = z["rows"], z["flag"][:, :T], z["e"][:, :T], z["sids"]
        assert flag.shape[1] == T, "the data cache is shorter than these windows"
    else:
        rows, flag, e, sids = extract(a, config_of, rooms, g0, T, pause.load(a.pauses))
        if a.data_cache:
            np.savez_compressed(a.data_cache, rows=rows, flag=flag, e=e, sids=sids)
    if a.filter_cache and os.path.exists(a.filter_cache):
        z = np.load(a.filter_cache)  # also from a longer replay with the same first start
        assert int(z["g0"]) == g0 and list(z["rooms"]) == rooms and len(z["P"]) >= T, "the filter cache does not fit"
        P = z["P"][:T]
    else:
        P = filter_series(a, config_of, configs, truth, rooms, g0, T, pause.load(a.pauses))
        if a.filter_cache:
            np.savez_compressed(a.filter_cache, P=P, rooms=np.array(rooms), g0=g0)
    pauses = pause.load(a.pauses)
    for m in messages(a.recordings, g0, g0 + T):  # the switch's pauses, for skipping windows
        pause.follow(pauses, m)

    import entries
    plan = entries.Plan(config)
    rows_pts = rows[(rows[:, 2] > 0)][:, [0, 3, 4, 5, 6]]
    dec, bg = baselines(rows, flag, e, sids, config, rooms, g0, T, plan,
                        lambda: entries.crossings(plan, rows_pts, config.sensors))
    F = np.nan_to_num(P, nan=0.0) > c
    dec["F"] = F
    F5 = np.nan_to_num(P, nan=0.0) > 0.5  # equal costs (light_cost 1)
    dec["F.5"] = F5
    dec["F.5|B1mo10"] = F5 | dec["B1mo10"]
    for H in (2, 5, 10, 30):
        for v in ("m", "r", "md", "rd", "mo", "ro"):
            dec[f"F|B1{v}{H}"] = F | dec[f"B1{v}{H}"]
        dec[f"F|B2e{H}"] = F | dec[f"B2e{H}"]
    seen_m = np.zeros((T, len(rooms)), dtype=bool)
    k = np.floor(rows[:, 0] - g0).astype(int)
    sel = (rows[:, 2] > 0) & (k >= 0) & (k < T)
    seen_m[k[sel], rows[sel, 1].astype(int)] = True

    print(f"{a.truth}: rooms {', '.join(rooms)}; LD2410C background (median of quiet seconds): "
          + "; ".join(f"{r} {v[1] // 60} min" for r, v in bg.items()))
    print(f"{'method':18s} {'wrong on':>14s} {'wrong off':>14s} | {'min on/empty':>16s} {'min off/occ':>16s} | "
          f"{'lit empty':>9s} {'dark occ':>8s}")
    out = {}
    for name, d in dec.items():
        res, per = score(d, windows, rooms, g0, T, seen_m, pauses)
        out[name] = {"res": res, "per": per}
        print(f"{name:18s} {res['on']:6.2f} of {res['n_off']:3d} {res['off']:6.2f} of {res['n_on']:3d} | "
              f"{res['min_on']:6.1f} / {res['min_empty']:7.1f} {res['min_off']:6.1f} / {res['min_occ']:7.1f} | "
              f"{res['lit_empty']:9.1f} {res['dark_occ']:8.1f}")
    if a.episodes:
        b1 = dec["B1m5"]
        print("\nwithin the windows: the filter off while B1m5 on, and on while B1m5 off (>= 5 s)")
        for t0, t1, r in windows:
            lo, hi = max(int(math.ceil(t0 - g0)), 0), min(int(t1 - g0) + 1, T)
            for room, n in r["rooms"].items():
                if room not in rooms:
                    continue
                j = rooms.index(room)
                for label, x in (("F off, B1 on", b1[lo:hi, j] & ~F[lo:hi, j]), ("F on, B1 off", F[lo:hi, j] & ~b1[lo:hi, j])):
                    for s0, s1 in runs_of(x):
                        if s1 - s0 >= 5:
                            print(f"  {r['name'][:34]:34s} {room:13s} truth {n}: {label} {hms(g0 + lo + s0)} {s1 - s0:5d} s")
    if a.json:
        json.dump({"truth": a.truth, "rooms": rooms, "results": out, "background": bg}, open(a.json, "w"))
    if a.json:  # the decisions, for further analysis
        np.savez_compressed(os.path.splitext(a.json)[0] + "_dec.npz", g0=g0, rooms=np.array(rooms), P=P,
                            **{k_.replace("|", "OR").replace("&", "AND").replace("(", "").replace(")", ""): v
                               for k_, v in dec.items()})


if __name__ == "__main__":
    main()
