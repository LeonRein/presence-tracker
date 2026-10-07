"""The app goes on whatever comes in: a browser that stops reading, broken MQTT payloads, broken files,
failing saves. Node-RED keeps a light's last state while the app is gone or frozen, so anything that
stops it or freezes its outputs leaves a light that is on burning."""

import asyncio
import base64
import json
import math
import os
import socket
import threading
import time

import aiohttp
import numpy as np
import pytest
from aiohttp import web

from presence_tracker import app as app_module
from presence_tracker.app import App
from presence_tracker.filter import Tracker
from presence_tracker.ha import AVAILABILITY
from presence_tracker.zones import ZoneState
from presence_tracker.sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def test_a_browser_that_stops_reading_holds_up_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "SEND_TIMEOUT", 0.5)
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)

    async def main():
        runner = web.AppRunner(app.web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        # a client that does the handshake and then never reads (a phone gone to sleep, a stalled proxy)
        stuck = socket.socket()
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode()
        stuck.sendall(f"GET /api/live HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        received = []
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://127.0.0.1:{port}/api/live") as good:
                async def read():
                    async for msg in good:
                        received.append(msg.data)
                reader = asyncio.create_task(read())
                for _ in range(100):
                    if len(app.ws_clients) == 2:
                        break
                    await asyncio.sleep(0.02)
                assert len(app.ws_clients) == 2
                slowest = 0.0
                for i in range(300):  # 12 MB: far more than the socket buffers hold
                    t0 = time.monotonic()
                    app._broadcast(f"{i:04d}" + "x" * 40_000)
                    slowest = max(slowest, time.monotonic() - t0)
                    await asyncio.sleep(0.01)
                assert slowest < 0.05, slowest  # the housekeeping loop never waits for a browser
                for _ in range(200):
                    if len(app.ws_clients) == 1:
                        break
                    await asyncio.sleep(0.02)
                assert len(app.ws_clients) == 1  # the stuck one is cut off, the other one stays
                app._broadcast("last")
                for _ in range(100):
                    if received and received[-1] == "last":
                        break
                    await asyncio.sleep(0.02)
                assert received[-1] == "last"
                reader.cancel()
        stuck.close()
        await runner.cleanup()

    asyncio.run(main())


BAD_FRAMES = {  # valid JSON, but no frame (BUGS 3): each of these stopped the app until 0.18
    "null": b"null",
    "list": b"[]",
    "number": b"5",
    "uptime_ms a string": b'{"uptime_ms": "x", "targets": [], "ld2410": {}}',
    "a target without y": b'{"uptime_ms": 1100, "targets": [{"x": 100, "slot": 1}], "ld2410": {}}',
    "x null": b'{"uptime_ms": 1200, "targets": [{"x": null, "y": 1000, "slot": 1}], "ld2410": {}}',
    "targets null": b'{"uptime_ms": 1300, "targets": null, "ld2410": {}}',
    # caught before, but by a restart of the model (seconds of CPU each)
    "x NaN": b'{"uptime_ms": 1400, "targets": [{"x": NaN, "y": 1000, "slot": 1}]}',
    "ld2410 a list": b'{"uptime_ms": 1500, "targets": [], "ld2410": []}',
    "a gate a string": b'{"uptime_ms": 1600, "targets": [], "ld2410": {"move_gates": ["a"], "still_gates": []}}',
    "a target a list": b'{"uptime_ms": 1700, "targets": [[1]]}',
    "no JSON": b"{",
}


def test_no_payload_stops_the_app_or_restarts_the_model(tmp_path, caplog):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    tracker = app.tracker
    good = {"uptime_ms": 1000, "targets": [], "ld2410": {}}
    app.on_message("presence/a/frame", json.dumps(good).encode(), 1000.0)
    for name, payload in BAD_FRAMES.items():
        app.on_message("presence/a/frame", payload, 1001.0)  # must not raise
        assert app.tracker is tracker, name  # dropped, not a model failure
    assert app.tracker.runtime["a"].frames == 1
    assert sum("dropped" in r.message for r in caplog.records) == 1  # once a minute per sensor
    app.on_message("presence/a/frame", json.dumps({**good, "uptime_ms": 2000}).encode(), 1001.0)
    assert app.tracker.runtime["a"].frames == 2 and app.tracker is tracker
    assert [json.loads(p)["uptime_ms"] for _, topic, p in app.recent] == [1000, 2000]  # not in a report


def test_the_mqtt_loop_survives_whatever_a_message_does(monkeypatch):
    """Even if handling a message failed, the MQTT connection goes on."""
    import aiomqtt

    from presence_tracker import sources

    class Msg:
        def __init__(self, n):
            self.topic, self.payload = "presence/a/frame", str(n).encode()

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, topic):
            pass

        @property
        def messages(self):
            async def gen():
                for n in range(3):
                    yield Msg(n)
                raise ValueError("anything")
            return gen()

    monkeypatch.setattr(aiomqtt, "Client", Client)
    seen, clients = [], []

    def on_message(topic, payload, recv):
        seen.append(payload)
        raise RuntimeError("bad")

    async def on_client(c):
        clients.append(c)

    async def main():
        task = asyncio.create_task(sources.mqtt_source({"host": "x"}, "presence", on_message, on_client))
        await asyncio.sleep(0.1)
        assert not task.done()  # reconnecting
        task.cancel()

    asyncio.run(main())
    assert seen == [b"0", b"1", b"2"] and clients[-1] is None  # all messages, then reconnecting


def run_a_while(app, until=40.0):
    config = app.config
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), start=2, pauses={2: 60}))
    for t, sid, frame in simulate([a], sim_sensors(config), until, walls=config.wall_segments):
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        app.tick(1000.0 + t)


@pytest.mark.parametrize("name, content", [
    ("calibration.npz", b""), ("calibration.npz", b"PK\x03\x04garbage"), ("people.json", b"[1]"),
    ("people.json", b'"x"'), ("people.json", b""), ("people.json", b'{"tiles": [], "hyps": 5}'),
    ("ghostmap.json", b""), ("ghostmap.json", b"[]"), ("ghostmap.json", b'{"x0": "a"}'),
    ("ld2410.json", b"[]"), ("ld2410.json", b'{"num": {"a": 5}}'), ("destinations.json", b"[]"),
])
def test_a_broken_file_is_nothing_learned_for_its_part_only(tmp_path, caplog, name, content):
    """An empty file after a power cut, a broken or wrongly typed one: the app starts (until 0.18 it
    did not), that part with nothing learned, the others as saved."""
    caplog.set_level("INFO")
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    run_a_while(app)
    app._save_learned()
    saved = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.suffix in (".json", ".npz")}
    assert set(saved) >= {"ghostmap.json", "ld2410.json", "destinations.json", "people.json", "calibration.npz"}
    (tmp_path / name).write_bytes(content)
    again = App(tmp_path, publish=False)  # must not raise
    assert any(name in r.message for r in caplog.records if r.levelname in ("WARNING", "INFO"))
    assert (again.tracker.started_from is None) == (name == "people.json")
    if name != "calibration.npz":
        assert again.calibrator.data()  # the other parts as saved
    else:
        assert not again.calibrator.data()
    if name == "ghostmap.json":
        assert not again.tracker.ghost_map.count
    if name == "ld2410.json":
        assert not again.tracker.ld_background.num
    run_a_while(again, 20.0)
    assert again.tick(1000.0 + 20.0)


def test_a_failed_save_stops_nothing(tmp_path, monkeypatch, caplog):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    run_a_while(app, 20.0)

    def full(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(app_module, "atomic_write", full)
    app._save_learned()  # must not raise
    asyncio.run(app._save_in_background())
    assert sum("not saved" in r.message for r in caplog.records) == 10
    monkeypatch.undo()
    # the model refuses to save a broken state (a density that is no density): it starts over
    tracker = app.tracker

    def broken():
        raise ValueError("a density that is no density")

    monkeypatch.setattr(tracker, "people_state", broken)
    app._save_learned()  # must not raise
    assert app.tracker is not tracker and (tmp_path / "ghostmap.json").exists()


def test_saving_runs_in_a_thread_and_loads_again(tmp_path, monkeypatch):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    run_a_while(app)
    threads = []
    write = app_module.atomic_write

    def spy(path, data):
        threads.append(threading.current_thread() is threading.main_thread())
        write(path, data)

    monkeypatch.setattr(app_module, "atomic_write", spy)
    asyncio.run(app._save_in_background())
    assert threads == [False] * 5
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))
    again = App(tmp_path, publish=False)
    assert again.tracker.started_from is not None
    assert json.loads(json.dumps(again.tracker.learned())) == json.loads(json.dumps(app.tracker.learned()))
    assert {k: len(v) for k, v in again.calibrator.data().items()} == {k: len(v) for k, v in app.calibrator.data().items()}


def test_a_nan_is_a_model_failure_never_nobody_there(tmp_path, caplog):
    """A NaN in a weight made every output NaN: "nobody there" without a word (NaN > c is false), and
    the app stopped at the next save or in to_dict. Now the model starts over, with a log line."""
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    run_a_while(app, 20.0)
    assert app.zone_states["wohn"].occupied
    tracker = app.tracker
    for hy in tracker.hyps:
        hy.logw = math.nan
    tracker._version += 1
    assert not app.tick(1000.0 + 20.5)  # no raise
    assert app.tracker is not tracker and "not finite" in caplog.text
    with pytest.raises(FloatingPointError):
        tracker._normalize()
    st = ZoneState(probability=math.nan, p_enter=math.nan, eta=math.inf, approaching=True)
    assert not st.finite() and st.to_dict()["probability"] is None  # never raises


def test_the_ld2410_factor_of_a_person_all_in_view_never_takes_log_0():
    """All of a person in an LD2410C's view (masses summing to 1) and every ratio below e^-745: until
    now math.log(0), the error that stopped 0.9.3 ("math domain error")."""
    class Stats:
        def log_ratio(self, mu, mu0, lik):
            return np.array([-1000.0, -2000.0])

    m = np.array([0.5, 0.5])
    lr, log_f = Tracker._ld_ratio(object(), (m, np.zeros((2, 16))), np.zeros(16), Stats(), None)
    assert math.isclose(log_f, -1000.0 + math.log(0.5), rel_tol=1e-12)
    # the usual case as before
    lr, log_f = Tracker._ld_ratio(object(), (np.array([0.3, 0.2]), np.zeros((2, 16))), np.zeros(16),
                                  type("S", (), {"log_ratio": lambda self, *a: np.array([1.0, -1.0])})(), None)
    assert math.isclose(log_f, math.log(0.5 + 0.3 * math.e + 0.2 / math.e))


class FakeMqtt:
    def __init__(self):
        self.sent = []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload))

    async def subscribe(self, topic):
        pass

    def availability(self):
        return [p for t, p in self.sent if t == AVAILABILITY]


async def feed(app, seconds, uptime):
    """Empty frames of both sensors every 0.1 s, as the boards send them when they see something."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        uptime[0] += 100
        for sid in ("a", "b"):
            frame = {"uptime_ms": uptime[0], "targets": [{"x": 500, "y": 2000, "speed": 0, "slot": 1}], "ld2410": {}}
            app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), time.time())
        await asyncio.sleep(0.1)


def test_online_only_with_fresh_states(tmp_path):
    """At a (re)connect the broker may still hold "online" and the states of a run that ended without
    its last will: Home Assistant showed them as now (Node-RED: unavailable -> on is a change). The
    app says "offline" at once and "online" only after its first fresh states."""
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=True)
    mqtt = FakeMqtt()

    async def main():
        await app._on_client(mqtt)
        assert mqtt.sent[0] == (AVAILABILITY, "offline")
        task = asyncio.create_task(app.housekeeping())
        await asyncio.sleep(0.3)
        assert mqtt.availability() == ["offline"]  # no frame yet: nothing fresh to say
        await feed(app, 0.5, [1000])
        task.cancel()

    asyncio.run(main())
    assert mqtt.availability() == ["offline", "online"]
    topics = [t for t, _ in mqtt.sent]
    first_state = min(i for i, t in enumerate(topics) if t.endswith("/state") and "zone" in t)
    assert topics.index(AVAILABILITY, 1) > first_state


def test_a_model_that_fails_again_and_again_is_unavailable(tmp_path, monkeypatch, caplog):
    """A failure that comes back after every rebuild (here: every tick): until 0.18 the model was
    rebuilt at every one, and Home Assistant kept the last states as "online". Now after DOWN_AFTER
    failures the entities are unavailable, the model rests RETRY s between tries, and once it has run
    RETRY s again they are online again."""
    monkeypatch.setattr(app_module, "RETRY", 0.6)
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=True)
    mqtt = FakeMqtt()
    builds = []
    real_reset = App._reset_tracker

    def counted(self):
        builds.append(1)
        real_reset(self)

    monkeypatch.setattr(App, "_reset_tracker", counted)
    real_states = Tracker.zone_states
    broken = [False]

    def zone_states(self):
        if broken[0]:
            raise TypeError("'NoneType' object is not subscriptable")
        return real_states(self)

    monkeypatch.setattr(Tracker, "zone_states", zone_states)
    uptime = [1000]

    async def main():
        await app._on_client(mqtt)
        task = asyncio.create_task(app.housekeeping())
        await feed(app, 0.5, uptime)
        assert mqtt.availability() == ["offline", "online"]
        broken[0] = True
        await feed(app, 2.0, uptime)
        assert app.down and mqtt.availability() == ["offline", "online", "offline"]
        assert len(builds) <= app_module.DOWN_AFTER + 4, len(builds)  # not one per tick: one per RETRY
        broken[0] = False
        await feed(app, 2.0, uptime)
        task.cancel()

    asyncio.run(main())
    assert not app.down and mqtt.availability() == ["offline", "online", "offline", "online"]
    assert caplog.text.count("FAILS AGAIN AND AGAIN") == 1 and "runs again" in caplog.text
