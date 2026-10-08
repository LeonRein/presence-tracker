"""Lernen pausieren (pause.py, MODEL.md 10 "Hintergrundaktivität"): while the switch is on, nothing is
learned - ghost map, LD2410C background and echo rate, destination map, calibration data - and the
tracking goes on as with learning off; after it, learning goes on. The switch survives a restart, and
an error report replays with the same pauses."""

import asyncio
import collections
import gzip
import json

from presence_tracker import ha, pause
from presence_tracker.app import App
from presence_tracker.filter import Tracker
from presence_tracker.frames import SensorClock
from presence_tracker.model import Config
from presence_tracker.pause import Pauses
from presence_tracker.sim import Person, simulate

from test_destination import routine
from test_entering import two_rooms
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def test_the_intervals():
    p = Pauses()
    assert not p.on and not p.paused(5.0)
    assert p.set(True, 10.0) and not p.set(True, 11.0) and p.on and p.since == 10.0
    assert p.paused(10.0) and p.paused(1e9) and not p.paused(9.9) and p.paused(5.0, 10.0)
    assert p.set(False, 20.0) and not p.on
    assert p.paused(19.9) and not p.paused(20.0) and p.paused(0.0, 30.0) and not p.paused(20.0, 30.0)
    p.set(True, 40.0)
    p.set(False, 50.0)
    assert p.between(25.0, 35.0) == [] and p.between(15.0) == [[10.0, 20.0], [40.0, 50.0]]
    p.prune(30.0)
    assert p.intervals == [[40.0, 50.0]]
    assert Pauses.from_dict(json.loads(json.dumps(p.to_dict()))).intervals == p.intervals
    # known beforehand (the tools: the vacuum's history), kept apart from the switch's
    p.add(55.0, 60.0)
    p.add(0.0, 5.0)
    p.add(4.0, 6.0)
    assert p.known == [[0.0, 6.0], [55.0, 60.0]] and p.intervals == [[40.0, 50.0]]
    assert p.paused(57.0) and p.paused(5.5) and not p.paused(52.0) and not p.on
    p.set(True, 70.0)  # the switch after them
    assert p.on and p.paused(80.0) and p.between(0.0, 60.0) == [[0.0, 6.0], [40.0, 50.0], [55.0, 60.0]]
    assert [pause.parse(x) for x in (b"ON", "off", True, "1", b"maybe")] == [True, False, True, True, None]


def _run(tr, person, until, config):
    out, nxt = [], 0.0
    for t, sid, frame in simulate([person], sim_sensors(config), until, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
        if t >= nxt:
            tr.step(t)
            out.append(tr.count_distribution())
            nxt = t + 0.2
    return out


def _learned(tr) -> tuple:
    return (sum(tr.ghost_map.time.values()), sum(tr.ld_background.den.values()), tr.dest_map.walks)


def test_nothing_is_learned_while_paused_and_the_tracking_is_that_without_learning():
    config = two_rooms()
    a = routine(3)
    until = a.waypoints[-1][0] + 1
    free = Tracker(config, start=0.0, people=["observed"])
    out_free = _run(free, a, until, config)
    assert all(x > 0 for x in _learned(free))  # the run teaches all three
    off = Tracker(config, start=0.0, people=["observed"])
    off.learn_ghosts = off.learn_dest = False
    out_off = _run(off, a, until, config)
    paused = Tracker(config, start=0.0, people=["observed"])
    paused.pauses = Pauses([[0.0, None]])
    births = []
    paused.listeners.append(lambda kind, d: births.append(kind) if kind in ("watch", "track_end") else None)
    out_paused = _run(paused, a, until, config)
    assert _learned(paused) == (0.0, 0.0, 0.0) and not births
    assert paused.ghost_map.to_dict() == off.ghost_map.to_dict()
    # the tracking: as with learning off, exactly (what was learned weighs in, MODEL.md 4.2)
    assert all(x == y for p, q in zip(out_paused, out_off) for x, y in zip(p.values(), q.values()))
    assert len(out_paused) == len(out_free)


def test_learning_goes_on_after_the_pause():
    config = two_rooms()
    a = routine(6)
    until = a.waypoints[-1][0] + 1
    half = until / 2
    free = Tracker(config, start=0.0, people=["observed"])
    _run(free, a, until, config)
    later = Tracker(config, start=0.0, people=["observed"])
    later.pauses = Pauses([[0.0, half]])
    _run(later, a, until, config)
    watched, background, walks = _learned(later)
    w0, b0, k0 = _learned(free)
    # the sensors watched only after the pause; walks counted only after it
    assert 0.3 * w0 < watched < 0.7 * w0 and 0.3 * b0 < background < 0.7 * b0
    assert 0 < walks < k0


def _frames(config, until=60.0):
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (4.5, 1.0), start=5, pauses={2: 20, 3: 60}))
    return list(simulate([a], sim_sensors(config, idle=0.0), until, walls=config.wall_segments))


def test_the_switch_pauses_the_app_and_survives_a_restart(tmp_path):
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    app.on_message(pause.COMMAND, b"ON", 1000.0)
    assert app.pauses.on and app.tracker.pauses is app.pauses
    assert json.loads((tmp_path / "pause.json").read_text())["intervals"] == [[1000.0, None]]
    for t, sid, frame in _frames(config, 30.0):
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        app.tick(1000.0 + t)
    assert _learned(app.tracker) == (0.0, 0.0, 0.0)
    assert not any(len(a) for a in app.calibrator.data().values())  # no calibration data
    app.calibrator.status_due = lambda: False  # (no count in a thread here)
    assert json.loads(app.live_message())["learning_paused"] == {"on": True, "since": 1000.0}
    # restarted while paused: it stays paused (until the switch, retained at the broker, says otherwise)
    again = App(tmp_path, publish=False)
    assert again.pauses.on and again.tracker.pauses is again.pauses
    again.on_message(pause.COMMAND, b"OFF", 1100.0)
    assert not again.pauses.on and not App(tmp_path, publish=False).pauses.on
    # off: the calibration data are collected again
    for t, sid, frame in _frames(config, 30.0):
        again.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1200.0 + t)
        again.tick(1200.0 + t)
    assert any(len(a) for a in again.calibrator.data().values()) and _learned(again.tracker)[0] > 0


def test_the_switch_in_home_assistant():
    sent = []

    async def publish(topic, payload, retain=False):
        sent.append((topic, payload, retain))
    asyncio.run(ha.Discovery(publish).sync(flat_config().zones))
    cfg = [json.loads(p) for t, p, _ in sent if t.startswith("homeassistant/switch/")]
    assert len(cfg) == 1 and cfg[0]["name"] == "Lernen pausieren"
    assert cfg[0]["command_topic"] == pause.COMMAND and cfg[0]["state_topic"] == pause.STATE
    assert cfg[0]["retain"] is True and "availability_topic" not in cfg[0]  # usable while the app is down
    assert cfg[0]["default_entity_id"] == "switch.presence_lernen_pausieren"
    # and a recording of it (tools/record.py) is followed by the offline tools
    p = Pauses()
    assert pause.follow(p, {"t": 5.0, "topic": pause.COMMAND, "payload": "ON"}) and p.on
    assert pause.follow(p, {"t": 9.0, "topic": pause.STATE, "payload": "OFF"}) and p.intervals == [[5.0, 9.0]]
    assert not pause.follow(p, {"t": 9.0, "topic": "presence/a/frame", "payload": {}})


def test_a_report_replays_with_the_apps_pauses(tmp_path):
    """The report holds the pauses; replayed with them, what was learned is what the app learned (bit for
    bit), and without them it is not."""
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    for t, sid, frame in _frames(config, 90.0):
        recv = 1000.0 + t
        if 20.0 <= t and not app.pauses.on and t < 50.0:
            app.on_message(pause.COMMAND, b"ON", recv)
        if t >= 50.0 and app.pauses.on:
            app.on_message(pause.COMMAND, b"OFF", recv)
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), recv)
        app.tick(recv)

    class Request:
        async def json(self):
            return {"kind": "ghost", "room": "wohn"}
    asyncio.run(app.h_report(Request()))
    path, = (tmp_path / "reports").glob("*.jsonl.gz")
    lines = gzip.open(path, "rt").read().splitlines()
    meta, messages = json.loads(lines[0]), [json.loads(x) for x in lines[1:]]
    (s, e), = meta["pauses"]
    assert 1019.9 < s < 1020.2 and 1049.9 < e < 1050.2

    def replay(pauses):
        tr = Tracker(Config.from_dict(meta["config"]))
        tr.load_learned(meta)
        tr.pauses = pauses
        clocks = collections.defaultdict(SensorClock)
        for m in messages:
            if m["topic"].endswith("/frame") and m["t"] >= meta["report"]["model_start"]:
                sid = m["topic"].split("/")[1]
                tr.process_frame(sid, clocks[sid](m["t"], m["payload"].get("uptime_ms")), m["payload"])
                tr.step(m["t"])
        return json.loads(json.dumps(tr.learned()))
    app_learned = json.loads(json.dumps(app.tracker.learned()))
    assert replay(Pauses(meta["pauses"])) == app_learned
    assert replay(Pauses())["ld_background"] != app_learned["ld_background"]
