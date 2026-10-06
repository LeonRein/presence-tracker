"""The model must not depend on how time is cut into frames (MODEL.md 4.4): a heartbeat gap of 5 s
weighs like the 56 empty frames it stands for."""

import numpy as np

from presence_tracker.filter import Tracker

from test_filter import flat_config


def place_mass(gaps):
    """One person, somewhere in the room or on the balcony, nothing seen; frames of both sensors
    after each gap."""
    config = flat_config(people=1)
    tr = Tracker(config, start=0.0, seed=3, people=["anywhere"])
    t = 0.0
    empty = {"targets": []}
    for g in gaps:
        t += g
        for sid in ("a", "b"):
            tr.process_frame(sid, t, empty)
    return 1 - tr.place_distribution()["observed"][0]


def test_a_heartbeat_gap_weighs_like_the_empty_frames_in_it():
    one = place_mass([0.089] + [5.0] * 6)
    many = place_mass([0.089] + [5.0 / 56] * 56 * 6)
    assert abs(one - many) < 0.03, (one, many)
