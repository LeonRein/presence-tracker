"""Tracker against simulated scenes in a 6 x 5 m room with two sensors in the lower corners.

Entry (door) in the upper left corner.
"""

import math

import pytest

from presence_tracker.model import Config, SensorConfig, TrackerParams, ZoneConfig
from presence_tracker.sim import Person, SimSensor, simulate
from presence_tracker.tracker import SensorClock, Tracker
from presence_tracker.zones import evaluate

DOOR = (0.5, 4.6)


def room_config(**params) -> Config:
    return Config(
        sensors=[
            SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
            SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True),
        ],
        zones=[
            ZoneConfig("door", "Tür", kind="entry", shape="rect", points=[[0, 4.0], [1.0, 5.0]]),
            ZoneConfig("room", "Raum", kind="room", shape="rect", points=[[0, 0], [6, 5]]),
            ZoneConfig("table", "Tisch", kind="area", shape="rect", points=[[3.5, 1.5], [4.5, 2.5]]),
        ],
        params=TrackerParams(**{"warmup": 0.0, **params}),
    )


def sim_sensors(config: Config, **kw) -> list:
    return [SimSensor(s, **kw) for s in config.sensors]


def run(config, people, duration, sensors=None, sample_every=0.5, seed=1):
    """Feed the simulation into the tracker. Returns [(t, n_confirmed, zone_states)]."""
    sensors = sensors or sim_sensors(config)
    tracker = Tracker(config, start=0.0)
    samples = []
    next_sample = 0.0
    next_step = 0.0
    for t, sid, frame in simulate(people, sensors, duration, seed=seed):
        tracker.process_frame(sid, t, frame)
        if t >= next_step:
            tracker.step(t)
            next_step = t + 0.2
        if t >= next_sample:
            samples.append((t, len(tracker.present()), evaluate(config, tracker)))
            next_sample = t + sample_every
    return tracker, samples


def counts(samples, t0, t1):
    return {n for t, n, _ in samples if t0 <= t <= t1}


def walk(*points, start=0.0, speed=1.0, pauses=None):
    """Waypoints from a polyline at walking speed. pauses: {point index: seconds}."""
    pauses = pauses or {}
    t = start
    out = [(t, *points[0])]
    if 0 in pauses:
        t += pauses[0]
        out.append((t, *points[0]))
    for i, (a, b) in enumerate(zip(points, points[1:]), 1):
        t += math.dist(a, b) / speed
        out.append((t, *b))
        if i in pauses:
            t += pauses[i]
            out.append((t, *b))
    return out


def test_single_person_enters_sits_and_leaves():
    config = room_config()
    person = Person(walk(DOOR, (3, 2.5), (4, 2), (3, 3), DOOR, start=5, pauses={2: 60}))
    end = person.waypoints[-1][0]
    sensors = sim_sensors(config, still_dropout=0.1, still_gap=25, ghost_rate=1.0)
    tracker, samples = run(config, [person], end + 10, sensors)
    assert counts(samples, 7, end - 1) == {1}
    assert counts(samples, end + 5, end + 10) == {0}
    # sitting at the table is reported as still, in the table zone
    mid = [s for t, n, s in samples if 30 <= t <= 50]
    assert all(s["table"].count == 1 and s["table"].still == 1 for s in mid)


def test_ghosts_alone_never_confirm():
    config = room_config()
    sensors = sim_sensors(config, ghost_rate=6.0)
    tracker, samples = run(config, [], 300, sensors)
    assert counts(samples, 0, 300) == {0}


def test_two_people_crossing():
    config = room_config()
    a = Person(walk(DOOR, (1, 1), (5, 4), (5.2, 1), start=2, speed=0.9))
    b = Person(walk(DOOR, (5, 1), (1, 4), (1, 2.5), start=4, speed=0.9))
    tracker, samples = run(config, [a, b], 20)
    assert counts(samples, 7, 14) == {2}


def test_five_people_with_three_target_limit():
    config = room_config(residents=5)  # a party: the household's size is known
    spots = [(1.5, 1.5), (2.5, 3), (3.5, 1.5), (4.5, 3.2), (5, 1.2)]
    people = [Person(walk(DOOR, spot, start=3 * i, pauses={1: 120})) for i, spot in enumerate(spots)]
    sensors = sim_sensors(config, still_dropout=0.05)
    tracker, samples = run(config, people, 100, sensors)
    final = counts(samples, 50, 100)
    assert final and min(final) >= 4 and max(final) <= 5


def test_track_of_a_vanished_person_fades():
    # a person stands in the room and then "disappears" (a confirmed false track): both sensors
    # see the spot well and report nothing, the LD2410C shows only its ghosts: within minutes the
    # track's mass has flowed to the door and faded
    config = room_config()
    person = Person(walk(DOOR, (3, 2.5), start=1, pauses={1: 5}))
    end = person.waypoints[-1][0]
    tracker, samples = run(config, [person], end + 240)
    assert counts(samples, end - 2, end) == {1}
    assert counts(samples, end + 200, end + 240) == {0}
    assert all(s["_total"].count == 0 for t, n, s in samples if t > end + 200)


def test_approaching_zone():
    config = room_config()
    person = Person(walk(DOOR, (2, 2), (4.6, 2.0), start=1, speed=1.0))
    tracker, samples = run(config, [person], person.waypoints[-1][0], sample_every=0.1)
    first_approach = next(t for t, n, s in samples if s["table"].approaching)
    first_inside = next(t for t, n, s in samples if s["table"].count)
    assert 0.3 <= first_inside - first_approach <= 1.5


def test_sensor_clock_removes_jitter():
    clock = SensorClock()
    times = []
    for k in range(200):
        recv = 1000 + k * 0.09 + (0.03 if k % 3 else 0.0)
        times.append(clock(recv, 5000 + k * 90))
    diffs = {round(b - a, 3) for a, b in zip(times[10:], times[11:])}
    assert diffs <= {0.09, 0.091, 0.089}


@pytest.mark.parametrize("mirror", [False, True])
def test_sensor_transform_roundtrip(mirror):
    s = SensorConfig("x", x=1, y=2, heading=30, height=1.5, mirror=mirror)
    wx, wy, ground, slant = s.to_world(0.5, 3.0, 1.5)
    lx, ly = s.to_local(wx, wy)
    assert lx == pytest.approx(0.5) and ly == pytest.approx(3.0)


def test_detections_behind_a_wall_are_reflections():
    # concrete wall at x = 3 (door opening y 4.2-5.0); the sensor at the origin looks along +x
    config = Config(
        sensors=[SensorConfig("a", x=0.0, y=2.0, heading=0, placed=True)],
        walls=[{"points": [[3.0, 0.0], [3.0, 5.0]], "kind": "wall"}],
        doors=[{"id": "d", "x": 3.0, "y": 4.6, "width": 0.8}],
        params=TrackerParams(warmup=0),
    )
    tracker = Tracker(config, start=0.0)
    def frame(k):  # a few millimeters of jitter, as real targets have
        return {"targets": [
            {"slot": 1, "x": k % 3, "y": 4500, "speed": 0},  # 1.5 m behind the wall
            {"slot": 2, "x": k % 3, "y": 3200, "speed": 0},  # 0.2 m behind it: noise at the wall, kept
            {"slot": 3, "x": k % 3, "y": 2000, "speed": 0},  # in front of it
        ]}
    tracker.process_frame("a", 1.0, frame(0))
    hidden = [d.hidden for d in tracker.runtime["a"].detections]
    assert hidden == [True, False, False]
    for k in range(1, 60):
        tracker.process_frame("a", 1.0 + 0.1 * k, frame(k))
        tracker.step(1.0 + 0.1 * k)
    xs = sorted(round(float(tr.position()[0]), 1) for tr in tracker.confirmed())
    assert all(x < 3.4 for x in xs) and len(xs) >= 1


def test_frozen_ld2450_target_is_not_tracked():
    # the LD2450 keeps reporting the last position bit-identically after the person left
    config = room_config()
    tracker = Tracker(config, start=0.0)
    frozen = {"targets": [{"slot": 1, "x": 300, "y": 2000, "speed": 0}]}
    for k in range(100):
        tracker.process_frame("a", 0.1 * k, frozen)
        tracker.step(0.1 * k)
    assert all(d.stale for d in tracker.runtime["a"].detections)
    assert not tracker.confirmed()


def balcony_config() -> Config:
    """The test room with a balcony behind its right wall: concrete, one door, no sensor."""
    w = lambda a, b: {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "a", "x": 0.05, "y": 0.05, "heading": 45, "placed": True},
            {"id": "b", "x": 5.95, "y": 0.05, "heading": 135, "placed": True},
        ],
        "walls": [w((0, 0), (6, 0)), w((6, 0), (6, 5)), w((6, 5), (0, 5)), w((0, 5), (0, 0)),
                  w((6, 1), (7.5, 1)), w((7.5, 1), (7.5, 3)), w((7.5, 3), (6, 3))],
        "doors": [{"id": "balcony", "x": 6.0, "y": 2.0, "width": 0.9}],
        "zones": [{"id": "door", "name": "Tür", "kind": "entry", "shape": "rect", "points": [[0, 4.0], [1.0, 5.0]]}],
        "params": {"warmup": 0},
    })


def run_walls(config, people, duration, sensors=None, **kw):
    sensors = sensors or sim_sensors(config, **kw)
    tracker = Tracker(config, start=0.0)
    samples = []
    next_sample = 0.0
    for t, sid, frame in simulate(people, sensors, duration, walls=config.wall_segments):
        tracker.process_frame(sid, t, frame)
        tracker.step(t)
        if t >= next_sample:
            samples.append((t, len(tracker.present()), evaluate(config, tracker)))
            next_sample = t + 0.5
    return tracker, samples


def test_balcony_is_a_closed_room():
    config = balcony_config()
    assert [r["open"] for r in config.regions.values()] == [False]


def test_out_to_the_balcony_and_back():
    config = balcony_config()
    balcony = next(iter(config.regions))
    person = Person(walk(DOOR, (3, 2.5), (5.5, 2.0), (7.0, 2.0), (5.0, 2.0), (3, 3), start=2, pauses={3: 40}))
    tracker, samples = run_walls(config, [person], person.waypoints[-1][0] + 2)
    on_balcony = [(t, n, s) for t, n, s in samples if 25 <= t <= 50]
    # nobody visible, but the person is counted on the balcony, and in the house
    assert all(n == 0 and s["_total"].count == 1 for t, n, s in on_balcony)
    assert all(s[next(z for z in config.regions[balcony]["rooms"])].count == 1 for t, n, s in on_balcony)
    # back inside: the same track, right away; balcony empty
    end = person.waypoints[-1][0]
    assert counts(samples, end - 1.5, end) == {1}
    assert len(tracker.confirmed()) == 1 and tracker.confirmed()[0].where.region(balcony) < 0.05
    assert tracker.dwell.dwell.get(balcony)  # the stay was learned


def test_echo_in_the_balcony_door_is_no_person():
    config = balcony_config()
    # someone sits in the room; a static reflection shows up in the glass door the whole time
    sitter = Person([(0, 3.0, 2.5), (90, 3.0, 2.5)])
    # in front of the balcony door, like the real ones: one sensor reports it, the other, which
    # sees the spot just as well, never does
    echo = Person([(5, 5.6, 2.0), (90, 5.6, 2.0)])
    sensors = [SimSensor(config.sensors[0]), SimSensor(config.sensors[1], blind_to=(1,))]
    tracker, samples = run_walls(config, [sitter, echo], 90, sensors=sensors)
    assert counts(samples, 20, 90) == {1}
    # the echo was measured all along, it just never became a person
    assert any(abs(d.pos[0] - 5.6) < 0.5 for d in tracker.runtime["a"].detections)


def test_two_people_never_fuse():
    # 19:30: one sits on the sofa, the other comes in, walks right behind them and sits down
    # next to them; up close the radar sees only one target for both
    config = room_config()
    sitter = Person([(0, 3.0, 1.0), (60, 3.0, 1.0)])
    other = Person(walk(DOOR, (3.3, 1.6), (3.35, 1.15), start=5, speed=0.9, pauses={2: 40}))
    # as at 19:32: each sensor sees only one of them, never both at once
    sensors = [SimSensor(config.sensors[0], blind_to=(1,), blind_after=12),
               SimSensor(config.sensors[1], blind_to=(0,), blind_after=12)]
    tracker, samples = run(config, [sitter, other], 55, sensors)
    assert counts(samples, 15, 55) == {2}


def test_ghost_seen_by_one_of_two_sensors_never_becomes_a_person():
    # both sensors look at (3, 2.5) well; only sensor a reports something there, for a minute
    config = room_config()
    tracker = Tracker(config, start=0.0)
    for k in range(660):
        t = k / 11
        tracker.process_frame("a", t, {"targets": [{"slot": 1, "x": 0 + k % 7, "y": 3900 + k % 5, "speed": 0}]})
        tracker.process_frame("b", t + 0.04, {"targets": []})
        tracker.step(t)
    assert not tracker.confirmed()


def test_person_seen_by_both_sensors_confirms_quickly():
    config = room_config()
    person = Person([(0, 3.0, 2.5), (20, 3.0, 2.5)])
    tracker, samples = run(config, [person], 20, sim_sensors(config), sample_every=0.1)
    first = next(t for t, n, _ in samples if n)
    assert first < 2.5


def test_a_moved_sensor_forgets_what_was_learned():
    from presence_tracker.sensormodel import SensorModel
    config = room_config()
    sm = SensorModel(config)
    sm.trials["a"][10, 10] = 5
    sm.clutter["a"][10, 10] = 3
    sm.trials["b"][10, 10] = 7
    sm.forget("a")
    assert sm.trials["a"].sum() == 0 and sm.clutter["a"].sum() == 0 and sm.trials["b"][10, 10] == 7
