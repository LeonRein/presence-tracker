"""From LD2450 frames to detections (in the core: frames.rs), plus helpers for the scene tests."""

import json
import math

import numpy as np
import pytest

from presence_tracker.frames import Frame, SensorClock
from presence_tracker.model import Config
from presence_tracker.filter import Tracker
from sim import SimSensor, geometry, ld_prior

from fixture import fixture


def local(s, x, y) -> tuple:
    """A point of the house in sensor s's frame, as it measures it (the core's geometry)."""
    return tuple(geometry(Config.from_dict({"sensors": [s]})).to_local(s["id"], [(x, y)])[0])


def room_config() -> Config:
    return Config.from_dict(dict(
        sensors=[
            dict(id="a", name="", x=0.05, y=0.05, heading=45, placed=True),
            dict(id="b", name="", x=5.95, y=0.05, heading=135, placed=True),
        ],
        zones=[{"id": "room", "name": "Raum", "kind": "room", "shape": "rect", "points": [[0, 0], [6, 5]]}],
    ))


def sim_sensors(config: Config, **kw) -> list:
    return [SimSensor(s, **kw) for s in config.data["sensors"]]


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


def detections(config: Config, s: dict, frame: dict) -> list:
    """The detections of one frame of sensor s as a tracker reads it."""
    tr = Tracker(config, start=0.0)
    tr.process_frame(s["id"], 0.0, Frame.of(frame))
    return tr.runtime[s["id"]].detections


def test_sensor_clock_removes_jitter():
    clock = SensorClock()
    times = []
    for k in range(200):
        recv = 1000 + k * 0.09 + (0.03 if k % 3 else 0.0)
        times.append(clock(recv, Frame.of({"uptime_ms": 5000 + k * 90})))
    diffs = {round(b - a, 3) for a, b in zip(times[10:], times[11:])}
    assert diffs <= {0.09, 0.091, 0.089}


def test_detections_behind_a_wall_are_reflections():
    # concrete wall at x = 3 (door opening y 4.2-5.0); the sensor looks along +x
    config = Config.from_dict(dict(
        sensors=[dict(id="a", name="", x=0.0, y=2.0, heading=0, placed=True)],
        walls=[{"points": [[3.0, 0.0], [3.0, 5.0]], "kind": "wall"}],
        doors=[{"id": "d", "x": 3.0, "y": 4.6, "width": 0.8}],
    ))
    frame = {"targets": [
        {"slot": 1, "x": 0, "y": 4500, "speed": 0},  # 1.5 m behind the wall
        {"slot": 2, "x": 0, "y": 3200, "speed": 0},  # 0.2 m behind it: noise at the wall, kept
        {"slot": 3, "x": 0, "y": 2000, "speed": 0},  # in front of it
    ]}
    assert [d.hidden for d in detections(config, config.data["sensors"][0], frame)] == [True, False, False]


def test_behind_a_wall_is_measured_across_it_not_along_the_line_of_sight():
    # MODEL.md 4.1: a sensor hanging on a wall (x = 0) looks along it. A person sitting at that wall is
    # measured a few centimetres beyond it now and then; along the line of sight such a point lies
    # far behind the wall (7.10. 22:10, Bad and Schlafzimmer: 11 % / 20 % of the still targets were
    # thrown away), across it 0.1 m: noise at the wall, kept. 0.6 m beyond it: a reflection.
    s = dict(id="a", name="", x=0.0, y=0.0, heading=60, height=1.0, placed=True)
    config = Config.from_dict({"sensors": [s], "walls": [{"points": [[0.0, -1.0], [0.0, 6.0]], "kind": "wall"}]})

    def target(x, y):
        lx, ly = local(s, x, y)
        return {"slot": 1, "x": round(lx * 1000), "y": round(ly * 1000), "speed": 0}

    hidden = [detections(config, s, {"targets": [target(x, y)]})[0].hidden
              for x, y in [(-0.1, 3.0), (0.2, 3.0), (-0.6, 3.0)]]
    assert hidden == [False, False, True]


def _changed(**change) -> Config:
    d = room_config().to_dict()
    d["sensors"][0].update(change)
    return Config.from_dict(d)


def test_the_ld2410c_background_stays_only_where_the_sensors_hang_as_they_hung():
    # MODEL.md 4.3, 9 "Gelerntes und Geometrie" (0.51): any change of a sensor's place or reading starts it over
    # for every sensor; a name or a sensor switched off keeps it
    for change, kept in (({"heading": 48, "scale": 1.05}, False), ({"x": 0.3, "mirror": True}, False),
                         ({"x": 1.0}, False), ({"enabled": False}, True), ({"name": "Ecke"}, True)):
        tr = Tracker(room_config())
        prior = ld_prior()
        bg = tr.learned()["ld_background"]
        bg["num"]["a"], bg["den"]["a"] = list(prior * 3600.0 * 2), 3600.0
        bg["num"]["b"], bg["den"]["b"] = list(prior * 3600.0 * 3), 3600.0
        tr.use_ld_background(bg)
        learned = tr.learned()["ld_background"]
        tr.reconfigure(_changed(**change))
        now = tr.learned()["ld_background"]
        assert now["num"].get("a") == (learned["num"]["a"] if kept else None), change
        assert now["num"].get("b") == (learned["num"]["b"] if kept else None), change
        # what the core wrote it reads back the same (the format in Python's: a dict made by hand above;
        # ld2410/background.rs the_format_comes_back_and_a_broken_one_is_named)
        again = Tracker(tr.config)
        assert again.use_ld_background(json.loads(json.dumps(now))) == []
        for sid in ("a", "b"):
            assert [x.tolist() if hasattr(x, "tolist") else x for x in again.core.background(sid)] == \
                [x.tolist() if hasattr(x, "tolist") else x for x in tr.core.background(sid)]


def test_a_background_saved_by_0381_is_read_and_written_alike():
    # what ld2410.Background of 0.38.1 wrote (two sensors, each with its level, anchor and echoes, one held
    # after "Tracks zurücksetzen"; fixtures/ld2410.json): the core reads it as that read it - the
    # background, with the anchor it goes back to, and the echo rate - and writes it back the same
    ld = fixture("ld2410.json")
    tr = Tracker(room_config())
    assert tr.use_ld_background(ld["background"]) == []
    for sid in ("a", "b"):
        b, rate = tr.core.background(sid)
        assert np.allclose(b, ld["b"][sid], rtol=1e-12, atol=0) and rate == pytest.approx(ld["rate"][sid], rel=1e-12)
    assert tr.learned()["ld_background"] == ld["background"]
