"""MODEL.md 6 "Belegt": a room is occupied by the filter's probability or while its own LD2450 measures
somebody in it (roomseen.py); only an output."""

import asyncio
import json

import pytest

from presence_tracker import ha
from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sensortracks import LOST


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


# ------------------------------------------------------------------ a silent sensor: unavailable

def feed_both(tracker, t0, t1, wohn=True, ess=True, k0=0):
    """Frames of the sensors at 10 Hz from t0 to t1 (somebody in front of each), the model stepped."""
    k, t = k0, t0
    while t < t1 - 1e-9:
        if wohn:
            tracker.process_frame("presence-wohn", t, frame(k, 2500))
        if ess:
            tracker.process_frame("presence-ess", t, frame(k, 1500))
        tracker.step(t)
        k += 1
        t = round(t + 0.1, 6)
    return k


def available(tracker, t):
    tracker.step(t)
    return {z: st.available for z, st in tracker.zone_states().items()}


def test_a_room_whose_own_sensor_went_silent_is_unavailable():
    """9.10. 15:04 the boards of Wohnzimmer and Esszimmer went offline: the filter takes a silent
    sensor's view as unobserved (MODEL.md 4.4, 5.5), the person on the sofa expired, the room was
    "empty" for 1.5 h and the light went off. With no data the room is unavailable now, not empty."""
    config = open_plan()
    tr = Tracker(config, start=0.0)
    assert tr.seen.own == {"presence-wohn": "wohn", "presence-ess": "ess"}
    assert tr.seen.watched() == {"wohn", "ess"}
    k = feed_both(tr, 0.0, 10.0)
    assert all(available(tr, 10.0).values())
    # wohn's board goes offline after its frame at 9.9 s; ess goes on
    k = feed_both(tr, 10.0, 9.9 + LOST - 0.05, wohn=False, k0=k)
    assert available(tr, 9.9 + LOST - 0.05)["wohn"]  # a gap up to LOST is no silence
    k = feed_both(tr, 9.9 + LOST - 0.05, 30.0, wohn=False, k0=k)
    a = available(tr, 30.0)
    assert not a["wohn"] and a["ess"] and a["_total"]
    assert tr.seen.silent(30.0, 0.0) == {"wohn"}
    # the state itself is computed as always (only Home Assistant is told "unavailable")
    st = tr.zone_states()["wohn"]
    assert "available" not in st.to_dict() and st.probability is not None
    # back: available with its first frame
    feed_both(tr, 30.0, 30.5, k0=k)
    assert all(available(tr, 30.5).values())


def test_at_the_start_a_sensor_counts_as_silent_only_after_the_limit():
    """Before the first frames nothing is silent (the app's start); a sensor that sent nothing since the
    model started counts from that start: no room flaps to unavailable while the boards come in."""
    tr = Tracker(open_plan())  # as in the app: started by the first frame
    assert tr.start is None and all(st.available for st in tr.zone_states().values())
    assert tr.seen.silent(1e9, None) == set()
    t0 = 1000.0
    k = feed_both(tr, t0, t0 + 3.0, wohn=False)  # only ess sends at first
    assert tr.start == t0
    assert available(tr, t0 + LOST - 0.1)["wohn"]
    feed_both(tr, t0 + 3.0, t0 + LOST + 1.0, wohn=False, k0=k)
    assert not available(tr, t0 + LOST + 1.0)["wohn"]


def test_a_room_without_an_own_sensor_in_use_stays_as_it_is():
    d = open_plan().to_dict()
    d["sensors"][0]["enabled"] = False  # wohn's sensor switched off: wohn has no own sensor in use
    tr = Tracker(Config.from_dict(d), start=0.0)
    assert tr.seen.watched() == {"ess"}
    feed_both(tr, 0.0, 1.0, wohn=False, ess=False)
    a = available(tr, 100.0)  # nothing from anybody for 100 s
    assert a["wohn"] and not a["ess"]
    assert tr.seen.silent(100.0, 0.0) == {"ess"}
    # not placed: the same
    d["sensors"][0]["enabled"], d["sensors"][0]["placed"] = True, False
    tr.reconfigure(Config.from_dict(d))
    assert tr.seen.watched() == {"ess"}


def test_a_room_with_two_own_sensors_is_silent_only_when_both_are():
    d = open_plan().to_dict()
    d["sensors"][1]["name"] = "wohn"  # both are wohn's own
    tr = Tracker(Config.from_dict(d), start=0.0)
    assert set(tr.seen.own.values()) == {"wohn"}
    k = feed_both(tr, 0.0, 20.0, wohn=False)
    assert available(tr, 20.0)["wohn"]
    feed_both(tr, 20.0, 20.1, wohn=False, ess=False, k0=k)
    assert not available(tr, 40.0)["wohn"]


def test_home_assistant_gets_a_silent_room_unavailable_through_its_own_topic():
    d = open_plan().to_dict()
    d["zones"].append({"id": "sofa", "name": "Sofa", "kind": "area", "shape": "rect", "points": [[1, 1], [3, 2]]})
    config = Config.from_dict(d)
    tr = Tracker(config, start=0.0)
    sent = []

    async def publish(topic, payload, retain):
        sent.append((topic, payload, retain))
    disc = ha.Discovery(publish)
    asyncio.run(disc.sync(config.zones, {"wohn"}))
    configs = {t: json.loads(p) for t, p, _ in sent}

    def cfg(comp, zone, suffix):
        return configs[f"homeassistant/{comp}/presence_tracker/presence_tracker_{zone}_{suffix}/config"]
    topic = ha.zone_availability("wohn")
    assert topic == "presence-tracker/zone/wohn/availability"
    entities = (("binary_sensor", "occupancy"), ("sensor", "count"), ("binary_sensor", "moving"),
                ("binary_sensor", "approaching"), ("binary_sensor", "ziel"))
    for comp, suffix in entities:
        c = cfg(comp, "wohn", suffix)
        assert c["availability"] == [{"topic": ha.AVAILABILITY}, {"topic": topic}]
        assert c["availability_mode"] == "all" and "availability_topic" not in c
        # a room without its own availability (here ess), an area and the house: as before
        for zone in ("ess", "sofa", "total"):
            if suffix == "ziel" and zone != "ess":
                continue
            c = cfg(comp, zone, suffix)
            assert c["availability_topic"] == ha.AVAILABILITY and "availability" not in c

    def run(t):
        sent.clear()
        tr.step(t)
        asyncio.run(disc.states(tr.zone_states(), t=t))
        return list(sent)

    k = feed_both(tr, 0.0, 5.0)
    out = run(5.0)
    assert (topic, "online", True) in out and not any("ess/availability" in tp for tp, _, _ in out)
    assert all(tp != topic for tp, _, _ in run(5.1))  # only on a change
    k = feed_both(tr, 5.0, 20.0, wohn=False, k0=k)
    assert [(tp, p, r) for tp, p, r in run(20.0) if tp == topic] == [(topic, "offline", True)]
    assert all(tp != topic for tp, _, _ in run(20.1))
    # back: the fresh state first, then "online"
    feed_both(tr, 20.1, 20.5, k0=k)
    out = run(20.5)
    topics = [tp for tp, _, _ in out]
    assert (topic, "online", True) in out
    assert "presence-tracker/zone/wohn/state" not in topics or \
        topics.index("presence-tracker/zone/wohn/state") < topics.index(topic)
    # a new connection: it goes out again
    disc.reconnected()
    assert (topic, "online", True) in run(20.6)


def test_a_rooms_availability_topic_is_cleared_when_it_has_none_any_more():
    config = open_plan()
    tr = Tracker(config, start=0.0)
    sent = []

    async def publish(topic, payload, retain):
        sent.append((topic, payload, retain))
    disc = ha.Discovery(publish)
    asyncio.run(disc.sync(config.zones, {"wohn", "ess"}))
    feed_both(tr, 0.0, 1.0)
    asyncio.run(disc.states(tr.zone_states(), t=1.0))
    assert ("presence-tracker/zone/ess/availability", "online", True) in sent
    # while running: ess's sensor switched off (or ess deleted) -> its availability topic goes
    sent.clear()
    asyncio.run(disc.sync(config.zones, {"wohn"}))
    asyncio.run(disc.states(tr.zone_states(), t=1.0))
    assert ("presence-tracker/zone/ess/availability", "", True) in sent
    assert not any(t.endswith("/wohn/availability") and p == "" for t, p, _ in sent)
    # from a run before: the broker still holds the config of a deleted room with its availability, and
    # one of ess (still wanted) from when it had its own availability
    sent.clear()
    gone = "homeassistant/binary_sensor/presence_tracker/presence_tracker_kueche_occupancy/config"
    old = {"state_topic": "presence-tracker/zone/kueche/state",
           "availability": [{"topic": ha.AVAILABILITY}, {"topic": "presence-tracker/zone/kueche/availability"}]}
    assert disc.retained(gone, json.dumps(old).encode())
    ess = next(t for t in disc.wanted if "ess_occupancy" in t)
    old_ess = {"state_topic": "presence-tracker/zone/ess/state",
               "availability": [{"topic": ha.AVAILABILITY}, {"topic": "presence-tracker/zone/ess/availability"}]}
    assert disc.retained(ess, json.dumps(old_ess).encode())
    wohn = next(t for t in disc.wanted if "wohn_occupancy" in t)
    assert disc.retained(wohn, disc.wanted[wohn].encode())  # our own, as wanted: nothing to clear
    asyncio.run(disc.states(tr.zone_states(), t=1.0))
    cleared = {t for t, p, r in sent if p == "" and r}
    assert cleared == {gone, "presence-tracker/zone/kueche/state", "presence-tracker/zone/kueche/availability",
                       "presence-tracker/zone/ess/availability"}
