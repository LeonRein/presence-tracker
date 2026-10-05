"""Where an unseen person is: stay durations and the hypothesis bookkeeping."""


import pytest

from presence_tracker.model import TrackerParams
from presence_tracker.unobserved import Dwell


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
