"""Decoding the LD2450's own tracks (sensortracks.py, MODEL.md 4.1)."""

import itertools

from presence_tracker.frames import detections
from presence_tracker.sensortracks import COAST, SensorTracks

from test_frames import room_config


def feed(frames):
    """frames: lists of (x mm, y mm, speed) in slot order. Returns the events per frame."""
    config = room_config()
    s = config.sensors[0]
    st = SensorTracks(s.id, itertools.count(1))
    out = []
    for k, targets in enumerate(frames):
        frame = {"targets": [{"slot": i + 1, "x": x, "y": y, "speed": v} for i, (x, y, v) in enumerate(targets)]}
        out.append(st.update(k * 0.089, frame, detections(config, s, frame)))
    return out


def test_a_track_keeps_its_id_when_the_slots_move_up():
    # two targets; the first ends, the second moves up into slot 1: still the same track
    ev = feed([[(0, 2000, 10), (1000, 3000, 20)], [(0, 2010, 30), (1000, 3010, 40)], [(1000, 3020, 50)]])
    a, b = [seg for seg, _ in ev[0].born]
    assert {seg for seg, _ in ev[1].measured} == {a, b}
    assert ev[2].ended == [a] and [seg for seg, _ in ev[2].measured] == [b]


def test_coasting_and_freezing_are_no_measurement():
    frames = [[(0, 2000 + 10 * k, 100 + k)] for k in range(3)]
    frames += [[(0, 2030 + 10 * k, 300)] for k in range(4)]  # the same speed: coasting from the third
    frames += [[(0, 2060, 300)], [(0, 2060, 300)], [(0, 2060, 300)]]  # then repeated bit-identically
    frames += [[(10, 2110, 20)]]  # found again
    frames += [[]]  # gone
    ev = feed(frames)
    seg = ev[0].born[0][0]
    lost = [(k, kind) for k, e in enumerate(ev) for s, kind, _ in e.lost]
    assert lost == [(5, COAST)]
    assert all(not e.measured for e in ev[5:10])  # coasting, then frozen: held, not measured
    assert sum(len(e.held) for e in ev) == 4  # one lost phase (from frame 5), held until found again
    assert [s for s, _ in ev[10].measured] == [seg]  # found again: the same track
    assert ev[11].ended == [seg]


def test_a_target_behind_a_wall_is_born_when_it_comes_into_sight():
    config = room_config()
    s = config.sensors[0]
    st = SensorTracks(s.id, itertools.count(1))
    # sensor a at (0.05, 0.05) looks at 45 degrees; local y far beyond the room (behind its wall)
    far = {"targets": [{"slot": 1, "x": 0, "y": 9000, "speed": 10}]}
    near = {"targets": [{"slot": 1, "x": 0, "y": 8500, "speed": 20}]}
    inside = {"targets": [{"slot": 1, "x": 0, "y": 3000, "speed": 30}]}
    assert not st.update(0.0, far, detections(config, s, far)).born
    assert not st.update(0.09, near, detections(config, s, near)).born
    ev = st.update(0.18, inside, detections(config, s, inside))
    assert len(ev.born) == 1  # a jump of 5.5 m is no continuation: a new target, born where it is seen
