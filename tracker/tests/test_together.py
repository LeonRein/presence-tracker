"""Two people at one spot, a ghost beside a walker, a newcomer walking in (MODEL.md 4.1, 5.5, 10 "Zwei an
einer Stelle", "Bewegung als Merkmal"): two who stand together stay two, a multipath copy beside a walker
does not become a second person who walks along, and somebody walking in alone is a person within
seconds."""

from presence_tracker.filter import Tracker
from presence_tracker.sim import Person, simulate

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
    for t, sid, frame in simulate([a, b], sim_sensors(config, resolution=0.6), end, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    d = crowd.count_distribution()["wohn"]
    assert d[2] > 0.9, d
    assert abs(in_house(crowd) - 2) < 0.1, in_house(crowd)


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
    for t, sid, frame in simulate([a, copy], sensors, a.waypoints[-1][0] - 1, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if t > t1 + 60:
            most = max(most, mean(crowd.count_distribution()["wohn"]))
    assert most < 1.1, most
    assert known(crowd) < 1.05, known(crowd)


def test_somebody_walking_in_alone_is_a_person_within_seconds():
    # nobody known in the flat; somebody walks in and across the room: the light is on within 5 s
    # of the first frame that shows them in the room, and they are one person
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (5.0, 1.0), (1.0, 1.0), start=5, speed=0.9))
    crowd = Tracker(config, start=0.0, people=["outside"])
    seen = None
    late = []
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    for t, sid, frame in simulate([a], sim_sensors(config), a.waypoints[-1][0] - 1, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if seen is None and frame.get("targets") and t > a.waypoints[1][0]:
            seen = t
        if seen is not None and t > seen + 5:
            late.append(1 - crowd.count_distribution()["wohn"][0])
    assert late and min(late) > c, min(late)
    assert abs(in_house(crowd) - 1) < 0.05, in_house(crowd)
