import copy

import pytest

from presence_tracker.calibration import Calibrator
from presence_tracker.model import Config, SensorConfig, TrackerParams
from presence_tracker.sim import Person, SimSensor, simulate
from presence_tracker.tracker import Tracker

from test_tracker import walk


def test_recovers_poses_from_a_walk():
    truth = Config(sensors=[
        SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
        SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True),
        SensorConfig("c", x=3.0, y=4.95, heading=270, placed=True, mirror=True),
    ], params=TrackerParams(warmup=0))
    # what the user drew: off by decimeters and degrees, c's x direction unknown
    guess = copy.deepcopy(truth)
    guess.sensors[1].x, guess.sensors[1].y, guess.sensors[1].heading = 5.6, 0.4, 125
    guess.sensors[2].x, guess.sensors[2].heading, guess.sensors[2].mirror = 2.7, 260, False
    guess.rebuild()

    person = Person(walk((1, 1), (5, 1), (5, 4), (1, 4), (1, 1), (5, 4), (3, 1), (3, 4), speed=0.8))
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    tracker = Tracker(guess, start=0.0)
    calibrator = Calibrator(guess)
    calibrator.start()
    tracker.listeners.append(lambda e, d: e == "frame" and calibrator.on_frame(*d))
    for t, sid, frame in simulate([person], sensors, person.waypoints[-1][0]):
        tracker.process_frame(sid, t, frame)

    result = calibrator.solve("a")
    assert not result["unsolved"]
    for s in truth.sensors[1:]:
        r = result["sensors"][s.id]
        assert r["x"] == pytest.approx(s.x, abs=0.08)
        assert r["y"] == pytest.approx(s.y, abs=0.08)
        assert (r["heading"] - s.heading + 180) % 360 - 180 == pytest.approx(0, abs=2.0)
        assert r["mirror"] == s.mirror
