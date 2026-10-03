"""Probability that someone is still in a closed room without a sensor."""

import pytest

from presence_tracker.model import TrackerParams
from presence_tracker.unobserved import Occupancy


def occupancy(**params):
    return Occupancy(TrackerParams(**params))


def test_fades_with_time():
    o = occupancy()
    o.went_in("kueche", 0.0, 0.9)
    p = [o.best("kueche", t) for t in (1, 60, 300, 1800, 3 * 3600)]
    assert p[0] > 0.85 and p[0] > p[1] > p[2] > p[3] > p[4]
    assert p[3] < 0.2 and p[4] < 0.05


def test_door_not_watched_fades_faster():
    # the sensor that sees the kitchen door went offline right after someone went in
    watched, blind = occupancy(), occupancy()
    for o in (watched, blind):
        o.went_in("kueche", 0.0, 0.9)
    for t in range(1, 601):
        watched.step(float(t), 1.0, {"kueche": True})
        blind.step(float(t), 1.0, {"kueche": False})
    assert blind.best("kueche", 600) < watched.best("kueche", 600)


def test_todays_stuck_kitchen_is_forgotten():
    # 17:17:39 someone went in, at 17:18:39 the only sensor watching the door went offline
    o = occupancy()
    o.dwell["kueche"] = [24, 20, 48, 8, 25, 21, 13, 8, 2, 14, 5, 4, 21, 114]  # today's visits
    o.went_in("kueche", 0.0, 0.9)
    for t in range(1, 1201):
        o.step(float(t), 1.0, {"kueche": t < 60})
    assert not o.visits["kueche"]  # forgotten within 20 minutes, not after 6 hours


def test_learns_the_typical_stay():
    o = occupancy()
    assert o.stats("kueche")["median"] == 120  # the prior
    for _ in range(30):
        o.went_in("kueche", 0.0, 0.9)
        o.came_back("kueche", 20.0)
    assert o.stats("kueche")["median"] == pytest.approx(20, rel=0.3)
    assert o.changed
    # with short stays learned, a visit fades much faster
    o.went_in("kueche", 0.0, 0.9)
    assert o.best("kueche", 300) < 0.2


def test_lost_at_the_door_is_undone():
    o = occupancy()
    o.went_in("kueche", 0.0, 0.6)
    o.undo_last("kueche")
    assert o.best("kueche", 1.0) == 0.0 and not o.dwell


def test_long_stays_are_learned_too():
    # mostly fetching something, sometimes cooking: after a few minutes, the cook is still likely in
    o = occupancy()
    o.dwell["kueche"] = [20] * 20 + [1500] * 5
    o.went_in("kueche", 0.0, 0.9)
    assert o.best("kueche", 600) > 0.5
    # a single fitted curve over these visits would put this far below
    assert o.stats("kueche")["median"] < 60
