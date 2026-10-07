""""Ziel" (MODEL.md 6): where a walker goes next, from their motion ("wird betreten") and the map of
where walks went, learned by the app (destination.py). Two rooms with sensors, a door between them
(test_entering)."""

import json

import numpy as np

from presence_tracker import ha
from presence_tracker.destination import DestinationMap
from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate

from test_entering import DOOR, two_rooms
from test_frames import sim_sensors, walk

DESK = (1.5, 2.5)
SOFA = (8.5, 2.5)


def routine(trips: int, start: float = 1.0):
    """From the desk in a to the sofa in b and back, trips times, with pauses."""
    pts, pauses = [DESK], {0: 4}
    for _ in range(trips):
        pts += [SOFA, DESK]
        pauses[len(pts) - 2] = 4
        pauses[len(pts) - 1] = 4
    return Person(walk(*pts, start=start, pauses=pauses))


def run(person, until, tracker=None, learn=True):
    """The tracker after the run, and per step (t, zone states of b, count distribution)."""
    config = two_rooms()
    tr = tracker or Tracker(config, start=0.0, people=["observed"])
    tr.learn_dest = learn
    out, nxt = [], 0.0
    for t, sid, frame in simulate([person], sim_sensors(config), until, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
        if t >= nxt:
            tr.step(t)
            states = tr.zone_states()
            out.append((t, states["b"], tr.count_distribution(), tr.loglik))
            nxt = t + 0.2
    return tr, out


def test_untrained_ziel_is_wird_betreten():
    a = Person(walk((1.0, 2.5), (1.0, 2.5), (8.5, 2.5), start=1.0, pauses={0: 4}))
    tr, out = run(a, a.waypoints[-1][0] + 1)
    assert any(st.approaching for _, st, _, _ in out)
    disc = ha.Discovery(None)
    for t, st, _, _ in out:
        assert st.target == st.approaching
        assert st.target_weight == 0.0
        # and so in what Home Assistant gets: binary_sensor..._ziel is binary_sensor..._approaching
        sent = disc.steady("b", st.to_dict(), t)
        assert sent["target"] == sent["approaching"] == st.approaching
    # on the room's scale: at its threshold exactly where p_enter is at approach_cost's
    on = [st for _, st, _, _ in out if st.approaching]
    assert all(st.p_target >= st.c_target for st in on)


def test_the_map_learns_a_routine_and_predicts_it_earlier():
    trips = 10
    a = routine(trips)
    tr, out = run(a, a.waypoints[-1][0] + 1)
    dm, geo = tr.dest_map, tr.dest_geo
    b = tr.rooms.index("b")
    assert dm.walks >= 2 * trips - 2  # walks a -> b, b -> a (and the stops)
    # halfway from the desk to the door, walking towards it: these walks went to b
    cnt = dm.lookup(*geo.around(3.5, 2.5), 1.0, 0.0)
    assert cnt.sum() >= 0.4 * trips  # each walk counts up to 1 (less where it passes beside the centre)
    assert cnt[b] / cnt.sum() > 0.9
    # walking away from the door there: none of them went to b
    back = dm.lookup(*geo.around(3.5, 2.5), -1.0, 0.0)
    assert back[b] < 0.1 * back.sum()
    # one more trip: "Ziel" comes on well before "wird betreten", from the map
    more = Person([(t + a.waypoints[-1][0], x, y) for t, x, y in routine(1).waypoints])
    _, out = run(more, more.waypoints[-1][0], tracker=tr)
    first_t = next(t for t, st, _, _ in out if st.target)
    first_e = next(t for t, st, _, _ in out if st.approaching)
    assert first_t <= first_e - 1.0
    st = next(st for t, st, _, _ in out if st.target)
    assert st.target_weight > 0.5 and st.target_walks >= 0.25 * trips and st.target_from == "a"
    # and is on most of the time up to the door, all the last 1.5 s (the velocity estimate jitters:
    # where the map knows only a few walks, the motion's part flickers it near its threshold)
    t_door = more.waypoints[1][0] + (DOOR[0] - DESK[0])
    span = [st.target for t, st, _, _ in out if first_t <= t <= t_door - 0.2]
    assert sum(span) >= 0.6 * len(span)
    assert all(st.target for t, st, _, _ in out if t_door - 1.5 <= t <= t_door - 0.2)
    # back from b: not into b
    assert not any(st.target for t, st, _, _ in out if t > t_door + 2.0)


def test_the_map_is_saved_restored_and_belongs_to_its_floor_plan():
    a = routine(2)
    tr, _ = run(a, a.waypoints[-1][0] + 1)
    saved = json.loads(json.dumps(tr.learned()))
    other = Tracker(two_rooms(), start=0.0, people=["observed"])
    assert other.dest_map.count.sum() == 0
    other.load_learned(saved)
    assert np.array_equal(other.dest_map.count, tr.dest_map.count)
    assert other.dest_map.walks == tr.dest_map.walks
    # a recalibrated sensor keeps it
    d = two_rooms().to_dict()
    d["sensors"][0]["heading"] = 50
    assert not other.reconfigure(Config.from_dict(d))
    assert np.array_equal(other.dest_map.count, tr.dest_map.count)
    # a threshold neither: the outputs decide differently, the model goes on
    d = two_rooms().to_dict()
    d["sensors"][0]["heading"] = 50
    d["params"]["target_thresholds"] = {"b": 0.6}
    assert not other.reconfigure(Config.from_dict(d))
    assert np.array_equal(other.dest_map.count, tr.dest_map.count) and other.threshold("b") == 0.6
    # another door starts it over
    d = two_rooms().to_dict()
    d["doors"][0]["y"] = 1.5
    other.reconfigure(Config.from_dict(d))
    assert other.dest_map.count.sum() == 0
    # a map of another floor plan is not used
    third = Tracker(Config.from_dict(d), start=0.0, people=["observed"])
    assert not third.use_dest_map(DestinationMap.from_dict(saved["dest_map"]))
    assert third.dest_map.count.sum() == 0


def test_occupancy_is_unchanged_by_the_map():
    a = routine(3)
    end = a.waypoints[-1][0] + 1
    trained, _ = run(a, end)
    _, plain = run(a, end, learn=False)
    _, learning = run(a, end, learn=True)
    t = Tracker(two_rooms(), start=0.0, people=["observed"])
    t.use_dest_map(trained.dest_map)
    _, primed = run(a, end, tracker=t)
    for (t0, s0, c0, l0), (t1, s1, c1, l1), (t2, s2, c2, l2) in zip(plain, learning, primed):
        assert t0 == t1 == t2
        assert c0 == c1 == c2
        assert l0 == l1 == l2
        assert s0.approaching == s1.approaching == s2.approaching and s0.p_enter == s1.p_enter == s2.p_enter
        assert s0.occupied == s1.occupied == s2.occupied and s0.probability == s1.probability == s2.probability
