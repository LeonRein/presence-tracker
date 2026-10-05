import copy

import pytest

from presence_tracker.calibration import Calibrator
from presence_tracker.model import Config, SensorConfig, TrackerParams
from presence_tracker.sim import Person, SimSensor, simulate
from presence_tracker.crowd import Crowd

from test_frames import walk


def test_recovers_poses_from_a_walk():
    truth = Config(sensors=[
        SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
        SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True),
        SensorConfig("c", x=3.0, y=4.95, heading=270, placed=True, mirror=True),
    ], params=TrackerParams())
    # what the user drew: positions within 15 cm, headings off by 15-30 degrees (also the first
    # sensor's), c's x direction unknown
    guess = copy.deepcopy(truth)
    guess.sensors[0].x, guess.sensors[0].heading = 0.15, 60
    guess.sensors[1].x, guess.sensors[1].y, guess.sensors[1].heading = 5.85, 0.15, 110
    guess.sensors[2].x, guess.sensors[2].heading, guess.sensors[2].mirror = 2.9, 240, False
    guess.rebuild()

    person = Person(walk((1, 1), (5, 1), (5, 4), (1, 4), (1, 1), (5, 4), (3, 1), (3, 4), speed=0.8))
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    tracker = Crowd(guess, start=0.0)
    calibrator = Calibrator(guess)
    calibrator.start()
    tracker.listeners.append(lambda e, d: e == "frame" and calibrator.on_frame(*d))
    for t, sid, frame in simulate([person], sensors, person.waypoints[-1][0]):
        tracker.process_frame(sid, t, frame)

    result = calibrator.solve()
    assert not result["unsolved"]
    for s in truth.sensors:
        r = result["sensors"][s.id]
        assert r["x"] == pytest.approx(s.x, abs=0.15)
        assert r["y"] == pytest.approx(s.y, abs=0.15)
        assert (r["heading"] - s.heading + 180) % 360 - 180 == pytest.approx(0, abs=2.0)
        assert r["mirror"] == s.mirror
        assert r["quality"] == "ok", r["reason"]


def _session(truth, guess, people, duration):
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    tracker = Crowd(guess, start=0.0)
    calibrator = Calibrator(guess)
    calibrator.start()
    tracker.listeners.append(lambda e, d: e == "frame" and calibrator.on_frame(*d))
    for t, sid, frame in simulate(people, sensors, duration):
        tracker.process_frame(sid, t, frame)
    return calibrator.solve()


def _two_sensors():
    truth = Config(sensors=[
        SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
        SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True, mirror=True),
    ], params=TrackerParams())
    guess = copy.deepcopy(truth)
    guess.sensors[0].heading = 60
    guess.sensors[1].x, guess.sensors[1].heading, guess.sensors[1].mirror = 5.85, 110, False
    guess.rebuild()
    return truth, guess


def test_second_person_sitting_still_does_not_disturb():
    truth, guess = _two_sensors()
    walker = Person(walk((1, 1), (5, 1), (5, 4), (1, 4), (1, 1), (5, 4), (3, 1), (3, 4), speed=0.8))
    sitter = Person([(0, 4.5, 3.5), (walker.waypoints[-1][0], 4.5, 3.5)])
    r = _session(truth, guess, [walker, sitter], walker.waypoints[-1][0])["sensors"]["b"]
    assert r["quality"] != "bad"
    assert r["x"] == pytest.approx(5.95, abs=0.15) and r["mirror"] is True
    assert (r["heading"] - 135 + 180) % 360 - 180 == pytest.approx(0, abs=3)


def test_someone_only_sitting_gives_no_result():
    # the case that produced 7 m / 100 degree nonsense from everyday data
    truth, guess = _two_sensors()
    sitter = Person([(0, 3.0, 2.5), (120, 3.0, 2.5)])
    r = _session(truth, guess, [sitter], 120)
    assert "error" in r or r["unsolved"] == ["b"] or r["sensors"]["b"]["quality"] == "bad"
