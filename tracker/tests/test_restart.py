"""A restart of the app (MODEL.md 5.3): what was known about the people is saved and is the prior of
the next start, moved on over the time between (filter.Tracker.people_state / restore_people);
without it nothing is known, and nobody is invented in view."""

import json
import math

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.hidden import Hidden, Undetected
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate
from presence_tracker.frames import Frame

from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def sitting_two(config, end):
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=1, pauses={2: end}))
    b = Person(walk((-1.0, 4.0), FLUR_DOOR, (2.0, 2.5), (2.1, 2.5), start=20, pauses={2: end}))
    return [a, b]


def saved(tracker, people, t: float = 0.0) -> dict:
    """A saved state (Tracker.people_state's format) of one hypothesis: these known people (Hidden),
    nobody unknown - how a test puts people where it wants them (the core's state is written only by
    the filter)."""
    tl = tracker.tiles
    return {"t": t, "tiles": tl.fingerprint(), "densities": [h.to_dict() for h in people] + [Undetected.none(tl).to_dict()],
            "hyps": [{"logw": 0.0, "people": list(range(len(people))), "unknown": len(people)}]}


def two_in_the_room(dist) -> float:
    return float(dist[2]) if len(dist) > 2 else 0.0


def test_the_people_survive_a_restart():
    # two sit in the room; the app stops at 100 s and starts again 30 s later: both are still there,
    # their tracks start anew (a start, MODEL.md 4.1) and are theirs again, nobody is a ghost
    config = flat_config(entry=True)
    people = sitting_two(config, 300)
    frames = list(simulate(people, sim_sensors(config), 250, walls=config.wall_segments))
    crowd = Tracker(config, start=0.0, people=["outside"])
    for t, sid, frame in frames:
        if t >= 100:
            break
        crowd.process_frame(sid, t, Frame.of(frame))
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
        later.process_frame(sid, t, Frame.of(frame))
        later.check()
        if int(t * 2) != int((t - 0.1) * 2):
            samples.append((t, two_in_the_room(later.count_distribution()["wohn"])))
    assert all(p > 0.9 for t, p in samples), [(round(t), round(p, 3)) for t, p in samples if p <= 0.9]


def test_the_saved_state_round_trips_and_needs_the_same_tiles():
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside", "anywhere"])
    state = json.loads(json.dumps(crowd.people_state()))
    again = Tracker(config)
    assert again.restore_people(state)
    a, b = crowd.place_distribution(), again.place_distribution()
    assert all(np.allclose(a[k], b[k], atol=1e-3) for k in a)  # float16
    assert crowd.people_state()["densities"] == state["densities"]
    # another floor plan (the hallway a room of the flat with a sensor, no longer the stairwell): the
    # densities do not fit, nothing is restored; nor from a broken file
    d = config.to_dict()
    d["sensors"].append({"id": "c", "x": -1.95, "y": 3.05, "heading": 45, "placed": True})
    d["zones"] = [{**z, "entry": False} for z in d["zones"]]
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
    assert crowd.restore_people(saved(crowd, [h]))
    state = crowd.people_state()

    def after(gap, exact=False):
        from presence_tracker import filter as F
        from presence_tracker.filtermodel import Model
        m = Model()
        m.unseen_life = math.inf  # the motion alone (records nothing tests expire over a gap too, MODEL.md 5.5)
        old = F.GAP_EXACT
        F.GAP_EXACT = gap + 1 if exact else old  # a parameter of the core: given when it is built
        try:
            tr = Tracker(config, model=m)
            tr.restore_people(state)
            tr.process_frame("a", gap, Frame.of({"targets": []}))
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
        crowd.process_frame(sid, t, Frame.of(frame))
        occupied.append((t, 1 - crowd.count_distribution()["wohn"][0]))
    # a ghost of up to a second may be somebody stepping out of the hallway and back for a moment
    # (a second of light, as with the start of 0.10); nobody stays
    stretches, start, last = [], None, None
    for t, p in occupied:
        if t < 150 and p > c:
            start = t if start is None else start
            last = t
        elif start is not None:
            stretches.append(last - start)
            start = None
    assert all(s < 2.0 for s in stretches), stretches
    assert min(p for t, p in occupied if t > 200) > 0.9


def test_nothing_known_finds_who_sits_in_view_at_the_start():
    # nothing known, and somebody sits in the room already when the model starts (their tracks are
    # there at the first frames, MODEL.md 4.1): they are found, not taken for a ghost
    config = flat_config(entry=True)
    crowd = Tracker(config)
    a = Person([(0.0, 4.5, 1.0), (300.0, 4.55, 1.0)])
    occupied = []
    for t, sid, frame in simulate([a], sim_sensors(config), 200, walls=config.wall_segments):
        crowd.process_frame(sid, t, Frame.of(frame))
        occupied.append((t, 1 - crowd.count_distribution()["wohn"][0]))
    assert min(p for t, p in occupied if t > 60) > 0.9, [(round(t), round(p, 2)) for t, p in occupied[::50]]


def test_saving_the_people_changes_nothing():
    # people_state is read at every save (10 min, app.py): until 0.28 reading a stored LD2410C
    # amplitude applied its deferred mixing (Hidden.amp) in the model's own densities - the live run
    # then went another way than a replay of the same frames (rounding, MEASURED_CUT). The core
    # works on copies (its own test: a person with a track and a part gone through a door)
    from presence_tracker.gauss import Gauss
    crowd = Tracker(flat_config(), start=0.0)
    m, sh, tl = crowd.m, crowd.shapes, crowd.tiles
    g = Gauss.source(1, np.array([3.0, 3.0]), 0.01, m, sh)
    g.logw = np.log(np.array([1.0 - 1e-9, 1e-9]))  # standing
    a = int(np.argmin(abs(sh.amp - 0.3)))
    ka = np.zeros_like(g.ka)
    ka[:, a] = g.kw
    g.ka = ka
    h, other = g.to_tiles(tl), g.to_tiles(tl)
    assert h.measured and crowd.restore_people(saved(crowd, [h, other]))
    for k in range(1, 26):  # moving on defers the mixing of the amplitude (Hidden._keep)
        crowd.step(0.2 * k)

    def raw():
        return [np.copy(x) for u in crowd._h[0].hidden for x in (u._amp, u._keep, u.still)]
    first = crowd.people_state()
    before = raw()
    assert crowd.people_state() == first
    assert all(np.array_equal(x, y) for x, y in zip(raw(), before))
    assert all(u._keep is not None for u in crowd._h[0].hidden)
