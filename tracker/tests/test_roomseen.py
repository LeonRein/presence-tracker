"""MODEL.md 6 "Belegt": a room is occupied by the filter's probability or while its own LD2450 measures
somebody in it (roomseen.py); only an output."""

import asyncio
import json

import pytest

from presence_tracker import ha
from presence_tracker.filter import Tracker
from presence_tracker.model import Config


def open_plan() -> Config:
    """Two rooms without a wall between them (open plan): wohn 6 x 5 m with its sensor in the bottom
    left corner, ess 4 x 5 m right of it with its sensor on the right wall looking into wohn."""
    def rect(zid, x0, y0, x1, y1):
        return {"id": zid, "name": zid.capitalize(), "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2]}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [
            {"id": "presence-wohn", "name": "wohn", "x": 0.05, "y": 0.05, "heading": 45, "placed": True},
            {"id": "presence-ess", "name": "ess", "x": 9.95, "y": 2.5, "heading": 180, "placed": True},
        ],
        "zones": [rect("wohn", 0, 0, 6, 5), rect("ess", 6, 0, 10, 5)],
        "walls": [w((0, 0), (10, 0)), w((10, 0), (10, 5)), w((10, 5), (0, 5)), w((0, 5), (0, 0)),
                  {"points": [[6, 0], [6, 5]], "kind": "divider"}],
    })


def frame(k, y_mm, frozen=False):
    """One LD2450 frame with one target straight ahead: still, its position jittering by a few mm as
    measured, or bit-identical (frozen: the sensor holds a lost target)."""
    jitter = 0 if frozen else (7 if k % 2 else -7)
    return {"uptime_ms": 100 * k, "targets": [{"x": jitter, "y": y_mm + jitter, "speed": 0, "slot": 0}]}


def feed(tracker, sid, t0, t1, y_mm, frozen_from=None, k0=0):
    """Frames of one sensor at 10 Hz from t0 to t1; from frozen_from on the target is frozen."""
    k, t = k0, t0
    while t < t1 - 1e-9:
        tracker.process_frame(sid, t, frame(k, y_mm, frozen_from is not None and t >= frozen_from))
        tracker.step(t)
        k += 1
        t = round(t + 0.1, 6)
    return k


def dark_filter(config):
    """The filter alone never says occupied (its threshold near 1): what is occupied, the rule made."""
    config.params.light_cost = 1000.0
    return config


def test_a_still_person_the_own_ld2450_measures_keeps_the_room_occupied():
    config = dark_filter(open_plan())
    tr = Tracker(config, start=0.0)
    feed(tr, "presence-wohn", 0.0, 20.0, 2500)  # somebody sits 2.5 m in front of the sensor of wohn
    st = tr.zone_states()
    assert st["wohn"].probability < 0.999  # the filter alone: empty
    assert st["wohn"].occupied and st["wohn"].source == "ld2450"
    assert st["wohn"].count >= 1 and st["_total"].count >= 1
    assert not st["ess"].occupied and st["ess"].source is None
    assert tr.occupancy()["wohn"] == (True, "ld2450")
    # the sensor stops measuring: occupied for seen_hold s more, then the filter alone
    tr.step(20.0 + config.params.seen_hold - 0.5)
    assert tr.zone_states()["wohn"].occupied
    tr.step(20.0 + config.params.seen_hold + 0.5)
    st = tr.zone_states()["wohn"]
    assert not st.occupied and st.source is None


def test_a_held_frozen_target_after_leaving_does_not_keep_it():
    config = dark_filter(open_plan())
    tr = Tracker(config, start=0.0)
    # measured for 5 s, then the LD2450 holds the target bit-identical for 30 s (as after somebody left)
    feed(tr, "presence-wohn", 0.0, 35.0, 2500, frozen_from=5.0)
    assert any(d.stale for d in tr.runtime["presence-wohn"].detections)  # held: no measurement
    st = tr.zone_states()["wohn"]
    assert not st.occupied and st.source is None


def test_another_rooms_sensor_seeing_into_the_room_does_not_count():
    config = dark_filter(open_plan())
    tr = Tracker(config, start=0.0)
    # the sensor of ess measures somebody 4.5 m ahead: in wohn (no wall between the rooms)
    feed(tr, "presence-ess", 0.0, 20.0, 4500)
    det = tr.runtime["presence-ess"].detections[0]
    assert next(z for z in config.zones if z.id == "wohn").contains(*det.pos) and not det.hidden and not det.stale
    st = tr.zone_states()
    assert not st["wohn"].occupied and not st["ess"].occupied
    assert tr.seen.last == {}


def test_quelle_says_what_decided():
    config = open_plan()
    tr = Tracker(config, start=0.0)
    feed(tr, "presence-wohn", 0.0, 20.0, 2500)
    for cost, filt in ((1000.0, False), (1e-3, True)):
        config.params.light_cost = cost
        st = tr.zone_states()["wohn"]
        assert st.occupied and st.source == ("beide" if filt else "ld2450")
        assert st.to_dict()["source"] == st.source
    # the filter alone (the rule off with seen_hold 0)
    config.params.seen_hold = 0.0
    st = tr.zone_states()["wohn"]
    assert st.occupied and st.source == "filter"
    config.params.light_cost = 1000.0
    st = tr.zone_states()["wohn"]
    assert not st.occupied and st.source is None and st.to_dict()["source"] is None


def test_home_assistant_gets_the_flip_at_once_and_quelle_as_attribute():
    config = dark_filter(open_plan())
    tr = Tracker(config, start=0.0)
    sent = []

    async def publish(topic, payload, retain):
        sent.append((topic, payload))
    d = ha.Discovery(publish)
    asyncio.run(d.sync(config.zones))
    occ = next(p for t, p in sent if t.endswith("presence_tracker_wohn_occupancy/config"))
    assert "quelle" in occ and "value_json.source" in occ
    feed(tr, "presence-wohn", 0.0, 20.0, 2500)
    states = []
    t = 20.0
    while t < 20.0 + config.params.seen_hold + 1.0:  # the app's housekeeping: every 0.1 s
        tr.step(t)
        sent.clear()
        asyncio.run(d.states(tr.zone_states(), t=t))
        states += [(t, p) for topic, p in sent if topic.endswith("/wohn/state")]
        t = round(t + 0.1, 6)
    first, last = json.loads(states[0][1]), json.loads(states[-1][1])
    assert first["occupied"] and first["source"] == "ld2450"
    assert not last["occupied"] and last["source"] is None
    # off within one tick of seen_hold after the last measured frame (19.9 s)
    assert 19.9 + config.params.seen_hold < states[-1][0] <= 19.9 + config.params.seen_hold + 0.11


@pytest.mark.parametrize("value, ok", [(-1.0, False), (0.0, True), (10.0, True), (120.0, True), (121.0, False)])
def test_seen_hold_has_limits(value, ok):
    d = open_plan().to_dict()
    d["params"]["seen_hold"] = value
    if ok:
        assert Config.from_dict(d, check=True).params.seen_hold == value
    else:
        with pytest.raises(ValueError, match="Haltezeit eigener LD2450"):
            Config.from_dict(d, check=True)


def test_changing_seen_hold_restarts_nothing():
    config = open_plan()
    tr = Tracker(config, start=0.0)
    feed(tr, "presence-wohn", 0.0, 5.0, 2500)
    d = config.to_dict()
    d["params"]["seen_hold"] = 3.0
    assert tr.reconfigure(Config.from_dict(d)) is False  # an output only: the people stay
    assert tr.p.seen_hold == 3.0 and tr.seen.last == {"wohn": 4.9}
