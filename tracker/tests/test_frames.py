"""From LD2450 frames to detections (in the core: frames.rs), plus helpers for the scene tests."""

import json
import math

import numpy as np
import pytest

from presence_tracker.frames import SensorClock
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


def detections(config: Config, s: SensorConfig, frame: dict) -> list:
    """The detections of one frame of sensor s as a tracker reads it."""
    tr = Tracker(config, start=0.0)
    tr.process_frame(s.id, 0.0, frame)
    return tr.runtime[s.id].detections


def test_sensor_clock_removes_jitter():
    clock = SensorClock()
    times = []
    for k in range(200):
        recv = 1000 + k * 0.09 + (0.03 if k % 3 else 0.0)
        times.append(clock(recv, 5000 + k * 90))
    diffs = {round(b - a, 3) for a, b in zip(times[10:], times[11:])}
    assert diffs <= {0.09, 0.091, 0.089}


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


def _changed(**change) -> Config:
    config = room_config()
    for k, v in change.items():
        setattr(config.sensors[0], k, v)
    config.sensors[0]._update()
    config.rebuild()
    return config


def _floor(config, raw) -> np.ndarray:
    """Where sensor a's target at raw (m, its frame) is on the floor, as an array of one point."""
    frame = {"targets": [{"slot": 1, "x": round(raw[0] * 1000), "y": round(raw[1] * 1000), "speed": 0}]}
    return np.array([detections(config, config.sensors[0], frame)[0].pos])


def _ghost_source(tr, raw=(0.6, 4.0), n=30):
    """A reflection the LD2450 a reports at raw (m, its frame) n times in a day."""
    spot = _floor(tr.config, raw)
    for _ in range(n):
        tr.ghost_map.add_birth("a", spot[0], 1.0)
    tr.ghost_map.add_watch("a", 86400.0)
    tr.ghost_map.add_watch("b", 86400.0)
    tr.ghost_map.add_birth("b", (3.0, 2.5), 5.0)
    return spot


def test_a_recalibrated_sensor_keeps_its_ghost_map_in_its_frame():
    # MODEL.md 4.2: a new calibration changes how the sensor's reports are read, not what it reports:
    # its ghosts stay where it reports them, and in the house frame they go with the new pose
    raw = (0.6, 4.0)
    for change in ({"heading": 48}, {"heading": 42}, {"scale": 1.05}, {"scale": 0.95},
                   {"heading": 48, "scale": 1.05}, {"heading": 55}, {"x": 0.35}, {"mirror": True}):
        tr = Tracker(room_config())
        gm = tr.ghost_map
        spot = _ghost_source(tr, raw)
        before = gm.rate("a", spot)[0]
        b_before = gm.rate("b", np.array([[3.0, 2.5]]))[0]
        assert before > 5 * gm.prior_rate
        new = _changed(**change)
        tr.reconfigure(new)
        assert tr.ghost_map is gm and gm.time["a"] == 86400.0, change
        s = new.sensors[0]
        moved = _floor(new, raw)
        # the same cell of its frame; per m^2 of the floor as much less as a cell covers more of it
        k0 = gm.prior_rate * 0.16 * gm.prior_time
        area = lambda scale: k0 / gm.prior_rate + (0.4 * scale) ** 2 * 86400.0  # noqa: E731
        assert gm.rate("a", moved)[0] == pytest.approx(before * area(1.0) / area(s.scale), rel=1e-9), change
        assert gm.rate("b", np.array([[3.0, 2.5]]))[0] == b_before  # the other sensor: untouched
    # turned by 10 deg the reflection 4 m away lands 0.7 m off: the map goes along
    tr = Tracker(room_config())
    spot = _ghost_source(tr, raw)
    tr.reconfigure(_changed(heading=55))
    moved = _floor(tr.config, raw)
    assert np.hypot(*(moved - spot)[0]) > 0.6
    assert tr.ghost_map.rate("a", moved)[0] > 3 * tr.ghost_map.rate("a", spot)[0]


def test_a_remounted_sensor_starts_its_part_of_the_ghost_map_over():
    for change in ({"x": 1.0}, {"x": 0.45, "y": 0.45}, {"heading": 80}, {"heading": 10}, {"height": 2.2}):
        tr = Tracker(room_config())
        gm = tr.ghost_map
        _ghost_source(tr)
        b_before = gm.rate("b", np.array([[3.0, 2.5]]))[0]
        tr.reconfigure(_changed(**change))
        assert "a" not in gm.count and "a" not in gm.time, change
        assert gm.rate("a", np.array([[2.0, 2.0]]))[0] == pytest.approx(gm.prior_rate)
        assert gm.rate("b", np.array([[3.0, 2.5]]))[0] == b_before, change  # the other one keeps its part
    # a sensor removed takes its part along; a new one starts from the prior
    tr = Tracker(room_config())
    _ghost_source(tr)
    config = room_config()
    config.sensors = [config.sensors[1], SensorConfig("c", x=3.0, y=4.95, heading=270, placed=True)]
    config.rebuild()
    tr.reconfigure(config)
    assert set(tr.ghost_map.poses) == {"b", "c"} and set(tr.ghost_map.count) == {"b"}


def test_a_saved_ghost_map_reads_back_the_same():
    from presence_tracker.ghostmap import GhostMap
    tr = Tracker(room_config())
    gm = tr.ghost_map
    gm.add_watch("a", 86400.0)
    gm.add_birth("a", np.array([[2.2, 2.6]]), 12.0)
    centre = np.array([[2.2, 2.6]])
    assert gm.rate("a", centre)[0] > 3 * gm.prior_rate
    again = GhostMap.from_dict(json.loads(json.dumps(gm.to_dict())))
    assert again.use(tr.config) == [] and again.rate("a", centre)[0] == pytest.approx(gm.rate("a", centre)[0])
    # a map of 0.27 or earlier (counts in the house frame) is not read: the app starts a new one
    with pytest.raises(KeyError):
        GhostMap.from_dict({"x0": 0.0, "y0": 0.0, "nx": 1, "ny": 1, "prior_rate": gm.prior_rate,
                            "prior_time": gm.prior_time, "time": {}, "poses": {}, "count": {}})


def test_the_ld2410c_background_stays_with_a_recalibration_not_with_a_remount():
    # its cells are distances from the sensor (MODEL.md 4.3): a new heading or scale changes nothing
    for change, kept in (({"heading": 48, "scale": 1.05}, True), ({"x": 0.3, "mirror": True}, True),
                         ({"x": 1.0}, False), ({"heading": 90}, False)):
        tr = Tracker(room_config())
        bg = tr.ld_background
        bg.num["a"] = bg.prior * 3600.0 * 2
        bg.den["a"] = 3600.0
        bg.num["b"] = bg.prior * 3600.0 * 3
        bg.den["b"] = 3600.0
        learned_a, learned_b = bg.b("a").copy(), bg.b("b").copy()
        tr.reconfigure(_changed(**change))
        assert np.array_equal(bg.b("a"), learned_a if kept else bg.prior), change
        assert np.array_equal(bg.b("b"), learned_b)
