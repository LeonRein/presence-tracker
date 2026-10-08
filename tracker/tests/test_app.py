"""The app keeps running when the model fails (app.App._model_failed); its error reports replay to what
it showed (app.App.h_report)."""

import asyncio
import collections
import gzip
import json

from aiohttp.test_utils import TestClient, TestServer

from presence_tracker import app as app_module, code_hash
from presence_tracker.app import App
from presence_tracker.filter import Tracker
from presence_tracker.frames import SensorClock
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def test_model_error_does_not_stop_the_app(tmp_path):
    config = Config.from_dict({"sensors": [{"id": "s", "name": "s", "x": 0, "y": 0, "heading": 90, "placed": True}],
                               "zones": [{"id": "r", "name": "R", "kind": "room", "shape": "polygon",
                                          "points": [[-3, 0], [3, 0], [3, 5], [-3, 5]]}]})
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    broken = app.tracker

    def fail(*args, **kwargs):
        raise ValueError("math domain error")

    broken.process_frame = fail
    frame = {"uptime_ms": 1000, "targets": [], "ld2410": {}}
    app.on_message("presence/s/frame", json.dumps(frame).encode(), 1000.0)  # must not raise
    assert app.tracker is not broken and isinstance(app.tracker, Tracker)
    app.on_message("presence/s/frame", json.dumps({**frame, "uptime_ms": 1100}).encode(), 1000.1)
    assert app.tracker.start is not None


def test_a_report_replays_to_what_the_app_showed(tmp_path):
    """An error report holds what the replay needs (config, what was learned, when the model
    started, the frames) and what the app showed; replayed, the model believes what the app did."""
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    bg = app.tracker.ld_background  # learned beyond the prior before the start,
    bg.num["a"], bg.den["a"] = 2 * bg.prior * 600.0, 600.0
    bg.learn_echoes("a", 0.5, bg.echo_rate, 1.0, 600.0)
    app._model_started(None)  # as if loaded from ld2410.json
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (4.5, 1.0), start=5, pauses={2: 20, 3: 60}))
    next_tick = 0.0
    # every frame sent (no heartbeat thinning): the frames drive the app's ticks here
    for t, sid, frame in simulate([a], sim_sensors(config, idle=0.0), 60.0, walls=config.wall_segments):
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        if t >= next_tick:
            assert app.tick(1000.0 + t)
            next_tick = t + 0.1

    async def report_and_download():
        # auto_decompress off: the bytes as the browser saves them behind Home Assistant's ingress
        async with TestClient(TestServer(app.web_app()), auto_decompress=False) as client:
            r = await client.post("/api/reports", json={"kind": "ghost", "room": "wohn", "minutes_ago": 0.5, "text": "test"})
            name = (await r.json())["name"]
            r = await client.get(f"/api/reports/{name}", headers={"Accept-Encoding": "gzip, deflate, br"})
            assert r.status == 200 and r.content_type == "application/gzip"
            assert "Content-Encoding" not in r.headers  # else the browser unpacks it under the .gz name
            return name, await r.read()

    name, body = asyncio.run(report_and_download())
    path = tmp_path / "reports" / name
    assert body == path.read_bytes()
    lines = [json.loads(x) for x in gzip.open(path, "rt").read().splitlines()]
    meta, messages = lines[0], lines[1:]
    assert meta["report"]["code"] == code_hash()
    assert 1000.0 <= meta["report"]["model_start"] < 1000.2  # the app's first frame
    assert meta["report"]["learned_at"] == meta["report"]["model_start"]
    assert meta["ld_background"]["echoes"]["a"] > 0
    # what was learned at the model's start, not at the report: it learned on since
    assert meta["ghost_map"] != json.loads(json.dumps(app.tracker.learned()["ghost_map"]))
    shown = [(m["t"], m["payload"]) for m in messages if m["topic"] == "app/shown"]
    assert len(shown) >= 55 and all(t1 > t0 for (t0, _), (t1, _) in zip(shown, shown[1:]))

    # replayed like tools/replay.py --report
    replay = Tracker(Config.from_dict(meta["config"]))
    replay.load_learned(meta)
    assert replay.ld_background.to_dict() == meta["ld_background"]
    clocks = collections.defaultdict(SensorClock)
    k = 0
    diff = 0.0
    for m in messages:
        if not m["topic"].endswith("/frame") or m["t"] < meta["report"]["model_start"]:
            continue
        sid = m["topic"].split("/")[1]
        t = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
        replay.process_frame(sid, t, m["payload"])
        replay.step(t)
        while k < len(shown) and shown[k][0] <= t:
            diff = max(diff, abs(1 - replay.count_distribution()["wohn"][0] - shown[k][1]["zones"]["wohn"][1]))
            k += 1
    assert k >= len(shown) - 1
    assert shown[-1][1]["zones"]["wohn"][2] and shown[-1][1]["persons"][0][1] == "observed"
    assert diff < 0.002, diff  # what the app showed is rounded to 0.001


def test_a_report_without_the_model_start_has_what_was_learned_at_the_report(tmp_path, monkeypatch):
    """The model started before the report's data: the replay starts from nothing known at its first
    frame, with what was learned at the report (the copy from the start may be days old)."""
    monkeypatch.setattr(app_module, "REPORT_WINDOW", 20.0)
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    app.clock = lambda: 1040.0
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), start=5, pauses={2: 30}))
    for t, sid, frame in simulate([a], sim_sensors(config), 40.0, walls=config.wall_segments):
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        assert app.tick(1000.0 + t)

    class Request:
        async def json(self):
            return {"kind": "ghost", "room": "wohn"}

    asyncio.run(app.h_report(Request()))
    path, = (tmp_path / "reports").glob("*.jsonl.gz")
    meta = json.loads(gzip.open(path, "rt").readline())
    assert meta["report"]["model_start"] < 1001 and meta["report"]["learned_at"] == 1040.0
    learned = json.loads(json.dumps(app.tracker.learned()))
    assert meta["ghost_map"] == learned["ghost_map"] != app.start_learned["ghost_map"]


def test_a_restarted_app_keeps_the_people_and_reports_from_where(tmp_path):
    """The app saves what it knows about the people (people.json, MODEL.md 5.3) and starts from it
    again, moved on over the time it did not run; a report then holds that state, and its replay
    believes what the app did."""
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (4.5, 1.0), (4.6, 1.0), start=2, pauses={2: 300}))
    frames = list(simulate([a], sim_sensors(config), 140.0, walls=config.wall_segments))
    app = App(tmp_path, publish=False)
    for t, sid, frame in frames:
        if t >= 60:
            break
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        app.tick(1000.0 + t)
    app._save_learned()  # as at the exit
    again = App(tmp_path, publish=False)  # 20 s later
    assert again.tracker.started_from is not None and again.start_state is again.tracker.started_from
    for t, sid, frame in frames:
        if t < 80:
            continue
        again.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        assert again.tick(1000.0 + t)
        assert 1 - again.tracker.count_distribution()["wohn"][0] > 0.9

    class Request:
        async def json(self):
            return {"kind": "ghost", "room": "wohn"}

    again.clock = lambda: 1140.0
    asyncio.run(again.h_report(Request()))
    path, = (tmp_path / "reports").glob("*.jsonl.gz")
    lines = [json.loads(x) for x in gzip.open(path, "rt").read().splitlines()]
    meta, messages = lines[0], lines[1:]
    assert meta["people"] == json.loads(json.dumps(again.start_state))
    shown = [(m["t"], m["payload"]) for m in messages if m["topic"] == "app/shown"]
    replay = Tracker(Config.from_dict(meta["config"]))
    replay.load_learned(meta)
    assert replay.restore_people(meta["people"])
    clocks = collections.defaultdict(SensorClock)
    k, diff = 0, 0.0
    for m in messages:
        if not m["topic"].endswith("/frame") or m["t"] < meta["report"]["model_start"]:
            continue
        sid = m["topic"].split("/")[1]
        t = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
        replay.process_frame(sid, t, m["payload"])
        replay.step(t)
        while k < len(shown) and shown[k][0] <= t:
            diff = max(diff, abs(1 - replay.count_distribution()["wohn"][0] - shown[k][1]["zones"]["wohn"][1]))
            k += 1
    assert k >= len(shown) - 1 and diff < 0.002, diff


def test_a_report_after_a_live_recalibration_replays_to_what_the_app_showed(tmp_path):
    """A sensor recalibrated between the model's start and the report ("Übernehmen": the model goes on,
    the people stay). Replayed with the configuration at the report, the replay was up to 0.12 off
    (BUGS 6): the report holds the one at the start and each one taken over since, with its time, and
    the replay takes them over as the app did (tools/replay.py)."""
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (4.5, 1.0), (1.0, 1.0), (1.02, 1.0), start=5,
                    pauses={2: 20, 4: 200}))

    class Request:
        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    done = False
    next_tick = 0.0
    for t, sid, frame in simulate([a], sim_sensors(config, idle=0.0), 120.0, walls=config.wall_segments):
        if t >= 40 and not done:
            d = app.config.to_dict()
            d["sensors"][0]["heading"] += 8.0  # sensor a recalibrated by 8 degrees
            r = asyncio.run(app.h_put_config(Request(d)))
            assert json.loads(r.text)["restarted"] is False
            done = True
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        if t >= next_tick:
            assert app.tick(1000.0 + t)
            next_tick = t + 0.1
    app.clock = lambda: 1120.0
    asyncio.run(app.h_report(Request({"kind": "ghost", "room": "wohn"})))
    path, = (tmp_path / "reports").glob("*.jsonl.gz")
    lines = [json.loads(x) for x in gzip.open(path, "rt").read().splitlines()]
    meta, messages = lines[0], lines[1:]
    assert meta["config"]["sensors"][0]["heading"] == 45 and len(meta["config_changes"]) == 1
    t_change, changed = meta["config_changes"][0]
    assert 1039.5 < t_change <= 1040.1 and changed["sensors"][0]["heading"] == 53

    # replayed like tools/replay.py --report
    shown = [(m["t"], m["payload"]) for m in messages if m["topic"] == "app/shown"]
    replay = Tracker(Config.from_dict(meta["config"]))
    replay.load_learned(meta)
    changes = [(t, Config.from_dict(c)) for t, c in meta["config_changes"]]
    clocks = collections.defaultdict(SensorClock)
    k, diff = 0, 0.0
    for m in messages:
        if not m["topic"].endswith("/frame") or m["t"] < meta["report"]["model_start"]:
            continue
        sid = m["topic"].split("/")[1]
        t = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
        while changes and t > changes[0][0]:
            t_c, cfg = changes.pop(0)
            replay.step(t_c)
            assert not replay.reconfigure(cfg)
        replay.process_frame(sid, t, m["payload"])
        replay.step(t)
        while k < len(shown) and shown[k][0] <= t:
            diff = max(diff, abs(1 - replay.count_distribution()["wohn"][0] - shown[k][1]["zones"]["wohn"][1]))
            k += 1
    assert not changes and k >= len(shown) - 1
    assert diff < 0.002, diff  # what the app showed is rounded to 0.001


def test_the_live_view_counts_the_calibration_data_in_a_thread(tmp_path):
    """The calibration's status (a day of data: 30 ms here, about 0.25 s on Home Assistant) is counted
    in a thread; the live view sends the last count and the new one once it is there."""
    config = flat_config(entry=True)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (4.5, 1.0), start=1))
    for t, sid, frame in simulate([a], sim_sensors(config), 20.0, walls=config.wall_segments):
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        assert app.tick(1000.0 + t)

    async def live():
        first = json.loads(app.live_message())["calibration"]
        await app._counting
        return first, json.loads(app.live_message())["calibration"]

    first, then = asyncio.run(live())
    assert first is None and then == app.calibrator.status() and then["frames"]
