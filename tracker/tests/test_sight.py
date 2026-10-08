"""The radar can't see through walls (MODEL.md 4.1): a target accepted at a wall comes from somebody on
the sensor's side of it."""

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.frames import detections
from presence_tracker.model import Config


def two_rooms() -> Config:
    """A bathroom (y 0-3) and a study (y 3-6) behind the wall y = 3 (door at its right end), each with
    its own sensor in a corner."""
    def rect(zid, x0, y0, x1, y1):
        return {"id": zid, "name": zid, "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2]}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "s-bad", "name": "bad", "x": 0.05, "y": 0.05, "heading": 45, "placed": True},
            {"id": "s-buero", "name": "buero", "x": 3.95, "y": 5.95, "heading": 225, "placed": True},
        ],
        "zones": [rect("bad", 0, 0, 4, 3), rect("buero", 0, 3, 4, 6)],
        "walls": [w((0, 0), (4, 0)), w((4, 0), (4, 6)), w((4, 6), (0, 6)), w((0, 6), (0, 0)), w((0, 3), (4, 3))],
        "doors": [{"id": "tuer", "x": 3.5, "y": 3.0, "width": 0.8}],
    })


def target(s, x, y):
    lx, ly = s.to_local(x, y)
    return {"slot": 1, "x": round(lx * 1000), "y": round(ly * 1000), "speed": 0}


def test_somebody_at_the_wall_the_sensor_hangs_on_is_measured_in_the_room():
    # 0.20.0: a target a few centimetres beyond the wall the sensor hangs on (x = 0) is a measurement
    # (somebody sitting at it), and so is one 0.3 m beyond the wall to the study (the 0.4 m of the
    # measurement error); 0.6 m beyond it is a reflection. The person measured at the own wall is in
    # the bathroom, in the sensor's sight
    config = two_rooms()
    bad, buero = config.sensors
    assert [detections(config, bad, {"targets": [target(bad, x, y)]})[0].hidden
            for x, y in [(-0.05, 2.0), (1.0, 3.3), (1.0, 3.6)]] == [False, False, True]
    crowd = Tracker(config, start=0.0)
    rng = np.random.default_rng(2)
    for i in range(600):
        t = 0.1 * i
        x, y = -0.05 + rng.normal(0, 0.02), 2.0 + rng.normal(0, 0.02)
        crowd.process_frame(bad.id, t, {"targets": [target(bad, x, y)]})
        crowd.process_frame(buero.id, t + 0.05, {"targets": []})
    assert 1 - crowd.count_distribution()["bad"][0] > 0.9
    assert crowd.occupancy()["bad"][0]
    (p,) = [p for p in crowd.persons() if p.get("x") is not None]
    assert 0.0 <= p["x"] < 0.5 and 1.5 < p["y"] < 2.5


def test_a_track_at_the_wall_never_puts_somebody_behind_it():
    # 8.10. 21:35: somebody sitting in the bathroom, their track jumps to the wall to the study and
    # 0.3 m beyond it and back. The Kalman update pulled the person into the study (the light there
    # went on). Now they stay in the bathroom, and the study's own-LD2450 rule does not fire.
    config = two_rooms()
    bad, buero = config.sensors
    crowd = Tracker(config, start=0.0)
    seat = (1.0, 2.2)
    path = []
    for k in range(4):  # sitting, then the track jumps to the wall and beyond, and back
        path += [seat] * 150
        path += [(1.0, 2.2 + 0.2 * i) for i in range(1, 5)]  # 2 m/s to the wall
        path += [(1.0, 3.0 + 0.1 * i) for i in range(1, 4)] + [(1.0, 3.3)] * 5 + [(1.0, 3.0)]
        path += [(1.0, 3.0 - 0.2 * i) for i in range(1, 5)]
    prior, worst = None, 0.0  # P(somebody in the study): the unknown people's share, before the first jump
    measured_bad = False
    rng = np.random.default_rng(1)
    for i, (x, y) in enumerate(path):
        t = 0.1 * i
        x, y = x + rng.normal(0, 0.02), y + rng.normal(0, 0.02)  # bit-equal frames would be frozen ones
        crowd.process_frame(bad.id, t, {"targets": [target(bad, x, y)]})
        crowd.process_frame(buero.id, t + 0.05, {"targets": []})
        if t > 5:
            counts = crowd.count_distribution()
            if prior is None and i >= 149:
                prior = 1 - counts["buero"][0]
            worst = max(worst, 1 - counts["buero"][0])
            occ = crowd.occupancy()
            assert not occ["buero"][0], (t, occ["buero"])
            measured_bad |= occ["bad"][0]
    assert worst < prior + 0.01  # 0.25.0: 0.67, the study's light on
    assert measured_bad
    assert 1 - crowd.count_distribution()["bad"][0] > 0.9
