"""The app keeps running when the model fails (app.App._model_failed); its error reports replay to what
it showed (app.App.h_report)."""

import asyncio
import collections
import gzip
import json

from presence_tracker import code_hash
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
    config = flat_config(entry=True, people=2)
    config.save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    app.tracker.ld_background.learn_echoes("a", 0.5)  # something learned beyond the prior
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (4.5, 1.0), start=5, pauses={2: 20, 3: 60}))
    next_tick = 0.0
    for t, sid, frame in simulate([a], sim_sensors(config), 60.0, walls=config.wall_segments):
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + t)
        if t >= next_tick:
            assert app.tick(1000.0 + t)
            next_tick = t + 0.1

    class Request:
        async def json(self):
            return {"kind": "ghost", "room": "wohn", "minutes_ago": 0.5, "text": "test"}

    asyncio.run(app.h_report(Request()))
    path, = (tmp_path / "reports").glob("*.jsonl.gz")
    lines = [json.loads(x) for x in gzip.open(path, "rt").read().splitlines()]
    meta, messages = lines[0], lines[1:]
    assert meta["report"]["code"] == code_hash()
    assert 1000.0 <= meta["report"]["model_start"] < 1000.2  # the app's first frame
    assert meta["ld_background"]["echoes"]["a"] > 0
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
    assert diff < 0.05, diff
