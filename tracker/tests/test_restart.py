"""A restart of the app (MODEL.md 5.3): what was known about the people is saved and is the prior of
the next start, moved on over the time between (filter.Tracker.people_state / restore_people);
without it nothing is known, and nobody is invented in view."""

import json

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.hidden import Hidden
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate

from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def sitting_two(config, end):
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=1, pauses={2: end}))
    b = Person(walk((-1.0, 4.0), FLUR_DOOR, (2.0, 2.5), (2.1, 2.5), start=20, pauses={2: end}))
    return [a, b]


def two_in_the_room(dist) -> float:
    return float(dist[2]) if len(dist) > 2 else 0.0


def test_the_people_survive_a_restart():
    # two sit in the room; the app stops at 100 s and starts again 30 s later: both are still there,
    # their tracks start anew (a start, MODEL.md 4.1) and are theirs again, nobody is a ghost
    config = flat_config(entry=True)
    people = sitting_two(config, 300)
    frames = list(simulate(people, sim_sensors(config), 250, walls=config.wall_segments))
    crowd = Tracker(config, start=0.0, people=["flur"])
    for t, sid, frame in frames:
        if t >= 100:
            break
        crowd.process_frame(sid, t, frame)
    before = crowd.count_distribution()["wohn"]
    state = json.loads(json.dumps(crowd.people_state()))
    assert len(json.dumps(state)) < 200_000
    # restored as it was saved: the same people, now on the tiles
    again = Tracker(config)
    assert again.restore_people(state)
    again.check()
    assert two_in_the_room(before) > 0.99 and two_in_the_room(again.count_distribution()["wohn"]) > 0.99
    # and on from the first frame after the gap
    later = Tracker(config)
    assert later.restore_people(state)
    samples = []
    for t, sid, frame in frames:
        if t < 130:
            continue
        later.process_frame(sid, t, frame)
        later.check()
        if int(t * 2) != int((t - 0.1) * 2):
            samples.append((t, two_in_the_room(later.count_distribution()["wohn"])))
    assert all(p > 0.9 for t, p in samples), [(round(t), round(p, 3)) for t, p in samples if p <= 0.9]


def test_the_saved_state_round_trips_and_needs_the_same_tiles():
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["flur", "anywhere"])
    state = json.loads(json.dumps(crowd.people_state()))
    again = Tracker(config)
    assert again.restore_people(state)
    a, b = crowd.place_distribution(), again.place_distribution()
    assert all(np.allclose(a[k], b[k], atol=1e-6) for k in a)
    assert crowd.people_state()["densities"] == state["densities"]
    # another floor plan (the hallway a room with a sensor): the densities do not fit, nothing is
    # restored; nor from a broken file
    d = config.to_dict()
    d["sensors"].append({"id": "c", "x": -1.95, "y": 3.05, "heading": 45, "placed": True})
    other = Tracker(Config.from_dict(d))
    assert other.tiles.n > again.tiles.n
    before = other.people_state()
    assert not other.restore_people(state) and other.started_from is None
    assert other.people_state() == before
    assert not again.restore_people({**state, "densities": state["densities"][:-1]})
    assert not again.restore_people({**state, "hyps": [{"logw": "x"}]})


def test_a_gap_moves_everybody_on():
    # somebody known to sit in the room: after a gap of minutes still most probably there, after a
    # day spread over the house and out of it (the motion of MODEL.md 3, nothing weighs: a gap says
    # nothing, 4.4); the leaps of a long gap agree with moving all of it as always
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside"])
    h = Hidden(crowd.tiles)
    h.still[:, :, crowd.tiles.cell_near((3.0, 2.5))] = crowd.shapes.stay_prior(ongoing=True)
    h._normalize()
    crowd.hyps[0].hidden = [h]
    state = crowd.people_state()

    def after(gap, exact=False):
        from presence_tracker import filter as F
        tr = Tracker(config)
        tr.restore_people(state)
        old = F.GAP_EXACT
        F.GAP_EXACT = gap + 1 if exact else old
        try:
            tr.process_frame("a", gap, {"targets": []})
        finally:
            F.GAP_EXACT = old
        return tr.hyps[0].hidden[0].places()

    short, day = after(60.0), after(86400.0)
    assert short[0] > 0.99
    assert day[0] < 0.9 and day[-1] > 0.02  # some went out
    long, exact = after(3 * 3600.0), after(3 * 3600.0, exact=True)
    assert np.abs(long - exact).max() < 0.01, (long, exact)


def test_nothing_known_invents_nobody_in_view():
    # nothing known (no saved state): nobody there, the sensors see ghosts now and then - the room
    # stays dark; then somebody comes in and sits down: found
    config = flat_config(entry=True)
    crowd = Tracker(config)
    assert crowd.started_from is None
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=150, pauses={2: 200}))
    occupied = []
    for t, sid, frame in simulate([a], sim_sensors(config, ghost_rate=4), 300, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        occupied.append((t, 1 - crowd.count_distribution()["wohn"][0]))
    worst = max((p, t) for t, p in occupied if t < 150)
    assert worst[0] < c, worst
    assert min(p for t, p in occupied if t > 200) > 0.9


def test_nothing_known_finds_who_sits_in_view_at_the_start():
    # nothing known, and somebody sits in the room already when the model starts (their tracks are
    # there at the first frames, MODEL.md 4.1): they are found, not taken for a ghost
    config = flat_config(entry=True)
    crowd = Tracker(config)
    a = Person([(0.0, 4.5, 1.0), (300.0, 4.55, 1.0)])
    occupied = []
    for t, sid, frame in simulate([a], sim_sensors(config), 200, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        occupied.append((t, 1 - crowd.count_distribution()["wohn"][0]))
    assert min(p for t, p in occupied if t > 60) > 0.9, [(round(t), round(p, 2)) for t, p in occupied[::50]]
