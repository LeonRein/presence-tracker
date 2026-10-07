"""The app keeps running when the model fails (app.App._model_failed)."""

import json

from presence_tracker.app import App
from presence_tracker.filter import Tracker
from presence_tracker.model import Config


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
