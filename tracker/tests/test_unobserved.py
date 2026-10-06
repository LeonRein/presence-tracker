"""How long people stay in rooms without a sensor (MODEL.md 3.3)."""

import pytest

from presence_tracker.model import TrackerParams
from presence_tracker.unobserved import Dwell


def test_stays_follow_the_assumed_log_normal():
    d = Dwell(TrackerParams(), open_regions=["flur"])
    assert d.stats("kueche")["median"] == 120
    assert d.survival("kueche", 120) == pytest.approx(0.5)
    assert d.survival("kueche", 1.0) > 0.99
    # in an open region (the way out, the bedroom) hours are normal
    assert d.stats("flur")["median"] == 1800
    assert d.survival("flur", 3600) > d.survival("kueche", 3600)
    assert d.stats("kueche")["p90"] == pytest.approx(120 * 6.83, rel=0.01)  # exp(1.5 * 1.2816)
