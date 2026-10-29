"""A restart of the app (MODEL.md 5.3): what was known about the people is saved and is the prior of
the next start, moved on over the time between (filter.Tracker.people_state / restore_people);
without it nothing is known, and nobody is invented in view."""

import json
import math

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from sim import Person, simulate
from presence_tracker.frames import Frame

from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk
from fixture import fixture, unpack


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
    frames = list(simulate(people, sim_sensors(config), 250, plan=config))
    crowd = Tracker(config, start=0.0, people=["outside"])
    for t, sid, frame in frames:
        if t >= 100:
            break
        crowd.process_frame(sid, t, Frame.of(frame))
    before = crowd.core.count_distribution()["wohn"]
    state = json.loads(json.dumps(crowd.people_state()))
    assert len(json.dumps(state)) < 200_000
    # restored as it was saved: the same people, now on the tiles
    again = Tracker(config)
    assert again.restore_people(state)
    again.core.check()
    assert two_in_the_room(before) > 0.99 and two_in_the_room(again.core.count_distribution()["wohn"]) > 0.99
    # and on from the first frame after the gap
    later = Tracker(config)
    assert later.restore_people(state)
    samples = []
    for t, sid, frame in frames:
        if t < 130:
            continue
        later.process_frame(sid, t, Frame.of(frame))
        later.core.check()
        if int(t * 2) != int((t - 0.1) * 2):
            samples.append((t, two_in_the_room(later.core.count_distribution()["wohn"])))
    assert all(p > 0.9 for t, p in samples), [(round(t), round(p, 3)) for t, p in samples if p <= 0.9]


def test_the_saved_state_round_trips_and_needs_the_same_tiles():
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside", "anywhere"])
    state = json.loads(json.dumps(crowd.people_state()))
    again = Tracker(config)
    assert again.restore_people(state)
    a, b = crowd.core.place_distribution(), again.core.place_distribution()
    assert all(np.allclose(a[k], b[k], atol=1e-3) for k in a)  # float16
    assert crowd.people_state()["densities"] == state["densities"]
    # another floor plan (the hallway a room of the flat with a sensor, no longer the stairwell): the
    # densities do not fit, nothing is restored; nor from a broken file
    d = config.to_dict()
    d["sensors"].append({"id": "c", "name": "", "x": -1.95, "y": 3.05, "heading": 45, "placed": True})
    d["zones"] = [{**z, "entry": False} for z in d["zones"]]
    other = Tracker(Config.from_dict(d))
    assert len(other.tiles()["squares"]) > len(again.tiles()["squares"])
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
    assert crowd.restore_people(fixture("people.json")["sitter"])  # standing in the tile at (3, 2.5)
    state = crowd.people_state()

    def after(gap, exact=False):
        """Where the one person is after the gap: per place (observed, regions, outside) their mass."""
        model = {"unseen_life": math.inf}  # the motion alone (records nothing tests expire over a gap too, MODEL.md 5.5)
        if exact:
            model["gap_exact"] = gap + 1
        tr = Tracker(config, model=model)
        tr.restore_people(state)
        tr.process_frame("a", gap, Frame.of({"targets": []}))
        return np.array([1 - d[0] for place, d in tr.core.place_distribution().items() if place != "_house"])

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
    c = config.data["params"]["light_cost"] / (config.data["params"]["light_cost"] + 1.0)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=150, pauses={2: 200}))
    occupied = []
    for t, sid, frame in simulate([a], sim_sensors(config, ghost_rate=4), 300, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
        occupied.append((t, 1 - crowd.core.count_distribution()["wohn"][0]))
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
    for t, sid, frame in simulate([a], sim_sensors(config), 200, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
        occupied.append((t, 1 - crowd.core.count_distribution()["wohn"][0]))
    assert min(p for t, p in occupied if t > 60) > 0.9, [(round(t), round(p, 2)) for t, p in occupied[::50]]


def test_saving_the_people_changes_nothing():
    # people_state is read at every save (10 min, app.py): until 0.28 reading a stored LD2410C
    # amplitude applied its deferred mixing in the model's own densities - the live run then went
    # another way than a replay of the same frames (rounding, MEASURED_CUT). The core works on copies
    # (its own test: a person with a track and a part gone through a door). Two standing with an
    # amplitude a track measured (fixtures/people.json), moved on, which defers the mixing: saving
    # between the steps changes nothing of the run
    def run(save):
        crowd = Tracker(flat_config(), start=0.0)
        assert crowd.restore_people(fixture("people.json")["measured"])
        states = []
        for k in range(1, 26):
            crowd.step(0.2 * k)
            if save:
                states.append(crowd.people_state())
        return crowd, states
    saving, states = run(True)
    plain, _ = run(False)
    assert all("amp" in d for d in states[-1]["densities"][:2])  # still measured
    assert states[-1] == saving.people_state() == plain.people_state()
    assert np.array_equal(saving.core.hyp_weights(), plain.core.hyp_weights())
    assert saving.core.count_distribution() == plain.core.count_distribution()


def test_a_state_saved_by_0381_is_read_and_written_alike():
    # the format (hidden.Hidden.to_dict until 0.38.1) is the core's now: what 0.38.1 saved, the core
    # reads on the same tiles (their fingerprint is the same string) and writes back with the same
    # masses (float16; a person scaled to sum 1 again; the zlib bytes may differ)
    from test_existence import _nook_config
    for name, config in (("nook", _nook_config()), ("sitter", flat_config(entry=True)), ("measured", flat_config())):
        saved = fixture("people.json")[name]
        tr = Tracker(config)
        assert tr.tiles()["id"] == saved["tiles"]
        assert tr.restore_people(saved)
        again = tr.people_state()
        assert again["hyps"] == saved["hyps"] and again["tiles"] == saved["tiles"]
        for a, b in zip(again["densities"], saved["densities"], strict=True):
            assert set(a) == set(b) and a["r"] == b["r"] and a["out"] == b["out"], name
            for k in ("walk", "still", "region"):
                assert np.allclose(unpack(a[k]) * a["scale"], unpack(b[k]) * b["scale"], rtol=1e-3, atol=1e-9), (name, k)
            if "amp" in b:
                assert np.allclose(unpack(a["amp"]), unpack(b["amp"]), rtol=1e-3), name
