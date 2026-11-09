"""Two people at one spot, a ghost beside a walker, a newcomer walking in (MODEL.md 4.1, 5.5, 10 "Zwei an
einer Stelle", "Bewegung als Merkmal"): two who stand together stay two, a multipath copy beside a walker
does not become a second person who walks along, and somebody walking in alone is a person within
seconds."""

import pytest

from presence_tracker.filter import Tracker
from sim import Person, simulate
from presence_tracker.frames import Frame

from test_existence import in_house, known
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def mean(dist) -> float:
    return sum(k * p for k, p in enumerate(dist))


def test_two_standing_together_stay_two():
    # both come in one after the other and stand 0.25 m apart for five minutes; both LD2450 merge
    # them into one target (resolution 0.6 m): still two, not one
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.0, 2.5), start=5, pauses={3: 360}))
    b = Person(walk((-1.0, 4.2), FLUR_DOOR, (3.25, 2.5), (3.25, 2.5), start=15, pauses={3: 360}))
    crowd = Tracker(config, start=0.0, people=["outside", "outside"])
    end = b.waypoints[3][0] + 300
    for t, sid, frame in simulate([a, b], sim_sensors(config, resolution=0.6), end, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
    d = crowd.core.count_distribution()["wohn"]
    assert d[2] > 0.9, d
    assert abs(in_house(crowd) - 2) < 0.1, in_house(crowd)


@pytest.mark.parametrize("leaver", ["a", "b"])
def test_when_one_of_two_lying_together_gets_up_the_other_is_still_there(leaver):
    # two come in one after the other and lie 0.3 m apart for half an hour (two in bed); both LD2450
    # merge them into one target. How many lie there does not matter (Leon 5.11.: the second sleeper may be
    # lost at night, or be a Störquelle's, MODEL.md 4.5); what matters is the morning: one of them gets up
    # and goes out, and the room stays occupied by the other one
    config = flat_config(entry=True)
    lie, out = 2000, [FLUR_DOOR, (-1.0, 4.0)]
    pa = [(-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.0, 2.5)] + (out if leaver == "a" else [])
    pb = [(-1.0, 4.2), FLUR_DOOR, (3.3, 2.5), (3.3, 2.5)] + (out if leaver == "b" else [])
    a = Person(walk(*pa, start=5, pauses={3: lie + (10 if leaver == "a" else 4000)}))
    b = Person(walk(*pb, start=15, pauses={3: lie + (0 if leaver == "b" else 4000)}))
    t_out = (a if leaver == "a" else b).waypoints[-1][0]
    crowd = Tracker(config, start=0.0, people=["outside", "outside"])
    least = 1.0
    for t, sid, frame in simulate([a, b], sim_sensors(config, resolution=0.6), t_out + 900, rate=4.0, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
        if t > t_out:
            least = min(least, 1 - crowd.core.count_distribution()["wohn"][0])
    assert least > 0.9, least


def test_a_copy_beside_a_walker_does_not_walk_along():
    # one walks in and around the room for three minutes; sensor a's LD2450 reports a copy 0.8 m
    # beside them for 6 s early on (multipath): a minute later one person, not a second one walking along
    # (meanwhile the copy may briefly be somebody: E[people] in the room up to 1.5, the room is lit anyway)
    config = flat_config(entry=True)
    pts = [(-1.0, 4.0), FLUR_DOOR, (1.0, 1.0), (5.0, 1.0), (5.0, 4.0), (1.0, 4.0), (1.0, 1.0), (5.0, 1.0),
           (5.0, 4.0), (1.0, 4.0), (1.0, 1.0), (5.0, 1.0), (5.0, 4.0), (3.0, 2.5), (3.0, 2.5)]
    a = Person(walk(*pts, start=5, speed=0.8, pauses={14: 30}))
    t1 = a.waypoints[3][0] + 1.0  # walking along the bottom wall
    copy = Person([(t, x, y + 0.8) for t, x, y in [(t1 + k * 0.5, *a.position(t1 + k * 0.5)) for k in range(13)]])
    sensors = sim_sensors(config, resolution=0.6)
    sensors[1].blind_to, sensors[1].blind_after = (1,), 0.0
    crowd = Tracker(config, start=0.0, people=["outside"])
    most = 0.0
    for t, sid, frame in simulate([a, copy], sensors, a.waypoints[-1][0] - 1, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
        if t > t1 + 60:
            most = max(most, mean(crowd.core.count_distribution()["wohn"]))
    # 0.63: beside a still target a still person is resolved only at 1.4 / 2.1 m (MODEL.md 4.1): what is left of the
    # copy as a person standing beside the walker fades a little slower (1.10 here, 0.62 under 1.1)
    assert most < 1.15, most
    assert known(crowd) < 1.05, known(crowd)


def test_somebody_walking_in_alone_is_a_person_within_seconds():
    # nobody known in the flat; somebody walks in and across the room: the light is on within 5 s
    # of the first frame that shows them in the room, and they are one person
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (5.0, 1.0), (1.0, 1.0), start=5, speed=0.9))
    crowd = Tracker(config, start=0.0, people=["outside"])
    seen = None
    late = []
    c = config.data["params"]["light_cost"] / (config.data["params"]["light_cost"] + 1.0)
    for t, sid, frame in simulate([a], sim_sensors(config), a.waypoints[-1][0] - 1, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
        if seen is None and frame.get("targets") and t > a.waypoints[1][0]:
            seen = t
        if seen is not None and t > seen + 5:
            late.append(1 - crowd.core.count_distribution()["wohn"][0])
    assert late and min(late) > c, min(late)
    assert abs(in_house(crowd) - 1) < 0.05, in_house(crowd)


def test_one_of_two_merged_into_one_target_leaves_and_the_other_is_still_there():
    # two walk in together and stand side by side for 20 min; the LD2450s see them as one target (the second
    # behind the first), and that target drops out now and then, as the real one does (6.11. 19:28, the bath).
    # Then one goes out and the other stays still, seen again only 30 s later: the room is occupied all the
    # time. Until 0.61 not finding the held target again counted against the second as if she were a target
    # the sensor could find on her own (MODEL.md 4.1 "Verlieren und Wiederfinden"): she was lost.
    config = flat_config(entry=True)
    stand, out = 1200, [FLUR_DOOR, (-1.0, 4.0)]
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.0, 2.5), *out, start=5, pauses={3: stand}))
    b = Person(walk((-1.0, 4.3), FLUR_DOOR, (3.2, 2.6), (3.2, 2.6), start=6, pauses={3: stand + 4000}))
    t_in, t_out = b.waypoints[2][0], a.waypoints[4][0]
    sensors = sim_sensors(config, resolution=0.6, still_dropout=0.05, still_gap=10.0, hold_dropped=True)
    for s in sensors:
        s.blind_to, s.blind_after, s.blind_until = (1,), t_in, t_out + 30
    crowd = Tracker(config, start=0.0, people=["outside", "outside"])
    least = 1.0
    for t, sid, frame in simulate([a, b], sensors, t_out + 300, rate=4.0, plan=config):
        crowd.process_frame(sid, t, Frame.of(frame))
        if t > t_in + 5:
            least = min(least, 1 - crowd.core.count_distribution()["wohn"][0])
    assert least > 0.9, least
