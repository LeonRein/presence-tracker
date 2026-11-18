"""Echtzeit (realtime.py, DOCS.md "Echtzeit"): the model in its own thread on a bounded queue, the receive time
taken at arrival, publishes that do not wait for the broker, the diagnostics. 17.11. (0.65.0): the core was slower
than the frames, they waited up to 42 min in an unbounded queue, and Home Assistant got 42 min old states as fresh."""

import asyncio
import json
import os
import signal
import socket
import statistics
import time

import aiohttp
import pytest

from presence_tracker import app as app_module
from presence_tracker import pause, realtime
from presence_tracker.app import App
from presence_tracker.filter import Tracker
from presence_tracker.frames import Frame
from presence_tracker.ha import AVAILABILITY, DIAGNOSTICS, DIAGNOSTICS_STATE, Discovery
from presence_tracker.realtime import CALL, FRAME, PAUSE, Inbox, Item, StampedQueue
from sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def frame_item(sensor, n, queued=None):
    return Item(FRAME, n, recv=1000.0 + n, queued=queued, sensor=sensor)


# ------------------------------------------------------------------ the rule


def test_a_compaction_keeps_the_newest_frame_of_each_sensor_and_every_other_message():
    inbox = Inbox()
    for item in [frame_item("a", 1), frame_item("b", 2), Item(PAUSE, b"ON"), frame_item("a", 3),
                 frame_item("a", 4), frame_item("b", 5), frame_item("c", 6)]:
        inbox.put(item)
    assert inbox.compact() == 3
    left = []
    while True:
        item, lost = inbox.get(0.0)
        if item is None:
            break
        left.append((item.kind, item.sensor, item.payload, lost))
    # in their places; that data was lost goes with the first message the model takes
    assert left == [(PAUSE, None, b"ON", 3), (FRAME, "a", 4, 0), (FRAME, "b", 5, 0), (FRAME, "c", 6, 0)]
    assert inbox.load.dropped == 3 and inbox.load.compactions == 1
    assert inbox.compact() == 0  # nothing more to drop: no new loss


def test_the_queue_is_compacted_by_age_and_bounded_in_frames():
    inbox = Inbox(after=0.05, limit=10)
    for k in range(4):
        inbox.put(frame_item("ab"[k % 2], k))
    time.sleep(0.08)  # the oldest waited too long
    item, lost = inbox.get(0.0)
    assert (item.payload, lost) == (2, 2)  # a's and b's newest; the two before them dropped
    for k in range(100):  # the model hangs: never more than limit frames wait
        inbox.put(frame_item("abc"[k % 3], k))
    assert inbox.frames <= 10 and inbox.waiting_max <= 10
    calls = Inbox()  # a call (the web UI) goes before the frames, and is never dropped
    calls.put(frame_item("a", 1))
    calls.put(Item(CALL, "call"))
    assert calls.get(0.0)[0].kind == CALL


@pytest.mark.hooks
def test_dropped_frames_are_lost_data_to_the_core_not_watched_and_empty():
    """Why the core is told (realtime.py): frames dropped silently leave a gap under 6 s, which the core counts as
    watched and empty (MODEL.md 4.4) - evidence that nobody new appeared, which nobody measured. Told (Tracker
    .reset_tracks), the sensors' tracks end as for lost data, the next frames are a start, and the gap is watched by
    nobody. The person is still there afterwards."""
    config = flat_config(entry=True)
    person = Person(walk((1.0, 1.0), (4.0, 3.0), start=1, pauses={1: 60}))
    frames = list(simulate([person], sim_sensors(config), 30.0, plan=config))

    def run(told: bool):
        tr = Tracker(config)
        events = []
        tr.listeners.append(lambda kind, payload: events.append((tr.now, kind, payload)))
        clocks = {}
        for t, sid, frame in frames:
            if 15.0 < t <= 18.0:  # dropped (a compaction: 3 s of every sensor)
                continue
            if told and t > 18.0 and not clocks.get("told"):
                clocks["told"] = True
                tr.reset_tracks()
            tr.process_frame(sid, 1000.0 + t, Frame.of({**frame, "uptime_ms": int(1000 * t) + 1000}))
            tr.step(1000.0 + t)
        watched = [p[1] for t, kind, p in events if kind == "watch" and t > 1015.0 and p[1] > 1.0]
        ends = [p[4] for t, kind, p in events if kind == "track_end" and 1015.0 < t <= 1018.5]
        return watched, ends, tr.zone_states()["_total"].count

    watched, ends, count = run(told=False)
    assert watched and min(watched) > 2.5  # the 3 s dropped counted as watched by the sensors
    watched, ends, count = run(told=True)
    assert not watched  # the gap is watched by nobody: it says nothing
    assert ends and all(ends)  # every live track ended for lost data
    assert count == 1


# ------------------------------------------------------------------ arrival, publishing


def test_the_receive_time_is_taken_when_aiomqtt_queues_a_message():
    class Msg:
        topic, payload = "presence/a/frame", b"{}"

    async def main():
        q = StampedQueue()
        before = time.time()
        q.put_nowait(Msg())
        await asyncio.sleep(0.2)  # the loop gets to it later
        return before, await q.get()

    before, (recv, queued, msg) = asyncio.run(main())
    assert before <= recv < before + 0.05 and isinstance(msg, Msg)
    full = StampedQueue(maxsize=2)  # frames beyond the bound are dropped and counted, the rest goes in
    for _ in range(4):
        full.put_nowait(Msg())
    switch = Msg()
    switch.topic = pause.COMMAND
    full.put_nowait(switch)
    assert full.qsize() == 3 and full.lost == 2


class SlowBroker:
    """An MQTT client whose broker confirms a QoS 1 message (PUBACK) only after `puback` s."""

    def __init__(self, puback=0.5):
        self.puback = puback
        self.sent = []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload, qos, retain))
        if qos:
            await asyncio.sleep(self.puback)

    async def subscribe(self, topic):
        pass


def test_publishing_does_not_wait_for_the_broker(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=True)
    broker = SlowBroker(puback=1.0)

    async def main():
        app.client = broker
        start = time.monotonic()
        for k in range(20):
            assert await app._publish(f"t/{k}", str(k), retain=True)
        took = time.monotonic() - start
        pending = len(app._pending)
        await asyncio.sleep(1.1)
        return took, pending

    took, pending = asyncio.run(main())
    assert took < 0.1 and pending == 20  # handed over, none waited for
    assert [t for t, *_ in broker.sent] == [f"t/{k}" for k in range(20)]  # in order
    assert {(qos, retain) for *_, qos, retain in broker.sent} == {(1, True)}  # states stay QoS 1, retained
    assert not app._pending


def test_the_diagnostics_are_diagnostic_entities_that_expire():
    sent = {}

    async def publish(topic, payload, retain):
        sent[topic] = (payload, retain)

    d = Discovery(publish)
    asyncio.run(d.sync([("wohn", "Wohnzimmer", True)]))
    configs = {json.loads(p)["unique_id"]: json.loads(p) for t, (p, _) in sent.items() if t.endswith("/config")}
    diag = {k: c for k, c in configs.items() if "diagnose" in k}
    assert len(diag) == len(DIAGNOSTICS) == 5
    for c in diag.values():
        assert c["entity_category"] == "diagnostic" and c["state_topic"] == DIAGNOSTICS_STATE
        assert "availability_topic" not in c and c["expire_after"] > 0  # readable while the rooms are not
    assert diag["presence_tracker_diagnose_backlog"]["unit_of_measurement"] == "s"
    asyncio.run(d.diagnostics({"backlog": 0.1, "cpu": 3.0, "hypotheses": 2, "untracked": 1, "fps": 22.0}))
    payload, retained = sent[DIAGNOSTICS_STATE]
    assert not retained and json.loads(payload)["backlog"] == 0.1


def test_with_the_option_a_backlog_makes_every_entity_unavailable(tmp_path):
    """offline_backlog (DOCS.md "Optionen"; off by default): above it Home Assistant gets "unavailable", back
    below half of it "online" again with the next fresh states."""
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=True, offline_backlog=4.0)
    broker = SlowBroker(puback=0.0)
    backlog = [0.0]

    class Worker:
        busy = None

        def backlog(self):
            return backlog[0]

    async def main():
        app.client = broker
        app.worker = Worker()
        said = []
        for b in (1.0, 5.0, 3.0, 1.5):
            backlog[0] = b
            app._watch_backlog()
            await app._set_available(not app.behind)
            said.append(app.available)
        return said

    assert asyncio.run(main()) == ["online", "offline", "offline", "online"]
    assert App(tmp_path, publish=False).offline_backlog == 0.0  # off unless set


# ------------------------------------------------------------------ the load test


class Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, payload


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("case, speed, delay", [
    ("slow core", 1.0, 0.1),  # frames in real time (2 sensors, 22/s), the core takes 0.1 s each: 2.2 times too slow
    ("frames faster than real time", 10.0, 0.01),  # 220 frames/s, the core 100/s
])
def test_under_overload_the_backlog_stays_bounded_and_the_loop_answers(tmp_path, monkeypatch, case, speed, delay):
    """The app as it runs live (MQTT, the model's thread, the web UI, Home Assistant), with more frames than the
    model can take and a broker that confirms only after 0.3 s. Until Echtzeit the backlog grew without bound
    (17.11.: linearly to 42 min), the loop waited for the model and for every confirmation. Now: the backlog is
    measured per frame and stays below the compaction's bound, the queue is bounded, the model is told of each
    loss, and the loop - the web UI, the publishes - answers at once."""
    import aiomqtt

    after, duration = 1.0, 6.0
    monkeypatch.setattr(realtime, "COMPACT_AFTER", after)
    monkeypatch.setattr(app_module, "DIAGNOSE_EVERY", 0.5)
    original, resets = Tracker.process_frame, []

    def slow(self, *args):
        original(self, *args)
        time.sleep(delay)  # the core computing (it releases the GIL as time.sleep does)

    reset = Tracker.reset_tracks
    monkeypatch.setattr(Tracker, "process_frame", slow)
    monkeypatch.setattr(Tracker, "reset_tracks", lambda self: (resets.append(time.monotonic()), reset(self)))
    brokers = []

    class Client:
        def __init__(self, *args, queue_type=None, **kwargs):
            self.queue = queue_type()
            self.sent = []
            brokers.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, topic):
            pass

        async def publish(self, topic, payload, qos=0, retain=False):
            self.sent.append((time.monotonic(), topic, payload))
            if qos:
                await asyncio.sleep(0.3)  # a slow broker's PUBACK

        @property
        def messages(self):
            async def gen():
                while True:
                    yield await self.queue.get()
            return gen()

    monkeypatch.setattr(aiomqtt, "Client", Client)
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    people = [Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (5.0, 1.0), (1.0, 1.0), start=0.5, speed=0.8)),
              Person(walk((3.0, 1.0), (4.0, 4.0), start=0.0, pauses={1: 100}))]
    frames = list(simulate(people, sim_sensors(config), duration * speed + 1, plan=config))
    app = App(tmp_path, publish=True, mqtt={"host": "x"})
    port = free_port()
    got = {"loop": [], "local": [], "model": [], "backlog": [], "waiting": [], "pending": []}

    async def produce():
        while not brokers:
            await asyncio.sleep(0.01)
        queue, m0 = brokers[0].queue, time.monotonic()
        paused = False
        for t, sid, frame in frames:
            wait = m0 + t / speed - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if time.monotonic() - m0 > duration:
                break
            frame = {**frame, "uptime_ms": int(1000 * (time.monotonic() - m0)) + 5000}
            queue.put_nowait(Msg(f"presence/{sid}/frame", json.dumps(frame).encode()))
            if not paused and time.monotonic() - m0 > duration / 2:  # "Lernen pausieren" in the middle
                paused = time.time()
                got["pause_backlog"] = app.backlog()
                queue.put_nowait(Msg(pause.COMMAND, b"ON"))
        got["pause_sent"] = paused

    async def probe():  # how late the loop wakes up, and what the web UI answers
        async with aiohttp.ClientSession() as session:
            while True:
                start = time.monotonic()
                await asyncio.sleep(0.01)
                got["loop"].append(time.monotonic() - start - 0.01)
                got["backlog"].append(app.backlog())
                got["waiting"].append(app.worker.inbox.frames if app.worker else 0)
                got["pending"].append(len(app._pending))
                if len(got["loop"]) % 10 == 0:
                    for key, path in (("local", "/api/recording"), ("model", "/api/config")):
                        start = time.monotonic()
                        try:
                            async with session.get(f"http://127.0.0.1:{port}{path}") as r:
                                assert r.status == 200
                                await r.read()
                        except aiohttp.ClientConnectionError:
                            continue
                        got[key].append(time.monotonic() - start)

    async def main():
        asyncio.get_running_loop().call_later(duration + 0.5, os.kill, os.getpid(), signal.SIGTERM)
        tasks = [asyncio.create_task(produce()), asyncio.create_task(probe())]
        await app.run("127.0.0.1", port)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(main())
    load, inbox = app.load, app.worker.inbox
    sent = brokers[0].sent
    states = [t for t, topic, _ in sent if topic.startswith("presence-tracker/zone/") and topic.endswith("/state")]
    diagnostics = [json.loads(p) for _, topic, p in sent if topic == DIAGNOSTICS_STATE]
    numbers = {
        "case": case, "frames_in": load.arrived, "frames_done": load.done, "dropped": load.dropped,
        "compactions": load.compactions, "resets": len(resets), "backlog_max": round(max(got["backlog"]), 2),
        "backlog_median": round(statistics.median(got["backlog"]), 2), "waiting_max": inbox.waiting_max,
        "loop_late_max_ms": round(1000 * max(got["loop"]), 1), "web_local_max_ms": round(1000 * max(got["local"]), 1),
        "web_model_max_ms": round(1000 * max(got["model"]), 1), "pending_max": max(got["pending"]),
        "state_publishes": len(states), "diagnostics": len(diagnostics),
        "publishes": len(sent),
        "diagnosed_backlog_max": max(d["backlog"] for d in diagnostics),
        "pause_arrival_vs_sent_ms": round(1000 * (app.pauses.intervals[0][0] - got["pause_sent"]), 1),
        "pause_backlog": round(got["pause_backlog"], 2),
    }
    print("\nBELASTUNG", json.dumps(numbers))
    if os.environ.get("PT_LOAD_OUT"):
        with open(os.environ["PT_LOAD_OUT"], "a") as f:
            f.write(json.dumps(numbers) + "\n")
    # more frames than the model takes: some were dropped, the core was told before the next one each time
    assert load.done < load.arrived and load.dropped > 0 and load.compactions >= 2
    assert 1 <= len(resets) <= load.compactions
    # the backlog is measured and bounded: never much more than the bound plus one frame's and one tick's time
    assert 0.3 < max(got["backlog"]) < after + 2 * delay + 0.4
    assert 0.3 < numbers["diagnosed_backlog_max"] < after + 2 * delay + 0.4
    # the queue is bounded: about the frames of `after` s, not the frames of the whole run
    assert inbox.waiting_max <= 22 * speed * (after + 0.5) + 4 < load.arrived
    # the loop answers: it wakes up in time, the web UI answers, without and with the model
    assert max(got["loop"]) < 0.1
    assert len(got["local"]) >= 10 and max(got["local"]) < 0.15
    assert len(got["model"]) >= 10 and max(got["model"]) < after
    # it publishes all along without waiting for the broker's confirmations (several QoS 1 ones unconfirmed at
    # once): the states (online after the first fresh ones) and every 0.5 s the diagnostics
    assert max(got["pending"]) > 1 and states and len(diagnostics) >= duration / 0.5 - 3
    assert [p for _, topic, p in sent if topic == AVAILABILITY][-1] == "offline"  # the stop
    assert "online" in [p for _, topic, p in sent if topic == AVAILABILITY]
    # "Lernen pausieren" at its arrival, not when the model got to it (it waited `pause_backlog` s)
    assert abs(numbers["pause_arrival_vs_sent_ms"]) < 20
    assert got["pause_backlog"] > 0.1
