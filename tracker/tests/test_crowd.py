"""The particle model (crowd.py, MODEL.md) on simulated scenes in a small flat: one room with two
sensors, a balcony and a hallway behind doors."""

import numpy as np

from presence_tracker.crowd import Crowd
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate

from test_tracker import sim_sensors, walk

FLUR_DOOR = (0.0, 4.0)
BALCONY_DOOR = (6.0, 2.0)


def flat_config(entry: bool = False, residents: int = 2) -> Config:
    """Room 6 x 5 m (two sensors in the bottom corners), balcony behind the right wall (door at
    (6, 2)), hallway behind the left wall (door at (0, 4))."""
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
        "params": {"warmup": 0, "residents": residents},
    })


def run(config, people, duration, start_places, every=0.5, **kw):
    crowd = Crowd(config, start=0.0, n=600, people=start_places)
    samples = []
    next_sample = 0.0
    for t, sid, frame in simulate(people, sim_sensors(config, **kw), duration, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if t >= next_sample:
            samples.append((t, crowd.place_probabilities(), len(crowd.present())))
            next_sample = t + every
    return crowd, samples


def at(samples, t):
    return min(samples, key=lambda s: abs(s[0] - t))[1]


def present(samples, t0, t1):
    return {n for t, _, n in samples if t0 <= t <= t1}


def test_the_world_knows_rooms_regions_and_walls():
    crowd = Crowd(flat_config(), start=0.0, people=["flur"])
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
    assert at(samples, t[2] + 10)[1].get("observed", 0) > 0.9  # in the room
    on_balcony = at(samples, t[4] + 15)[1]
    assert on_balcony.get("balkon", 0) > 0.8, on_balcony  # on the balcony, not hidden at the door
    assert at(samples, t[6] + 10)[1].get("observed", 0) > 0.9  # back: the same person


def test_one_person_in_the_room_is_one_person():
    # nothing known at the start, two may live here; only one comes in and sits: the other one is
    # not invented somewhere in the room
    config = flat_config(entry=True, residents=2)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (3.1, 2.5), start=3, pauses={2: 90}))
    crowd, samples = run(config, [a], a.waypoints[-1][0], None)
    assert present(samples, 30, a.waypoints[-1][0] - 1) == {1}


import pytest  # noqa: E402


@pytest.mark.xfail(reason="somebody unknown coming in is not built yet (MODEL.md 3.2)", strict=True)
def test_somebody_else_coming_in_is_counted():
    # one person lives here and sits; somebody walks in through the hallway door and stays
    config = flat_config(entry=True, residents=1)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=1, pauses={2: 120}))
    b = Person(walk((-1.0, 4.0), FLUR_DOOR, (2.0, 2.5), (2.1, 2.5), start=40, pauses={2: 60}))
    end = min(a.waypoints[-1][0], b.waypoints[-1][0])
    crowd, samples = run(config, [a, b], end, ["flur"])
    assert present(samples, 20, 38) == {1}
    assert present(samples, 70, end - 1) == {2}
