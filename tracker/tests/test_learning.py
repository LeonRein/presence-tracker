"""What the app learns online must not learn the filter's own errors back (MODEL.md 4.3, 10 "Lernen
aus dem eigenen Urteil"): the LD2410C background is learned by online EM with the energies that were
capped taken for what they were, not where an echo source holds the energy; the echo sources' rate
only where nobody is in view and judged without itself; the destination map is output only
(test_destination.py). The background's steps by hand: the Rust core (ld2410/background.rs)."""

import numpy as np

from presence_tracker.filter import Tracker
from sim import Person, SimSensor, ld_prior, model, simulate
from presence_tracker.frames import Frame

from test_filter import FLUR_DOOR, flat_config
from test_frames import walk


def _sit(seat, minutes, leave=0.0):
    """Somebody comes in and sits at seat for minutes; the LD2450s see them for about a second every
    15 minutes (still_dropout). Returns the tracker, P(occupied) every 10 min while they sit, and the
    largest P(occupied) from 20 s after they left."""
    config = flat_config(entry=True)
    pts = [(-1.0, 4.0), FLUR_DOOR, seat, (seat[0] + 0.05, seat[1])] + ([FLUR_DOOR, (-1.0, 4.0)] if leave else [])
    p = Person(walk(*pts, start=5, pauses={3: minutes * 60}))
    tr = Tracker(config, start=0.0, people=["outside"])
    sensors = [SimSensor(s, still_dropout=1.0, still_gap=900.0) for s in config.data["sensors"]]
    sat, gone = p.waypoints[3][0], p.waypoints[-1][0]
    occupied, after, nxt = [], 0.0, sat + 600
    for t, sid, frame in simulate([p], sensors, gone + leave, plan=config):
        tr.process_frame(sid, t, Frame.of(frame))
        if t >= nxt and t < sat + minutes * 60:
            occupied.append(1 - tr.core.count_distribution()["wohn"][0])
            nxt += 600
        if leave and t > gone + 20:
            after = max(after, 1 - tr.core.count_distribution()["wohn"][0])
    return tr, occupied, after


def test_a_sitter_for_an_hour_is_not_learned_as_background():
    # MODEL.md 4.3, 10: somebody sits 1.8 m in front of sensor a for an hour, the LD2450s rarely see
    # them. The background stays what the empty room gives (until 0.20: gate 2 of sensor a 5.0 ->
    # 2.2, its echo rate 0.33 -> 0.73 per hour), the person stays, and when they leave nobody is left
    c = 2 / 3
    tr, occupied, after = _sit((1.3, 1.3), 60, leave=60.0)
    prior = ld_prior()
    for s in ("a", "b"):
        assert np.all(np.abs(tr.core.background(s)[0] / prior - 1) < 0.3), (s, tr.core.background(s)[0])
        assert tr.core.background(s)[1] < 1.3 * model()["ld_echo_rate"], (s, tr.core.background(s)[1] * 3600)
    assert min(occupied) > c, occupied
    assert after < c, after


def test_a_new_sensor_learns_an_empty_room_that_is_louder_than_the_prior():
    # the bedroom sensor of 7.10.: its empty room gave 7.8 in every still gate, the prior is 5. While
    # it learns, nobody may appear there (learning only where the filter sees nobody was tried: a
    # phantom held the energy and the background never learned it, 4 h light in the empty bedroom)
    config = flat_config(entry=True)
    louder = ld_prior() * 1.56
    nobody = Person([(0.0, -1.0, 4.0), (1.0, -1.0, 4.0)])  # out in the stairwell, gone
    tr = Tracker(config, start=0.0, people=["outside"])
    sensors = [SimSensor(s) for s in config.data["sensors"]]
    for s in sensors:
        s.ld_background = louder
    worst = 0.0
    for t, sid, frame in simulate([nobody], sensors, 1800, plan=config):
        tr.process_frame(sid, t, Frame.of(frame))
        if t > 60:
            worst = max(worst, 1 - tr.core.count_distribution()["wohn"][0])
    assert worst < 2 / 3, worst
    for s in ("a", "b"):  # from 0.64 of it (the prior) to the empty room
        ratio = tr.core.background(s)[0][9:] / louder[9:]
        assert abs(ratio.mean() - 1) < 0.1 and np.all(np.abs(ratio - 1) < 0.25), tr.core.background(s)[0]


def test_a_sitter_the_model_starts_with_is_found_and_not_learned():
    # nothing known (a start of the app, a new sensor), somebody already sits 1.8 m in front of sensor
    # a. Energy that stays longer than an echo source lives is a person within minutes (until 0.54 an
    # echo source of a that never ended held it, nobody in the room for the whole hour, MODEL.md 4.3,
    # 10 "Echoquellen als Alternative"), and neither sensor learns it (until 0.20 gate 2 of a 5.0 ->
    # 10.6 in an hour; until 0.54 sensor b, 5 m away, 3.6-fold in a gate)
    config = flat_config(entry=True)
    seat = (1.3, 1.3)
    p = Person([(0.0, *seat), (3600.0, seat[0] + 0.05, seat[1])])
    tr = Tracker(config, start=0.0)
    sensors = [SimSensor(s, still_dropout=1.0, still_gap=900.0) for s in config.data["sensors"]]
    found = []
    for t, sid, frame in simulate([p], sensors, 3600, plan=config):
        tr.process_frame(sid, t, Frame.of(frame))
        if t > 600:
            found.append(1 - tr.core.count_distribution()["wohn"][0])
    assert min(found) > 2 / 3, min(found)
    tr.step(t)
    assert tr.zone_states()["wohn"].occupied  # a person of the display: the light follows them, not the mass
    prior = ld_prior()
    for s in ("a", "b"):
        assert np.all(np.abs(tr.core.background(s)[0] / prior - 1) < 0.4), (s, tr.core.background(s)[0])
