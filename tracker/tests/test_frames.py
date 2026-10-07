"""From LD2450 frames to detections (frames.py), plus helpers for the scene tests."""

import math

import numpy as np
import pytest

from presence_tracker.frames import SensorClock, detections
from presence_tracker.model import Config, SensorConfig, ZoneConfig
from presence_tracker.filter import Tracker
from presence_tracker.sim import SimSensor


def room_config() -> Config:
    return Config(
        sensors=[
            SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
            SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True),
        ],
        zones=[ZoneConfig("room", "Raum", kind="room", shape="rect", points=[[0, 0], [6, 5]])],
    )


def sim_sensors(config: Config, **kw) -> list:
    return [SimSensor(s, **kw) for s in config.sensors]


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
    # concrete wall at x = 3 (door opening y 4.2-5.0); the sensor looks along +x
    config = Config(
        sensors=[SensorConfig("a", x=0.0, y=2.0, heading=0, placed=True)],
        walls=[{"points": [[3.0, 0.0], [3.0, 5.0]], "kind": "wall"}],
        doors=[{"id": "d", "x": 3.0, "y": 4.6, "width": 0.8}],
    )
    frame = {"targets": [
        {"slot": 1, "x": 0, "y": 4500, "speed": 0},  # 1.5 m behind the wall
        {"slot": 2, "x": 0, "y": 3200, "speed": 0},  # 0.2 m behind it: noise at the wall, kept
        {"slot": 3, "x": 0, "y": 2000, "speed": 0},  # in front of it
    ]}
    assert [d.hidden for d in detections(config, config.sensors[0], frame)] == [True, False, False]


def test_behind_a_wall_is_measured_across_it_not_along_the_line_of_sight():
    # MODEL.md 4.1: a sensor hanging on a wall (x = 0) looks along it. A person sitting at that wall is
    # measured a few centimetres beyond it now and then; along the line of sight such a point lies
    # far behind the wall (7.10. 22:10, Bad and Schlafzimmer: 11 % / 20 % of the still targets were
    # thrown away), across it 0.1 m: noise at the wall, kept. 0.6 m beyond it: a reflection.
    s = SensorConfig("a", x=0.0, y=0.0, heading=60, height=1.0, placed=True)
    config = Config(sensors=[s], walls=[{"points": [[0.0, -1.0], [0.0, 6.0]], "kind": "wall"}])

    def target(x, y):
        lx, ly = s.to_local(x, y)
        return {"slot": 1, "x": round(lx * 1000), "y": round(ly * 1000), "speed": 0}

    hidden = [detections(config, s, {"targets": [target(x, y)]})[0].hidden
              for x, y in [(-0.1, 3.0), (0.2, 3.0), (-0.6, 3.0)]]
    assert hidden == [False, False, True]


def test_a_few_ghosts_do_not_make_a_spot_a_ghost_source_many_do():
    # MODEL.md 4.2: the prior weighs one ghost per cell. Three tracks taken for ghosts in 2.5 h (a seat
    # the filter got wrong, 6.10.) made the spot 13 times the prior with the weight of 4 h of watching;
    # a reflection that shows up 30 times a day is still learned
    tr = Tracker(room_config())
    gm = tr.ghost_map
    spot = np.array([[2.0, 2.0]])
    for _ in range(3):
        gm.add_birth("a", spot[0], 1.0)
    gm.add_watch("a", 2.5 * 3600)
    assert 1.0 < gm.rate("a", spot)[0] / gm.prior_rate < 2.5
    for _ in range(27):
        gm.add_birth("a", spot[0], 1.0)
    gm.add_watch("a", 21.5 * 3600)
    assert gm.rate("a", spot)[0] / gm.prior_rate > 5


def test_a_map_learned_with_another_prior_starts_over():
    tr = Tracker(room_config())
    gm = tr.ghost_map
    gm.add_birth("a", (2.0, 2.0), 1.0)
    gm.prior_time /= 24
    assert not tr.use_ghost_map(gm) and not tr.ghost_map.count


def test_a_moved_sensor_starts_the_ghost_map_over():
    config = room_config()
    tr = Tracker(config)
    gm = tr.ghost_map
    gm.add_birth("a", (2.0, 2.0), 1.0)
    assert tr.use_ghost_map(gm)  # the same poses: kept
    for change in ({"x": 0.3}, {"heading": 50}, {"height": 1.8}, {"mirror": True}, {"scale": 1.05}):
        moved = room_config()
        for k, v in change.items():
            setattr(moved.sensors[0], k, v)
        moved.rebuild()
        tr.reconfigure(moved)
        assert tr.ghost_map is not gm and not tr.ghost_map.count, change
        tr.reconfigure(room_config())
        assert tr.use_ghost_map(gm)
