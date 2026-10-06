import copy

import pytest

from presence_tracker.calibration import Calibrator
from presence_tracker.model import Config, SensorConfig, TrackerParams
from presence_tracker.sim import Person, SimSensor, simulate
from presence_tracker.filter import Tracker

from test_frames import walk


def test_recovers_headings_and_scales_from_a_walk():
    # radars that measure 7 % too short and 5 % too long
    truth = Config(sensors=[
        SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
        SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True, scale=1.07),
        SensorConfig("c", x=3.0, y=4.95, heading=270, placed=True, mirror=True, scale=0.95),
        SensorConfig("d", x=20.0, y=20.0, heading=180, placed=True),  # sees nothing of the walk
    ], params=TrackerParams())
    # what the user drew: the right positions, headings off by 15-30 degrees, c's x direction
    # unknown, no scales
    guess = copy.deepcopy(truth)
    guess.sensors[0].heading = 60
    guess.sensors[1].heading, guess.sensors[1].scale = 110, 1.0
    guess.sensors[2].heading, guess.sensors[2].mirror, guess.sensors[2].scale = 240, False, 1.0
    guess.rebuild()

    person = Person(walk((1, 1), (5, 1), (5, 4), (1, 4), (1, 1), (5, 4), (3, 1), (3, 4), speed=0.8))
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    tracker = Tracker(guess, start=0.0)
    calibrator = Calibrator(guess)
    calibrator.start()
    tracker.listeners.append(lambda e, d: e == "frame" and calibrator.on_frame(*d))
    for t, sid, frame in simulate([person], sensors, person.waypoints[-1][0]):
        tracker.process_frame(sid, t, frame)

    result = calibrator.solve()
    assert result["unsolved"] == ["d"]
    for s in truth.sensors[:3]:
        r = result["sensors"][s.id]
        assert "x" not in r and "y" not in r
        assert r["scale"] == pytest.approx(s.scale, abs=0.02)
        assert (r["heading"] - s.heading + 180) % 360 - 180 == pytest.approx(0, abs=2.0)
        assert r["mirror"] == s.mirror
        assert r["quality"] == "ok", r["reason"]


def _session(truth, guess, people, duration):
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    tracker = Tracker(guess, start=0.0)
    calibrator = Calibrator(guess)
    calibrator.start()
    tracker.listeners.append(lambda e, d: e == "frame" and calibrator.on_frame(*d))
    for t, sid, frame in simulate(people, sensors, duration):
        tracker.process_frame(sid, t, frame)
    return calibrator.solve()


def _two_sensors():
    truth = Config(sensors=[
        SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
        SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True, mirror=True, scale=1.05),
    ], params=TrackerParams())
    guess = copy.deepcopy(truth)
    guess.sensors[0].heading = 60
    guess.sensors[1].heading, guess.sensors[1].mirror, guess.sensors[1].scale = 110, False, 1.0
    guess.rebuild()
    return truth, guess


def test_second_person_sitting_still_does_not_disturb():
    truth, guess = _two_sensors()
    walker = Person(walk((1, 1), (5, 1), (5, 4), (1, 4), (1, 1), (5, 4), (3, 1), (3, 4), speed=0.8))
    sitter = Person([(0, 4.5, 3.5), (walker.waypoints[-1][0], 4.5, 3.5)])
    r = _session(truth, guess, [walker, sitter], walker.waypoints[-1][0])["sensors"]["b"]
    assert r["quality"] != "bad"
    assert r["scale"] == pytest.approx(1.05, abs=0.02) and r["mirror"] is True
    assert (r["heading"] - 135 + 180) % 360 - 180 == pytest.approx(0, abs=3)


def test_someone_only_sitting_gives_no_result():
    # the case that produced 7 m / 100 degree nonsense from everyday data
    truth, guess = _two_sensors()
    sitter = Person([(0, 3.0, 2.5), (120, 3.0, 2.5)])
    r = _session(truth, guess, [sitter], 120)
    assert "error" in r or "b" in r["unsolved"] or r["sensors"]["b"]["quality"] == "bad"
