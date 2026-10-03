"""Where an unseen person is: stay durations and the hypothesis bookkeeping."""

import math

import pytest

from presence_tracker.model import TrackerParams
from presence_tracker.unobserved import Dwell
from presence_tracker.whereabouts import DEAD, ROOM, Whereabouts


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


def test_a_walker_lost_at_an_unwatched_door_went_through_it():
    p = params()
    w = Whereabouts(p, existence=0.99)
    # walking straight at the kitchen door (prior 0.9), nobody would have seen it (unseen 1.0)
    w.lost()
    w.go(0.0, 0.95, [("kueche", 0.9, 1.0)])
    assert w.region("kueche") > 0.8 and w.t_in["kueche"] == 0.0
    # the same walk past a sensor that sees the way well: mostly refuted, they stayed
    w = Whereabouts(p, existence=0.99)
    w.lost()
    w.go(0.0, 0.95, [("kueche", 0.9, 0.05)])
    assert w.region("kueche") < 0.3 and w.room() > w.region("kueche")


def test_someone_sitting_does_not_drift_to_the_doors():
    p = params()
    w = Whereabouts(p, existence=0.99)
    w.lost()
    t = 0.0
    while t < 600:  # ten minutes, doors well in view of a sensor
        rate = p.getup_share / (t + p.getup_time)
        w.go(t, 1 - math.exp(-rate * 0.2), [("kueche", 1 / 3, 0.1), ("balkon", 1 / 3, 0.1)])
        t += 0.2
    assert w.room() > 0.85
    # without any evidence the room keeps the person; evidence against the room (nothing
    # re-detected, the LD2410C quiet) moves them to where they could have gone: the doors
    # and "never a person" share in proportion to their mass
    for _ in range(40):
        w.update(ROOM, 0.7, 1.0)
    assert w.room() < 0.1
    assert w.region("kueche") > 0 and w.dead() > 0


def test_a_visit_that_ends_unseen_brings_the_person_back_not_away():
    p = params()
    d = Dwell(p)
    w = Whereabouts(p, existence=0.99)
    w.w[ROOM], w.w["kueche"], w.w[DEAD], w.t_in["kueche"] = 0.0, 0.98, 0.02, 0.0
    t = 1.0
    while t < 1200:  # 20 minutes without a seen return
        w.end_visits(t, 1.0, d, {"kueche": True})
        t += 1.0
    # nobody came out seen: the kitchen fades, partly back to the room (missed return),
    # partly to "never a person" by renormalization; nothing is ever "gone"
    assert w.region("kueche") < 0.9 and w.room() > 0.0
    assert set(w.w) == {ROOM, DEAD, "kueche"}
    assert sum(w.w.values()) == pytest.approx(1.0)


def test_unwatched_door_fades_faster():
    p = params()
    d = Dwell(p)
    watched, blind = Whereabouts(p), Whereabouts(p)
    for w in (watched, blind):
        w.w[ROOM], w.w["kueche"], w.w[DEAD], w.t_in["kueche"] = 0.0, 0.9, 0.1, 0.0
    for t in range(1, 301):
        watched.end_visits(float(t), 1.0, d, {"kueche": True})
        blind.end_visits(float(t), 1.0, d, {"kueche": False})
    assert blind.region("kueche") < watched.region("kueche")


def test_returns_end_visits_and_duplicates_die():
    p = params()
    d = Dwell(p)
    w = Whereabouts(p)
    w.w[ROOM], w.w["kueche"], w.t_in["kueche"] = 0.1, 0.9, 0.0
    w.normalize()
    assert "kueche" in w.visited
    # measured again at their old place mid-room: the "visit" ends, reported but not learned
    w.w[ROOM], w.w["kueche"] = 0.9, 0.1
    assert w.arrive(60.0, d, lambda region: False) == [("kueche", 60.0)]
    assert not d.dwell
    # a confirmed return at the kitchen door is a real visit and is learned
    w.w[ROOM], w.w["kueche"], w.t_in["kueche"] = 0.1, 0.9, 100.0
    w.normalize()
    assert w.returned("kueche", 160.0, d) == 60.0
    assert d.dwell["kueche"] == [60.0] and w.room() == pytest.approx(1.0)
    # someone else's track came out of the kitchen: what of this one was in there was them
    w.w[ROOM], w.w["kueche"], w.w[DEAD] = 0.0, 0.95, 0.05
    w.duplicate("kueche", 0.95)
    assert w.dead() == pytest.approx(1.0)
