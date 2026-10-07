"""The filter (filter.py, MODEL.md) on simulated scenes in a small flat: one room with two
sensors, a balcony and a hallway behind doors."""

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate

from test_frames import sim_sensors, walk

FLUR_DOOR = (0.0, 4.0)
BALCONY_DOOR = (6.0, 2.0)


def flat_config(entry: bool = False) -> Config:
    """Room 6 x 5 m (two sensors in the bottom corners), balcony behind the right wall (door at
    (6, 2)), hallway behind the left wall (door at (0, 4)). entry: the hallway is the stairwell,
    outside the flat (MODEL.md 2), and its door the flat's door."""
    def rect(zid, x0, y0, x1, y1, is_entry=False):
        return {"id": zid, "name": zid, "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2], "entry": is_entry}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "a", "x": 0.05, "y": 0.05, "heading": 45, "placed": True},
            {"id": "b", "x": 5.95, "y": 0.05, "heading": 135, "placed": True},
        ],
        "zones": [rect("wohn", 0, 0, 6, 5), rect("balkon", 6, 1, 7.5, 3), rect("flur", -2, 3, 0, 5, entry)],
        "walls": [w((0, 0), (6, 0)), w((6, 0), (6, 5)), w((6, 5), (0, 5)), w((0, 5), (0, 0)),
                  w((6, 1), (7.5, 1)), w((7.5, 1), (7.5, 3)), w((7.5, 3), (6, 3)),
                  w((0, 3), (-2, 3)), w((-2, 3), (-2, 5)), w((-2, 5), (0, 5))],
        "doors": [{"id": "balkon", "x": 6.0, "y": 2.0, "width": 0.9}, {"id": "flur", "x": 0.0, "y": 4.0, "width": 0.9}],
    })


def run(config, people, duration, start_places, every=0.5, check=False, **kw):
    crowd = Tracker(config, start=0.0, people=start_places)
    samples = []
    next_sample = 0.0
    for t, sid, frame in simulate(people, sim_sensors(config, **kw), duration, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if check:
            crowd.check()
        if t >= next_sample:
            samples.append((t, crowd.place_distribution(), crowd.present_count()))
            next_sample = t + every
    return crowd, samples


def at(samples, t, place):
    """P(somebody is in the place) at the sample nearest t."""
    return 1 - min(samples, key=lambda s: abs(s[0] - t))[1][place][0]


def present(samples, t0, t1):
    return {n for t, _, n in samples if t0 <= t <= t1}


def test_the_world_knows_rooms_regions_and_walls():
    crowd = Tracker(flat_config(), start=0.0, people=["flur"])
    world = crowd.world
    assert set(world.places) == {"observed", "balkon", "flur", "outside"}
    pts = np.array([[3.0, 2.5], [7.0, 2.0], [-1.0, 4.0], [10.0, 10.0]])
    assert list(world.place_of(pts)) == [0, world.index["balkon"], world.index["flur"], -1]
    # through the wall: blocked; through the door: not
    assert world.crosses_wall(np.array([[5.8, 4.0]]), np.array([[6.2, 4.0]]))[0]
    assert not world.crosses_wall(np.array([[5.8, 2.0]]), np.array([[6.2, 2.0]]))[0]


def test_one_person_comes_in_goes_to_the_balcony_and_comes_back():
    config = flat_config()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (5.5, 2.0), (7.0, 2.0), (5.0, 2.0), (3, 3), start=3,
                    pauses={2: 20, 4: 30, 6: 30}))
    crowd, samples = run(config, [a], a.waypoints[-1][0] + 5, ["flur"])
    t = {k: tw[0] for k, tw in enumerate(a.waypoints)}
    assert at(samples, t[2] + 10, "observed") > 0.9  # in the room
    on_balcony = at(samples, t[4] + 15, "balkon")
    assert on_balcony > 0.8, on_balcony  # on the balcony, not hidden at the door
    assert at(samples, t[6] + 10, "observed") > 0.9  # back


def test_one_person_in_the_room_is_one_person():
    # nothing known at the start, two may live here; only one comes in and sits: the other one is
    # not invented somewhere in the room
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (3.1, 2.5), start=3, pauses={2: 90}))
    crowd, samples = run(config, [a], a.waypoints[-1][0], None)
    assert present(samples, 30, a.waypoints[-1][0] - 1) == {1}


def test_nobody_walks_along_unseen():
    # nothing known at the start, two may live here; one walks around the room, and each sensor sees
    # them a bit elsewhere, wandering (measured: 0.2-0.3 m, correlated over seconds, MODEL.md 4.1). "Two people, each
    # sensor sees one of them" must lose against "one person": two people walking exactly alike, one
    # never seen, is improbable
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 1.5), (5, 3.5), (1.5, 3.5), (4.5, 1.0), (2.5, 2.5), start=3))
    crowd, samples = run(config, [a], a.waypoints[-1][0], None, bias=0.25, bias_time=3.5)
    assert present(samples, 15, a.waypoints[-1][0] - 1) == {1}


import pytest  # noqa: E402


def test_somebody_else_coming_in_is_counted():
    # one person is known to be out (in the stairwell), nobody else; they come in and sit, then
    # somebody nobody knew of walks in through the flat's door and stays (MODEL.md 3.4)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=1, pauses={2: 120}))
    b = Person(walk((-1.0, 4.0), FLUR_DOOR, (2.0, 2.5), (2.1, 2.5), start=40, pauses={2: 60}))
    end = min(a.waypoints[-1][0], b.waypoints[-1][0])
    crowd, samples = run(config, [a, b], end, ["outside"])
    assert present(samples, 20, 38) == {1}
    assert present(samples, 70, end - 1) == {2}


def test_a_recalibrated_sensor_keeps_the_people():
    # report 7.10. 08:07: two people in the house, one known at the start; a sensor's heading is corrected
    # while both sit in view. The people stay as they were (a recalibration says nothing about
    # them); starting over, the one known person could take only one of them and the other's
    # track would be a ghost for good (no unknown person left to explain it)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=1, pauses={2: 160}))
    b = Person(walk((-1.0, 4.0), FLUR_DOOR, (2.0, 2.5), (2.1, 2.5), start=40, pauses={2: 100}))
    end = min(a.waypoints[-1][0], b.waypoints[-1][0])
    turned = Config.from_dict({**config.to_dict(), "sensors": [{**s, "heading": s["heading"] + 1.5}
                                                               if s["id"] == "a" else s
                                                               for s in config.to_dict()["sensors"]]})
    crowd = Tracker(config, start=0.0, people=["outside"])
    samples = []
    done = False
    for t, sid, frame in simulate([a, b], sim_sensors(config), end, walls=config.wall_segments):
        if t >= 100 and not done:
            crowd.reconfigure(turned)
            crowd.check()
            done = True
        crowd.process_frame(sid, t, frame)
        if t >= 90 and int(t * 2) != int((t - 0.1) * 2):
            dist = crowd.count_distribution()["wohn"]
            samples.append((t, float(dist[2]) if len(dist) > 2 else 0.0))
    assert crowd.config.sensors[0].heading == 46.5
    assert all(p > 0.9 for t, p in samples), [(round(t), round(p, 3)) for t, p in samples if p <= 0.9]
    # what only the outputs read (the light's cost, "wird betreten", the display) and the names of
    # zones and sensors: nothing of the model changes, the tracks and hypotheses stay
    d = turned.to_dict()
    d["params"].update(light_cost=3.0, approach_cost=0.1, lead_time=3.0, ld2410_hold=5.0, target_threshold=0.7)
    d["zones"][0]["name"] = "Wohnzimmer"
    d["sensors"][1]["name"] = "Ecke"
    segs, hyps = dict(crowd.segs), [(hy.logw, hy.key()) for hy in crowd.hyps]
    assert not crowd.reconfigure(Config.from_dict(d)) and crowd.segs == segs
    assert [(hy.logw, hy.key()) for hy in crowd.hyps] == hyps
    c = 3.0 / 4.0
    assert crowd.zone_states()["wohn"].decided == (crowd.zone_states()["wohn"].probability > c)
    # a parameter of the model: it starts over as after a restart, from what it knew (the tracks
    # end, the people go to the tiles, MODEL.md 5.3): both are still there
    other = Config.from_dict({**d, "params": {**d["params"], "range_sigma_base": 0.2}})
    assert crowd.reconfigure(other) and not crowd.segs and crowd.started_from is not None
    dist = crowd.count_distribution()["wohn"]
    assert len(dist) > 2 and dist[2] > 0.9, dist


def test_a_short_track_in_an_empty_room_is_a_ghost():
    # nobody home, known: a target shows up mid-room for a second and is gone again
    config = flat_config()
    ghost = Person([(20.0, 3.0, 2.5), (21.0, 3.0, 2.5)])
    crowd, samples = run(config, [ghost], 40, ["outside", "outside"])
    assert all(n == 0 for t, _, n in samples)


def test_the_bookkeeping_holds_when_two_cross_and_ghosts_come():
    # two people cross, sensors see them a bit elsewhere, merge them when close, and show ghosts
    config = flat_config()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (1.0, 1.0), (5.0, 4.0), (5.1, 4.0), start=1, pauses={3: 5}))
    b = Person(walk((7.0, 2.0), BALCONY_DOOR, (5.0, 1.0), (1.0, 4.0), (1.1, 4.0), start=1, pauses={3: 5}))
    run(config, [a, b], 25, ["flur", "balkon"], check=True, bias=0.2, bias_time=3.5, resolution=0.5,
        ghost_rate=20)


def test_occupied_is_the_cheaper_decision_on_the_probability():
    # nothing known: P(somebody in the room) lies between the thresholds of the two costs
    config = flat_config()
    crowd = Tracker(config, start=0.0)
    p = 1 - crowd.count_distribution()["wohn"][0]
    assert 0.2 < p < 0.8
    for cost, occupied in ((0.1, True), (10.0, False)):
        config.params.light_cost = cost
        st = crowd.zone_states()["wohn"]
        assert st.probability == p and st.occupied is occupied


def _learned(crowd):
    """(z0, P(ghost) the map learns with) of every track that ends."""
    out = []
    crowd.listeners.append(lambda kind, d: out.append((d[1], d[2])) if kind == "track_end" else None)
    return out


def test_the_ghost_map_does_not_confirm_itself():
    # the map claims a hotspot of ghosts where somebody sits down (MODEL.md 4.2): the tracks on them
    # there count as a person all the same - what the map says at a spot is no evidence for it
    seat = (4.5, 3.5)
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside", "outside"])
    for sid in ("a", "b"):
        crowd.ghost_map.add_birth(sid, seat, 200.0)
    learned = _learned(crowd)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, seat, (seat[0] + 0.05, seat[1]), start=2, pauses={2: 150}))
    for t, sid, frame in simulate([a], sim_sensors(config, still_dropout=1 / 30, still_gap=40), a.waypoints[-1][0],
                                  walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    at_seat = [p for z, p in learned if np.hypot(z[0] - seat[0], z[1] - seat[1]) < 0.5]
    assert len(at_seat) >= 2 and max(at_seat) < 0.01, learned
    assert max(p for _, p in learned) < 0.05, learned  # coming in at the door: a person too


def test_a_sitter_taken_for_ghosts_does_not_become_a_ghost_source():
    # MODEL.md 4.2, 10: somebody already sits at a spot when the filter starts with nobody inside (as
    # after a start of the app); the LD2450s drop them now and then. Some of their tracks are taken
    # for ghosts. That must not make the seat a ghost source (with the prior of 4 h of watching and the
    # lives learned online: 3.6 / 12.6 times the prior after 10 min, the long ghosts 44 s instead of 39)
    seat = (4.5, 3.5)
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside", "outside"])
    learned = _learned(crowd)
    a = Person([(0.0, *seat), (600.0, seat[0] + 0.05, seat[1])])
    for t, sid, frame in simulate([a], sim_sensors(config, still_dropout=1 / 60, ld2410=False), 600,
                                  walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    gm = crowd.ghost_map
    assert sum(p for _, p in learned) > 2, learned  # the case: some of them were taken for ghosts
    assert all(gm.rate(s, np.array([seat]))[0] < 2 * gm.prior_rate for s in ("a", "b"))
    assert crowd._ghost_types() == crowd.m.ghost_types


def test_a_ghost_in_an_empty_house_is_learned_as_one():
    config = flat_config()
    crowd = Tracker(config, start=0.0, people=["outside", "outside"])
    learned = _learned(crowd)
    ghost = Person([(20.0, 3.0, 2.5), (21.0, 3.0, 2.5)])
    for t, sid, frame in simulate([ghost], sim_sensors(config), 40, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    assert learned and min(p for _, p in learned) > 0.9, learned


def test_a_sitter_the_ld2450_loses_stays_while_the_ld2410c_sees_them():
    # MODEL.md 4.3: somebody sits 1.5 m in front of sensor a; its LD2450 and the other one lose them
    # (hidden behind somebody, a posture it can't see), its LD2410C keeps seeing them
    config = flat_config()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (1.2, 1.2), start=2, pauses={2: 240}))
    sensors = sim_sensors(config)
    for s in sensors:
        s.blind_to, s.blind_after = (0,), a.waypoints[2][0] + 10
    crowd = Tracker(config, start=0.0, people=["flur"])
    samples = []
    for t, sid, frame in simulate([a], sensors, a.waypoints[-1][0] - 1, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if t > a.waypoints[2][0] + 180 and not samples:
            samples.append(1 - crowd.count_distribution()["wohn"][0])
    assert samples[0] > 0.9, samples


def test_the_app_and_its_config_have_one_version():
    import pathlib
    import re

    import presence_tracker
    text = (pathlib.Path(__file__).parent.parent / "config.yaml").read_text()
    assert re.search(r'version: "([^"]+)"', text).group(1) == presence_tracker.__version__


def test_the_silence_before_a_frame_belongs_to_the_frame_before_it():
    """The firmware sends a heartbeat every 5 s while it has nothing to report; the first frame after
    the silence is there because something rose. The silence stands for the energies of the frame
    before it, the new frame for one frame (MODEL.md 4.3)."""
    def frame(e):
        return {"targets": [], "ld2410": {"moving": e > 30, "still": e > 30, "move_gates": [e] * 9,
                                          "still_gates": [0, 0] + [e] * 7}}

    crowd = Tracker(flat_config(), start=0.0)
    si, f = crowd.sidx["a"], crowd.m.ld_frame
    crowd.process_frame("a", 1.0, frame(5))
    crowd.process_frame("a", 6.0, frame(60))
    st = crowd._ld_stats[si]
    assert np.isclose(st.time, 5.0 + f)
    assert np.allclose(st.t_e, 5 * 5.0 + 60 * f)
    # at the frame rate, each frame stands for the time since the one before
    crowd.process_frame("a", 6.09, frame(60))
    assert np.isclose(crowd._ld_stats[si].time, 5.09 + f)
    # after lost data (more than 6 s) the gap says nothing: each of the two frames stands for one frame
    crowd = Tracker(flat_config(), start=0.0)
    crowd.process_frame("a", 1.0, frame(5))
    crowd.process_frame("a", 8.0, frame(60))
    assert np.allclose(crowd._ld_stats[si].t_e, 5 * f + 60 * f)


def test_the_tracks_of_a_sensor_that_died_end():
    """A board dies while it tracks somebody (MODEL.md 4.1, 4.4: more than 6 s without a frame is
    lost data, its tracks are over). Its tracks end without waiting for its next frame; the person
    goes to the tiles and fades as anybody unseen does, instead of holding the room occupied for
    hours after they left (until 0.18)."""
    config = flat_config(entry=True)
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    # walks in, sits at (4.5, 1) until about 130 s, then walks out through the flat's door
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), FLUR_DOOR, (-1.0, 4.0), start=1, pauses={2: 120}))
    frames = list(simulate([a], sim_sensors(config), 300, walls=config.wall_segments))
    for dead in (("a", "b"), ("a",)):
        crowd = Tracker(config, start=0.0, people=["outside"])
        p = {}
        for t, sid, frame in frames:
            if sid in dead and t >= 60.0:
                crowd.step(t)  # the others' frames, or the app's ticks, go on
            else:
                crowd.process_frame(sid, t, frame)
            crowd.check()
            p.setdefault(int(t), 1 - crowd.count_distribution()["wohn"][0])
            if 50.0 <= t < 60.0:
                assert any(crowd.segs[s]["si"] == crowd.sidx["a"] for s in crowd.segs)
            if t >= 67.0:  # its last frame + LOST: no track of a dead sensor is left
                assert all(crowd.sensors[crowd.segs[s]["si"]] not in dead for s in crowd.segs), (t, dead)
        for t in (600.0, 1800.0, 3600.0):
            crowd.step(t)
            p[int(t)] = 1 - crowd.count_distribution()["wohn"][0]
        assert p[55] > 0.99
        assert all(p[t] < c for t in p if t >= 200), (dead, {t: round(v, 3) for t, v in p.items() if t >= 200 and v >= c})
