import copy

import numpy as np
import pytest

from presence_tracker.calibration import MIN_STRETCHES, STRETCH, Calibrator, Problem, more_out_of_sight, solve
from presence_tracker.model import Config, SensorConfig, TrackerParams
from presence_tracker.sim import Person, SimSensor, simulate

from test_frames import walk


def _collect(truth, guess, people, duration, walls=(), hours=MIN_STRETCHES):
    """The app's calibration data: raw frames of simulated sensors (placed as in `truth`) into a
    Calibrator that knows only `guess`; the same walk once in each of `hours` hours (independent
    stretches of everyday life)."""
    calibrator = Calibrator(guess)
    for k in range(hours):
        sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
        for t, sid, frame in simulate(people, sensors, duration, walls=walls, seed=k + 1):
            calibrator.on_frame(sid, t + k * STRETCH, frame)
    status = calibrator.status()
    assert status["since"] is not None and all(n > 0 for n in status["frames"].values())
    return calibrator


def _session(truth, guess, people, duration, walls=(), check_mirror=False, hours=MIN_STRETCHES):
    calibrator = _collect(truth, guess, people, duration, walls, hours)
    return solve(guess, calibrator.data(), check_mirror)


def _turn(r, heading):
    return (r["heading"] - heading + 180) % 360 - 180


def _three_sensors():
    # radars that measure 7 % too short and 5 % too long
    truth = Config(sensors=[
        SensorConfig("a", x=0.05, y=0.05, heading=45, placed=True),
        SensorConfig("b", x=5.95, y=0.05, heading=135, placed=True, scale=1.07),
        SensorConfig("c", x=3.0, y=4.95, heading=270, placed=True, mirror=True, scale=0.95),
        SensorConfig("d", x=20.0, y=20.0, heading=180, placed=True),  # sees nothing of the walk
    ], params=TrackerParams())
    # what the user drew: the right positions and mirrors (as installed), headings off by 15-30
    # degrees, no scales
    guess = copy.deepcopy(truth)
    guess.sensors[0].heading = 60
    guess.sensors[1].heading, guess.sensors[1].scale = 110, 1.0
    guess.sensors[2].heading, guess.sensors[2].scale = 240, 1.0
    guess.rebuild()
    path = [(1, 1), (5, 1), (5, 4), (1, 4), (1, 1), (5, 4), (3, 1), (3, 4)]
    person = Person(walk(*path, *path[::-1], *path, speed=0.8))
    return truth, guess, person


def test_recovers_headings_and_scales_from_a_walk():
    truth, guess, person = _three_sensors()
    result = _session(truth, guess, [person], person.waypoints[-1][0])
    assert result["unsolved"] == ["d"]
    for s in truth.sensors[:3]:
        r = result["sensors"][s.id]
        assert "x" not in r and "y" not in r
        assert r["quality"] == "ok", r["reason"]
        assert r["scale"] == pytest.approx(s.scale, abs=0.02)
        assert _turn(r, s.heading) == pytest.approx(0, abs=2.0)
        assert r["mirror"] == s.mirror
        assert r["heading_sd"] < 4 and r["scale_sd"] < 0.05


def test_the_mirror_stays_as_installed_unless_asked():
    # c drawn with the wrong x direction: without asking, the solver doesn't touch the mirror (it is
    # how the sensor is installed) and finds no heading that fits; asked, it finds the mirror
    truth, guess, person = _three_sensors()
    guess.sensors[2].mirror = False
    guess.rebuild()
    plain = _session(truth, guess, [person], person.waypoints[-1][0])["sensors"]["c"]
    assert plain["mirror"] is False
    checked = _session(truth, guess, [person], person.waypoints[-1][0], check_mirror=True)["sensors"]["c"]
    assert checked["mirror"] is True and checked["mirror_gain"] > 3
    assert _turn(checked, 270) == pytest.approx(0, abs=2.0)
    # and a right mirror stays when asked
    good = _session(truth, copy.deepcopy(truth), [person], person.waypoints[-1][0], check_mirror=True)["sensors"]["a"]
    assert good["mirror"] is False and good["mirror_gain"] < 0


def _two_rooms():
    """Two rooms 5 x 4 m side by side, a door (1 m) in the wall between them; one sensor in each
    room, in the far corner: they see the same spot only in and right behind the door."""
    def rect(zid, x0, y0, x1, y1):
        return {"id": zid, "name": zid, "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2]}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "a", "x": 0.05, "y": 0.05, "heading": 35, "placed": True, "mirror": True, "scale": 1.04},
            {"id": "b", "x": 9.95, "y": 0.05, "heading": 145, "placed": True, "mirror": True, "scale": 1.08},
        ],
        "zones": [rect("links", 0, 0, 5, 4), rect("rechts", 5, 0, 10, 4)],
        "walls": [w((0, 0), (10, 0)), w((10, 0), (10, 4)), w((10, 4), (0, 4)), w((0, 4), (0, 0)), w((5, 0), (5, 4))],
        "doors": [{"id": "tuer", "x": 5.0, "y": 2.5, "width": 1.0}],
    })


def test_sensors_that_share_only_a_door():
    truth = _two_rooms()
    guess = copy.deepcopy(truth)
    guess.sensors[0].heading, guess.sensors[0].scale = 50, 1.0
    guess.sensors[1].heading, guess.sensors[1].scale = 125, 1.0
    guess.rebuild()
    left = [(1, 1), (4, 1), (4, 3), (1, 3), (1, 1.5), (3.5, 3.5), (2, 0.6)]
    right = [(6, 1), (9, 1), (9, 3), (6, 3), (8.5, 0.6), (6.5, 3.5), (9, 2)]
    door = [(4.3, 2.5), (5.7, 2.5)]
    path = [*left, *door, *right, *door[::-1], *left[::-1], *door, *right[::-1], *door[::-1], (2, 2)]
    person = Person(walk(*path, speed=0.8))
    result = _session(truth, guess, [person], person.waypoints[-1][0], walls=truth.wall_segments)
    for s in truth.sensors:
        r = result["sensors"][s.id]
        assert r["mirror"] is True
        assert r["apply"]["heading"], r["reason"]
        assert _turn(r, s.heading) == pytest.approx(0, abs=3.0)
        assert r["outside"][1] <= r["outside"][0]


def _door_walk():
    left = [(1, 1), (4, 1), (4, 3), (1, 3), (1, 1.5), (3.5, 3.5), (2, 0.6)]
    right = [(6, 1), (9, 1), (9, 3), (6, 3), (8.5, 0.6), (6.5, 3.5), (9, 2)]
    door = [(4.3, 2.5), (5.7, 2.5)]
    return Person(walk(*left, *door, *right, *door[::-1], (2, 2), speed=0.8))


def test_a_single_walk_proposes_nothing():
    # one stretch can't show whether a pose holds in other situations (7.10.: a walk alone turned
    # the hall by 17 deg, self-consistently, and everyday tracking got worse)
    truth = _two_rooms()
    guess = copy.deepcopy(truth)
    guess.sensors[1].heading = 125
    guess.rebuild()
    person = _door_walk()
    result = _session(truth, guess, [person], person.waypoints[-1][0], walls=truth.wall_segments, hours=1)
    for sid, r in result["sensors"].items():
        assert not r["apply"]["heading"] and not r["apply"]["scale"]
        assert r["heading"] == guess.sensor_by_id[sid].heading
        assert r["few"] and r["reason"] == "Erst 1 von 3 Stunden mit Gehenden."
    # the progress before a solve counts as the solve does
    calibrator = _collect(truth, guess, [person], person.waypoints[-1][0], walls=truth.wall_segments, hours=1)
    status = calibrator.status()
    assert status["min_hours"] == MIN_STRETCHES and set(status["hours"].values()) == {1}
    assert status["points"] == {sid: r["points"] for sid, r in result["sensors"].items()}


def test_no_pose_that_puts_more_walking_out_of_sight():
    truth = _two_rooms()
    person = _door_walk()
    data = _collect(truth, truth, [person], person.waypoints[-1][0], truth.wall_segments, hours=1).data()
    prob = Problem(truth, data, ["a", "b"])
    right = prob.start()
    turned = right.copy()
    turned[2] += np.radians(25)  # b turned by 25 deg: its walk into the neighbouring room and the wall
    assert more_out_of_sight(prob, right, turned, "b")
    assert not more_out_of_sight(prob, turned, right, "b")
    assert not more_out_of_sight(prob, right, right, "a")


def test_the_collected_data_survive_a_restart(tmp_path):
    truth, guess, person = _three_sensors()
    calibrator = _collect(truth, guess, [person], 30.0, hours=1)
    calibrator.save(tmp_path / "calibration.npz")
    again = Calibrator(guess)
    again.load(tmp_path / "calibration.npz")
    a, b = calibrator.data(), again.data()
    assert a.keys() == b.keys() and all(np.array_equal(a[k], b[k]) for k in a)
    assert again.since == calibrator.since
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    for t, sid, frame in simulate([person], sensors, 10.0):
        again.on_frame(sid, t + 100.0, frame)
    new = again.data()["a"]
    assert new[len(a["a"]):, 1].min() > a["a"][:, 1].max()  # new tracks, not merged with old ones


def test_second_person_sitting_still_does_not_disturb():
    truth, guess, walker = _three_sensors()
    sitter = Person([(0, 4.5, 3.5), (walker.waypoints[-1][0], 4.5, 3.5)])
    r = _session(truth, guess, [walker, sitter], walker.waypoints[-1][0])["sensors"]["b"]
    assert r["quality"] != "bad"
    assert r["scale"] == pytest.approx(1.07, abs=0.02) and r["mirror"] is False
    assert _turn(r, 135) == pytest.approx(0, abs=3)


def test_someone_only_sitting_gives_no_nonsense():
    # the case that produced 7 m / 100 degree nonsense from everyday data: one spot, seen by all
    # three. What is proposed must be right within its stated uncertainty (here the headings follow
    # from the spot's distances to the sensors and the prior on the LD2450's scales).
    truth, guess, _ = _three_sensors()
    sitter = Person([(0, 3.0, 2.5), (120, 3.0, 2.5)])
    r = _session(truth, guess, [sitter], 120)
    for s in truth.sensors[:3]:
        if "error" in r or s.id not in r["sensors"]:
            continue
        x = r["sensors"][s.id]
        if x["apply"]["heading"]:
            assert abs(_turn(x, s.heading)) <= 2 * x["heading_sd"]
        if x["apply"]["scale"]:
            assert abs(x["scale"] - s.scale) <= 2 * x["scale_sd"]


def test_reset_forgets_and_only_walking_is_kept():
    truth, guess, person = _three_sensors()
    calibrator = Calibrator(guess)
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    sitter = Person([(0, 3.0, 2.5), (60, 3.0, 2.5)])
    for t, sid, frame in simulate([sitter], sensors, 60):
        calibrator.on_frame(sid, t, frame)
    walking = sum(len(a) for a in calibrator.data().values())
    for t, sid, frame in simulate([person], sensors, 60):
        calibrator.on_frame(sid, t + 60, frame)
    assert sum(len(a) for a in calibrator.data().values()) > 5 * max(walking, 1)
    calibrator.reset()
    assert calibrator.data() == {} and calibrator.status()["since"] is None


def test_the_status_counts_a_copy_and_a_reset_drops_a_count_under_way(tmp_path):
    """The app counts in a thread (Calibrator.status_job) what was collected when the count began;
    frames that come meanwhile don't change it, and a count begun before a reset is not taken over."""
    truth, guess, person = _three_sensors()
    calibrator = _collect(truth, guess, [person], 30.0, hours=1)
    counted = calibrator.status()
    calibrator._status = None  # due
    job = calibrator.status_job()
    sensors = [SimSensor(s, noise=0.05) for s in truth.sensors]
    for t, sid, frame in simulate([person], sensors, 30.0, seed=7):
        calibrator.on_frame(sid, t + 100.0, frame)  # while the thread counts
    result = job()
    assert result[2] == counted
    calibrator.use_status(result)
    assert calibrator.last_status() == counted and not calibrator.status_due()
    job = calibrator.status_job()
    calibrator.reset()
    calibrator.use_status(job())
    assert calibrator.last_status() is None and calibrator.status()["frames"] == {}
    # the file: uncompressed, read back as it was
    calibrator = _collect(truth, guess, [person], 30.0, hours=1)
    calibrator.save(tmp_path / "calibration.npz")
    again = Calibrator(guess)
    again.load(tmp_path / "calibration.npz")
    assert again.status()["frames"] == calibrator.status()["frames"]
