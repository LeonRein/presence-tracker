""""Ziel" (MODEL.md 6): where a walker goes next, from the map of where walks went, learned by the app (the
core's learn/destination.rs). Two rooms with sensors, a door between them."""

import json

import numpy as np

from presence_tracker import ha
from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from sim import Person, simulate
from presence_tracker.frames import Frame

from test_frames import sim_sensors, walk
from fixture import fixture, unpack

DOOR = (6.0, 2.5)  # between "a" (x 0-6) and "b" (x 6-10)
DESK = (1.5, 2.5)
SOFA = (8.5, 2.5)


def two_rooms(areas=()) -> Config:
    """Two rooms with sensors, a door between them; areas: more zones (dicts)."""
    def rect(zid, x0, y0, x1, y1):
        return {"id": zid, "name": zid, "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2], "entry": False}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "a1", "name": "", "x": 0.05, "y": 0.05, "heading": 45, "placed": True},
            {"id": "a2", "name": "", "x": 5.95, "y": 4.95, "heading": 225, "placed": True},
            {"id": "b1", "name": "", "x": 9.95, "y": 0.05, "heading": 135, "placed": True},
        ],
        "zones": [rect("a", 0, 0, 6, 5), rect("b", 6, 0, 10, 5), *areas],
        "walls": [w((0, 0), (6, 0)), w((6, 0), (10, 0)), w((10, 0), (10, 5)), w((10, 5), (6, 5)),
                  w((6, 5), (0, 5)), w((0, 5), (0, 0)), w((6, 0), (6, 5))],
        "doors": [{"id": "ab", "x": DOOR[0], "y": DOOR[1], "width": 0.9}],
    })


def routine(trips: int, start: float = 1.0):
    """From the desk in a to the sofa in b and back, trips times, with pauses."""
    pts, pauses = [DESK], {0: 4}
    for _ in range(trips):
        pts += [SOFA, DESK]
        pauses[len(pts) - 2] = 4
        pauses[len(pts) - 1] = 4
    return Person(walk(*pts, start=start, pauses=pauses))


def dest_map(tr) -> dict:
    """The core's destination map as saved: its fingerprint, walks and counts (cells x velocities x
    outcomes, float32)."""
    d = tr.learned()["dest_map"]
    return {**d, "count": unpack(d["count"], "<f4").reshape(d["shape"])}


def lookup(dm: dict, vel: str) -> np.ndarray:
    """The walks counted near (3.5, 2.5) per outcome for a velocity of 1 m/s towards b ("ahead") or a
    ("back"): the cells there and the velocity cells with their weights as destination.Geometry.around
    and vel_interp of 0.38.1 had them (fixtures/destinations.json), DestinationMap.lookup."""
    geo = fixture("destinations.json")
    (cells, cw), (vc, vw) = geo["around"], geo[vel]
    return np.einsum("c,v,cvk->k", cw, vw, dm["count"][np.array(cells)[:, None], np.array(vc)[None, :]])


def run(person, until, tracker=None, config=None, zone="b"):
    """The tracker after the run, and per step (t, zone states of the zone, count distribution)."""
    config = config or two_rooms()
    tr = tracker or Tracker(config, start=0.0, people=["observed"])
    out, nxt = [], 0.0
    for t, sid, frame in simulate([person], sim_sensors(config), until, plan=config):
        tr.process_frame(sid, t, Frame.of(frame))
        if t >= nxt:
            tr.step(t)
            states = tr.zone_states()
            out.append((t, states[zone], tr.core.count_distribution(), tr.core.loglik[0]))
            nxt = t + 0.2
    return tr, out


def test_untrained_ziel_is_off():
    # nothing learned yet: "Ziel" says nothing, whoever walks straight at the door
    a = Person(walk((1.0, 2.5), (1.0, 2.5), (8.5, 2.5), start=1.0, pauses={0: 4}))
    tr, out = run(a, a.waypoints[-1][0] + 1)
    disc = ha.Discovery(None)
    for t, st, _, _ in out:
        assert not st.target and st.c_target is not None
        assert not disc.steady("b", st.to_dict(), t)["target"]


def test_the_map_learns_a_routine_and_predicts_it_earlier():
    trips = 20
    a = routine(trips)
    d = two_rooms().to_dict()
    d["params"]["target_threshold"] = 0.66  # Leon's (two equal walks against the prior's one)
    config = Config.from_dict(d)
    tr, out = run(a, a.waypoints[-1][0] + 1, config=config)
    dm, geo = dest_map(tr), fixture("destinations.json")
    assert dm["fingerprint"] == geo["fingerprint"] and dm["count"].shape[0] == geo["n"]  # the geometry of 0.38.1
    b = tr.rooms.index("b")
    assert dm["walks"] >= 2 * trips - 2  # walks a -> b, b -> a (and the stops)
    # halfway from the desk to the door, walking towards it: these walks went to b
    cnt = lookup(dm, "ahead")
    assert cnt.sum() >= 0.4 * trips  # each walk counts up to 1 (less where it passes beside the centre)
    assert cnt[b] / cnt.sum() > 0.9
    # walking away from the door there: none of them went to b
    back = lookup(dm, "back")
    assert back[b] < 0.1 * back.sum()
    # one more trip: "Ziel" comes on well before the door, from the map
    more = Person([(t + a.waypoints[-1][0], x, y) for t, x, y in routine(1).waypoints])
    _, out = run(more, more.waypoints[-1][0], tracker=tr, config=config)
    t_door = more.waypoints[1][0] + (DOOR[0] - DESK[0])
    first_t = next(t for t, st, _, _ in out if st.target)
    assert first_t <= t_door - 2.0
    st = next(st for t, st, _, _ in out if st.target)
    assert st.target_walks >= 0.25 * trips and st.target_from == "a"
    # and is on most of the time up to the door (the velocity estimate jitters: a step into velocity cells
    # with fewer walks lowers it for a moment)
    span = [st.target for t, st, _, _ in out if first_t <= t <= t_door - 0.2]
    assert sum(span) >= 0.8 * len(span)
    # back from b: not into b
    assert not any(st.target for t, st, _, _ in out if t > t_door + 2.0)


def test_the_map_is_saved_restored_and_belongs_to_its_floor_plan():
    a = routine(2)
    tr, _ = run(a, a.waypoints[-1][0] + 1)
    saved = json.loads(json.dumps(tr.learned()))
    other = Tracker(two_rooms(), start=0.0, people=["observed"])
    assert dest_map(other)["count"].sum() == 0
    other.load_learned(saved)
    trained = dest_map(tr)
    assert np.array_equal(dest_map(other)["count"], trained["count"]) and trained["count"].sum() > 0
    assert dest_map(other)["walks"] == trained["walks"]
    # a name or a sensor switched off keeps it, a threshold too: the outputs decide differently, the model goes on
    d = two_rooms().to_dict()
    d["sensors"][0]["name"] = "Ecke"
    d["sensors"][1]["enabled"] = False
    d["params"]["target_thresholds"] = {"b": 0.6}
    assert not other.reconfigure(Config.from_dict(d))
    assert np.array_equal(dest_map(other)["count"], trained["count"]) and other.zone_states()["b"].c_target == 0.6
    # a recalibrated sensor starts it over (0.51: another geometry, MODEL.md 9 "Gelerntes und Geometrie"), as
    # another door does
    d["sensors"][0]["heading"] = 50
    assert not other.reconfigure(Config.from_dict(d))
    assert dest_map(other)["count"].sum() == 0
    other.load_learned(saved)  # (learned under another geometry: the caller's to know, the app's files say it)
    assert dest_map(other)["count"].sum() > 0
    d = two_rooms().to_dict()
    d["doors"][0]["y"] = 1.5
    other.reconfigure(Config.from_dict(d))
    assert dest_map(other)["count"].sum() == 0
    # a map of another floor plan is not used
    third = Tracker(Config.from_dict(d), start=0.0, people=["observed"])
    assert not third.use_dest_map(saved["dest_map"])
    assert dest_map(third)["count"].sum() == 0


def test_occupancy_is_unchanged_by_the_map():
    a = routine(3)
    end = a.waypoints[-1][0] + 1
    trained, learning = run(a, end)
    plain = Tracker(two_rooms(), start=0.0, people=["observed"])  # the walks counted, the map left empty:
    plain.listeners.append(lambda kind, d: plain.use_dest_map(None) if kind == "watch" else None)
    _, plain = run(a, end, tracker=plain)
    t = Tracker(two_rooms(), start=0.0, people=["observed"])
    assert t.use_dest_map(trained.learned()["dest_map"])
    _, primed = run(a, end, tracker=t)
    for (t0, s0, c0, l0), (t1, s1, c1, l1), (t2, s2, c2, l2) in zip(plain, learning, primed):
        assert t0 == t1 == t2
        assert c0 == c1 == c2
        assert l0 == l1 == l2
        assert s0.occupied == s1.occupied == s2.occupied and s0.probability == s1.probability == s2.probability


def test_a_map_saved_by_0381_is_read_and_written_alike():
    # what destination.DestinationMap of 0.38.1 wrote (two trips desk -> sofa and back; fixtures/
    # destinations.json): the core takes it for these rooms and writes it back with the same counts, and
    # asked where the walks near the door went, they say what they said to the Python map
    dest = fixture("destinations.json")
    tr = Tracker(two_rooms(), start=0.0, people=["observed"])
    assert tr.use_dest_map(dest["map"])
    again = tr.learned()["dest_map"]
    assert {k: v for k, v in again.items() if k != "count"} == {k: v for k, v in dest["map"].items() if k != "count"}
    dm = dest_map(tr)
    assert np.array_equal(dm["count"], unpack(dest["map"]["count"], "<f4").reshape(dest["map"]["shape"]))
    for vel in ("ahead", "back"):
        assert np.allclose(lookup(dm, vel), dest["map_lookup"][vel], rtol=1e-12, atol=0), vel
