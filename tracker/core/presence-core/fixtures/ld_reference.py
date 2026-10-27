"""Makes ld_reference.json.gz, the reference of the LD2410C's weighing for filter/ld_weigh.rs's tests: a small
fixed case in the flat of tests/test_filter.py (two sensors) - three hypotheses with a person with a track
whose mixture has a part gone through a door, a known person without one whose amplitude was measured,
another one anywhere, the unknown ones - two rounds of frames (censored ones, all 0, a silence, the
LD2450 with a target), weighed by the Python filter of 16d148f (the last one weighing in Python).

    cd tracker && uv run python core/presence-core/fixtures/ld_reference.py world OUT_WORLD.json
        (any version with Tracker._export_sensor: the world as the core gets it)
    cd <worktree at 16d148f>/tracker && uv run python <this file> ref OUT_WORLD.json ld_reference.json.gz
"""

import gzip
import json
import math
import sys

import numpy as np

sys.path[:0] = [".", "tests"]  # run from tracker/

from presence_tracker.filter import Tracker  # noqa: E402

from test_filter import FLUR_DOOR, flat_config  # noqa: E402


def floats(a):
    return [float(x) for x in np.asarray(a, dtype=float).ravel()]


def ints(a):
    return [int(x) for x in np.asarray(a).ravel()]


def pairs(a):
    return [floats(r) for r in np.asarray(a, dtype=float).reshape(-1, 2)]


def world(out):
    """The world as world::WorldExport (its layout, for serde); the walking matrix and the LD2450s' sight 0
    (the weighing moves nobody and tests no track, and so the file stays small)."""
    tr = Tracker(flat_config(entry=True), start=0.0, people=["outside"])
    e = tr._export()
    regions = int(e["R"])
    rates = np.asarray(e["door_rates"], dtype=float).reshape(-1, regions + 1)
    ptr, lst = ints(e["door_ptr"]), ints(e["door_list"])

    def sensor(s):
        return {"at": list(s["at"]), "origin": list(s["origin"]), "grid": [0.0] * len(floats(s["grid"])),
                "observed": floats(s["observed"]),
                "ld": {"axis": list(s["ld_axis"]), "dh2": s["ld_dh2"], "los": floats(s["ld_los"]),
                       "view": None if s["ld_view"] is None else floats(s["ld_view"]),
                       "tiles": {"idx": ints(s["ld_idx"]),
                                 "still": [floats(r) for r in np.asarray(s["ld_still"]).reshape(-1, 16)],
                                 "walk": [floats(r) for r in np.asarray(s["ld_walk"]).reshape(-1, 16)]}}}
    w = {
        "params": json.loads(e["params"]),
        "shapes": {k: floats(e[k]) for k in ("go", "go_w", "go_w_ongoing", "kappa", "kappa_w", "amp", "ka_w",
                                             "amp_given_kappa")},
        "raster": {"x0": e["x0"], "y0": e["y0"], "nx": e["nx"], "ny": e["ny"]},
        "walls": [floats(r) for r in np.asarray(e["walls"]).reshape(-1, 4)],
        "wall_dist": floats(e["wall_dist"]),
        "tiles": {"n": e["n"], "centers": pairs(e["centers"]), "var": pairs(e["tile_var"]), "area": floats(e["area"]),
                  "fine": ints(e["fine"]), "fine_xy": pairs(e["fine_xy"]), "regions": regions, "ages": e["A"],
                  "tick": e["tick"], "walk_t": [0.0] * len(floats(e["walk_t"])), "into_t": floats(e["into_t"]),
                  "door_tiles": [{"center": c, "var": v, "area": a, "rates": floats(r)} for c, v, a, r in
                                 zip(pairs(e["door_center"]), pairs(e["door_var"]), floats(e["door_area"]), rates)],
                  "region_doors": [lst[a:b] for a, b in zip(ptr, ptr[1:])], "exits": ints(e["exits"]),
                  "entries": ints(e["entries"]), "region_rates": floats(e["region_rates"]),
                  "widths": floats(e["widths"]), "points": pairs(e["points"]), "point_tile": ints(e["point_tile"]),
                  "point_count": floats(e["point_count"])},
        "sensors": [sensor(s) for s in e["sensors"]],
        "rooms": floats(e["rooms"]),
    }
    json.dump(w, open(out, "w"), separators=(",", ":"))


def state(tr):
    """The core's state as its dump gives it, ids replaced by positions."""
    dens, mixs, hyps, tracks = tr._c.dump()
    dpos = {k: i for i, (k, _, _) in enumerate(dens)}
    mpos = {m[0]: i for i, m in enumerate(mixs)}
    out = {"densities": [], "mixtures": [], "hyps": [], "tracks": []}
    for _, (walk, still, region, o, clock, r, amp, keep), intensity in dens:
        out["densities"].append({"walk": floats(walk), "still": floats(still),
                                 "amp": None if amp is None else floats(amp), "region": floats(region), "out": o, "clock": clock, "r": r,
                                 "intensity": bool(intensity), "keep": None if keep is None else floats(keep)})
    for _, logw, mean, cov, stay, ka, slots, phantom, a, away, t in mixs:
        out["mixtures"].append({"logw": floats(logw), "mean": floats(mean), "cov": floats(cov), "stay": floats(stay),
                                "ka": floats(ka), "slots": [[int(s), int(j), float(v)] for s, j, v in slots],
                                "phantom": bool(phantom), "a": a, "away": None if away is None else dpos[away], "t": t})
    for logw, trs, gh, people, hidden, unknown in hyps:
        out["hyps"].append({"logw": logw, "tracks": [[int(s), None if o is None else int(o)] for s, o in trs],
                            "ghosts": [[int(s), floats(life), mpos[src]] for s, life, src in gh],
                            "people": [[int(g), mpos[m]] for g, m in people],
                            "hidden": [dpos[d] for d in hidden], "unknown": dpos[unknown]})
    for seg, si, (t, zt, gt, var), z, z0, born, lost, odds in tracks:
        assert lost is None
        out["tracks"].append({"seg": int(seg), "si": si, "t": t, "zt": zt, "gt": gt, "var": var, "z": list(z),
                              "z0": list(z0), "born": born, "map_odds": odds})
    return out


def energies(rng, si, k):
    """A frame's gates: a background, somebody standing at gate 2 of sensor a, noise; some censored."""
    moving = np.array([13, 9, 4, 3, 3, 2, 2, 2, 2], dtype=float)
    still = np.array([0, 0, 6, 5, 5, 5, 4, 4, 4], dtype=float)
    if si == 0:
        still[2:5] += [20, 35, 10]
        moving[2] += 8
    moving = np.round(moving * rng.uniform(0.6, 1.5, 9))
    still = np.round(still * rng.uniform(0.6, 1.5, 9))
    if k % 7 == 3:
        moving[0] = still[3] = 100  # capped: censored
    return np.minimum(moving, 100), np.minimum(still, 100)


def reference(world_path, out):
    from presence_tracker.hidden import Hidden, Undetected
    from presence_tracker.filter import Hyp
    from presence_tracker.sim import Person, SimSensor, simulate
    from test_frames import walk

    w = json.load(open(world_path))
    config = flat_config(entry=True)
    seat = (1.3, 1.3)
    p = Person(walk((-1.0, 4.0), FLUR_DOOR, seat, (seat[0] + 0.05, seat[1]), start=5, pauses={3: 600}))
    tr = Tracker(config, start=0.0, people=["outside"])
    sensors = [SimSensor(s) for s in config.sensors]
    for t, sid, frame in simulate([p], sensors, 30.0, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
    # the fixed case, from the three hypotheses on top (they differ in their tracks)
    m = tr._view()
    tops = sorted(m.hyps, key=lambda hy: -hy.logw)[:3]
    assert len(tops) == 3 and tops[0].groups, "three hypotheses, a person with a track on top"
    tiles = tr.tiles
    g = next(iter(tops[0].groups.values()))
    g.a, g.away = 0.3, Hidden.at_place(tiles, tr.world.index["balkon"])  # a part gone through the door
    known = Hidden.anywhere(tiles, share_view=0.8, share_regions=0.1)
    levels, amps = known.still.shape[1], len(tr.shapes.amp)
    k, a, c = np.meshgrid(np.arange(levels), np.arange(amps + 1), np.arange(tiles.n), indexing="ij")
    amp = 1.0 + (k + 2 * a + c) % 3
    known._amp = amp / amp.sum(axis=1, keepdims=True)  # its amplitude measured
    anywhere = Hidden.anywhere(tiles)
    few = Undetected.anywhere(tiles, people=0.4)
    tr.hyps = [Hyp(math.log(w), dict(hy.kind), dict(hy.ghost), dict(hy.groups), hidden, ppp, dict(hy.phantom))
               for w, hy, hidden, ppp in ((0.6, tops[0], [known], tops[0].ppp), (0.3, tops[1], [], tops[0].ppp),
                                          (0.1, tops[2], [known, anywhere], few))]
    tr._push()
    start = state(tr)
    # the LD2410Cs start over
    tr._ld_stats.clear()
    tr._ld_memory.clear()
    tr._ld_echo.clear()
    t0 = tr.now
    for rt in tr.runtime.values():
        rt.ld_t, rt.ld_e, rt.ld_target_t = t0, None, t0
    learned = []
    bg = tr.ld_background
    learn, learn_echoes = bg.learn, bg.learn_echoes

    def spy(sid, b, mus, prior_w, st, alpha, off, quiet):
        learned.append({"sid": sid, "mus": [floats(x) for x in mus], "prior_w": floats(prior_w), "echo_off": off})
        return learn(sid, b, mus, prior_w, st, alpha, off, quiet)

    def spy_echoes(sid, began, rate, nobody, seconds):
        learned[-1].update(began=began, nobody=nobody, time=seconds)
        return learn_echoes(sid, began, rate, nobody, seconds)
    bg.learn, bg.learn_echoes = spy, spy_echoes
    rng = np.random.default_rng(7)
    frames, rounds = [], []
    t = t0
    for r in range(2):
        for k in range(16):
            # every 0.1 s; one all 0, one after a silence of 3 s (a heartbeat), one after a gap of 9 s
            t += 3.0 if (r, k) == (0, 9) else 9.0 if (r, k) == (1, 4) else 0.1
            for si, sid in enumerate(tr.sensors):
                rt = tr.runtime[sid]
                mg, sg = energies(rng, si, k)
                if (r, k) == (0, 5) and si == 1:
                    mg, sg = mg * 0, sg * 0
                target = si == 0 and k % 4 == 0
                rt.detections = [type("D", (), {"hidden": False})()] if target else []
                tr._ld_frame(si, rt, t, {"move_gates": floats(mg), "still_gates": floats(sg)})
                frames.append({"si": si, "t": t, "moving": floats(mg), "still": floats(sg), "target": target})
        tr.now = t
        weighed = []
        for si in sorted(tr._ld_stats):
            if tr._ld_stats[si].time >= tr.m.ld_every:
                sid = tr.sensors[si]
                b, rate = floats(bg.b(sid)), float(bg.rate(sid))
                tr._ld_weigh(si, tr._ld_stats.pop(si))
                weighed.append({"si": si, "b": b, "rate": rate, "learned": learned[-1],
                                "memory": floats(tr._ld_memory[si]), "echo": floats(tr._ld_echo[si].p)})
        if tr._dirty:
            tr._push()
        rounds.append({"t": t, "weighed": weighed, "state": state(tr)})
    data = json.dumps({"world": w, "start": start, "t0": t0, "frames": frames, "rounds": rounds}, separators=(",", ":"))
    with open(out, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as z:
        z.write(data.encode())


if __name__ == "__main__":
    if sys.argv[1] == "world":
        world(sys.argv[2])
    else:
        reference(sys.argv[2], sys.argv[3])
