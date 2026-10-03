"""Where an unseen person is: stay durations and the hypothesis bookkeeping."""

import pytest

from presence_tracker.model import TrackerParams
from presence_tracker.unobserved import Dwell
from presence_tracker.whereabouts import GONE, HERE, NEAR, Whereabouts


def params(**kw):
    return TrackerParams(**kw)


def test_hazard_follows_learned_stays():
    d = Dwell(params())
    assert d.stats("kueche")["median"] == 120  # the prior
    for _ in range(30):
        d.learn("kueche", 20.0)
    assert d.stats("kueche")["median"] == pytest.approx(20, rel=0.3)
    assert d.changed
    # a visit of 25 s is very likely over, one of 5 s hardly
    assert d.hazard("kueche", 25.0, 5.0) > d.hazard("kueche", 5.0, 5.0)


def test_long_stays_are_learned_too():
    # mostly fetching something, sometimes cooking: after a few minutes the cook is still likely in
    d = Dwell(params())
    d.dwell["kueche"] = [20] * 20 + [1500] * 5
    assert d.survival("kueche", 600) > 0.15
    assert d.stats("kueche")["median"] < 60


def test_mass_flows_to_a_door_and_fades_when_the_visit_ends():
    p = params()
    w = Whereabouts(p)
    d = Dwell(p)
    for _ in range(50):  # 10 s lost while walking, the kitchen door within reach
        w.leak(0.2, True, [("kueche", 1.0)], t_last_seen=0.0)
    assert w.region("kueche") > 0.6 and w.here() < 0.05  # the rest: moved unseen nearby
    assert w.t_in["kueche"] == 0.0
    # the sensors see the area around the last position and report nobody: not roaming nearby
    for _ in range(10):
        w.update(NEAR, 0.1, 1.0)
    assert w.w[NEAR] < 0.01
    # 20 minutes without a seen return, as the tracker steps it: the visit is over, the person
    # came out unseen (gone) or, with a small remainder, is still in there
    t = 10.0
    while t < 1200:
        w.leak(1.0, False, [], t_last_seen=0.0)
        w.end_visits(t, 1.0, d, {"kueche": True})
        t += 1.0
    assert w.w[GONE] > 0.5 and w.region("kueche") < 0.45  # with 10 % unseen exits, "still in there" stays plausible


def test_unwatched_door_fades_faster():
    p = params()
    d = Dwell(p)
    watched, blind = Whereabouts(p), Whereabouts(p)
    for w in (watched, blind):
        w.w[HERE], w.w["kueche"], w.t_in["kueche"] = 0.0, 1.0, 0.0
    for t in range(1, 301):
        watched.end_visits(float(t), 1.0, d, {"kueche": True})
        blind.end_visits(float(t), 1.0, d, {"kueche": False})
    assert blind.region("kueche") < watched.region("kueche")


def test_someone_sitting_keeps_their_place_unless_evidence_says_otherwise():
    p = params()
    w = Whereabouts(p)
    for _ in range(300):  # a minute lost while sitting
        w.leak(0.2, False, [("kueche", 1.0)], t_last_seen=0.0)
    assert w.here() > 0.7
    # the LD2410C says nobody is at that distance, again and again
    for _ in range(40):
        w.update(HERE, 0.7, 1.0)
    assert w.here() < 0.1
    # ... and the mass that went to the kitchen makes it the most probable whereabouts
    w.w[HERE], w.w["kueche"], w.t_in["kueche"] = 0.1, 0.9, 0.0
    w.normalize()
    assert "kueche" in w.visited
    # until the person is seen at their place again: the "visit" ends; it is reported but not
    # learned as a stay (we never saw them in the kitchen)
    d = Dwell(p)
    w.w[HERE], w.w["kueche"] = 0.9, 0.1
    assert w.arrive(60.0, d, lambda region: False) == [("kueche", 60.0)]
    assert not d.dwell
    # a confirmed return at the kitchen door, however, is a real visit and is learned
    w.w[HERE], w.w["kueche"], w.t_in["kueche"] = 0.1, 0.9, 100.0
    w.normalize()
    assert w.returned("kueche", 160.0, d) == 60.0
    assert d.dwell["kueche"] == [60.0] and w.here() == 1.0
