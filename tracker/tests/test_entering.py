""""Wird betreten" (MODEL.md 6): P(somebody walking enters a room within the look-ahead), from the
walkers' own motion model, walls reflecting, mixed over the hypotheses. Two rooms with sensors, a
door between them; a hallway with a door into the first."""

from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate

from test_frames import sim_sensors, walk

DOOR = (6.0, 2.5)  # between "a" (x 0-6) and "b" (x 6-10)


def two_rooms() -> Config:
    def rect(zid, x0, y0, x1, y1):
        return {"id": zid, "name": zid, "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2], "entry": False}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "a1", "x": 0.05, "y": 0.05, "heading": 45, "placed": True},
            {"id": "a2", "x": 5.95, "y": 4.95, "heading": 225, "placed": True},
            {"id": "b1", "x": 9.95, "y": 0.05, "heading": 135, "placed": True},
        ],
        "zones": [rect("a", 0, 0, 6, 5), rect("b", 6, 0, 10, 5)],
        "walls": [w((0, 0), (6, 0)), w((6, 0), (10, 0)), w((10, 0), (10, 5)), w((10, 5), (6, 5)),
                  w((6, 5), (0, 5)), w((0, 5), (0, 0)), w((6, 0), (6, 5))],
        "doors": [{"id": "ab", "x": DOOR[0], "y": DOOR[1], "width": 0.9}],
    })


def run(person, until):
    """(t, p_enter of b, approaching b, P(b occupied)) every step of the tracker."""
    config = two_rooms()
    tr = Tracker(config, start=0.0, people=["observed"])
    out, nxt = [], 0.0
    for t, sid, frame in simulate([person], sim_sensors(config), until, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
        if t >= nxt:
            tr.step(t)
            st = tr.zone_states()["b"]
            out.append((t, st.p_enter, st.approaching, st.probability))
            nxt = t + 0.2
    return out


def test_who_walks_at_the_door_makes_the_room_about_to_be_entered_a_second_before():
    a = Person(walk((1.0, 2.5), (1.0, 2.5), (8.5, 2.5), start=1.0, pauses={0: 4}))
    cross = a.waypoints[1][0] + (DOOR[0] - 1.0)  # at 1 m/s
    out = run(a, cross + 2)
    first = next(t for t, _, on, _ in out if on)
    assert first <= cross - 0.9  # lit about a second (a metre) before the door
    assert all(on for t, _, on, _ in out if first <= t <= cross)
    assert all(not on for t, _, on, _ in out if t < cross - 3.0)  # not while still far away


def peak(person):
    return max(p for _, p, _, _ in run(person, person.waypoints[-1][0]))


def test_who_walks_past_the_door_does_not():
    # along the wall with the door, 1.5 m from it, at walking speed: not about to enter
    a = Person(walk((4.5, 0.4), (4.5, 0.4), (4.5, 4.6), start=1.0, pauses={0: 4}))
    out = run(a, a.waypoints[-1][0])
    assert not any(on for _, _, on, _ in out)
    # closer (0.8 m) the model's own motion model leaves room to turn in: some probability, but
    # far less than for whoever walks at the door
    straight = Person(walk((1.0, 2.5), (1.0, 2.5), (8.5, 2.5), start=1.0, pauses={0: 4}))
    close = Person(walk((5.2, 0.4), (5.2, 0.4), (5.2, 4.6), start=1.0, pauses={0: 4}))
    assert peak(close) < 0.4 * peak(straight)


def test_who_walks_along_the_wall_beside_the_door_does_not():
    # 0.5 m from the wall into b, 0.6-1.6 m beside the door, there and back
    a = Person(walk((5.5, 4.6), (5.5, 4.6), (5.5, 3.6), (5.5, 4.6), start=1.0, speed=0.8, pauses={0: 4}))
    out = run(a, a.waypoints[-1][0])
    assert not any(on for _, _, on, _ in out)


def test_nothing_through_walls():
    # straight at the wall between the rooms, away from the door, and back
    a = Person(walk((1.0, 4.3), (1.0, 4.3), (5.6, 4.3), (1.0, 4.3), start=1.0, pauses={0: 4}))
    out = run(a, a.waypoints[-1][0])
    assert not any(on for _, _, on, _ in out)
    assert max(p for _, p, _, _ in out) < 0.02
    # and once the walker is known to be in a, b stays empty
    assert max(q for t, _, _, q in out if t >= a.waypoints[1][0]) < 0.05
