"""The header's sensor count (static/js/util.js sensorsOnline): every sensor in use (the core's plan of the saved
configuration) counts, also one the live message does not have. 9.10. two boards were offline and the header
said 5/5. Runs the module in Node; skipped without it."""

import json
import pathlib
import shutil
import subprocess

import presence_core
import pytest

from presence_tracker.model import Config

UTIL = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js" / "util.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def sensors_online(config, live):
    derived = json.loads(presence_core.derive(Config.from_dict(config).model_json()))  # as the web UI has it
    script = (f"import {{ sensorsOnline }} from {json.dumps(UTIL.as_uri())};\n"
              f"console.log(JSON.stringify(sensorsOnline({json.dumps(derived)}, {json.dumps(live)})));")
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def sensor(sid, enabled=True, placed=True):
    return {"id": sid, "name": sid, "enabled": enabled, "placed": placed, "x": 1.0, "y": 1.0}


def test_offline_sensors_count_in_the_header():
    config = {"sensors": [sensor(s) for s in ("kueche", "flur", "bad", "schlaf", "arbeit", "wohn", "ess")]
              + [sensor("alt", enabled=False), sensor("neu", placed=False)]}
    # the live message: only the sensors heard from since the model started (wohn, ess: never), one of
    # them offline, and one that is not configured at all
    live = {"sensors": {s: {"online": True} for s in ("kueche", "flur", "bad", "schlaf", "arbeit", "fremd")}}
    live["sensors"]["arbeit"] = {"online": False}
    assert sensors_online(config, live) == {"online": 4, "total": 7}
    live["sensors"]["arbeit"]["online"] = True
    live["sensors"].update(wohn={"online": True}, ess={"online": True})
    assert sensors_online(config, live) == {"online": 7, "total": 7}
    assert sensors_online(config, {}) == {"online": 0, "total": 7}
    assert sensors_online({"sensors": []}, live) == {"online": 0, "total": 0}
