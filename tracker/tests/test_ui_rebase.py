"""An edit the server refuses (400) while the user edits on (static/js/util.js rebase): the editor goes
back to what the server holds, but keeps what was edited meanwhile. Until 0.18 the reload replaced
the whole configuration and those edits were lost. Runs the module in Node; skipped without it."""

import json
import pathlib
import shutil
import subprocess

import pytest

UTIL = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js" / "util.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def rebase(base, sent, now):
    script = (f"import {{ rebase }} from {json.dumps(UTIL.as_uri())};\n"
              f"console.log(JSON.stringify(rebase({json.dumps(base)}, {json.dumps(sent)}, {json.dumps(now)})));")
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_a_refused_edit_goes_back_and_later_edits_stay():
    base = {"params": {"light_cost": 2.0, "lead_time": 2.0},
            "zones": [{"id": "wohn", "name": "Wohn"}, {"id": "kueche", "name": "Küche"}],
            "walls": [{"points": [[0, 0], [1, 0]]}], "background": {"layers": []}}
    sent = json.loads(json.dumps(base))
    sent["params"]["light_cost"] = 0  # refused
    now = json.loads(json.dumps(sent))
    now["params"]["lead_time"] = 3.0  # edited while the request was under way
    now["zones"][1]["name"] = "Kochen"
    now["zones"].append({"id": "bad", "name": "Bad"})
    out = rebase(base, sent, now)
    assert out["params"] == {"light_cost": 2.0, "lead_time": 3.0}
    assert out["zones"] == [{"id": "wohn", "name": "Wohn"}, {"id": "kueche", "name": "Kochen"}, {"id": "bad", "name": "Bad"}]
    assert out["walls"] == base["walls"] and out["background"] == base["background"]


def test_nothing_edited_meanwhile_is_what_the_server_holds():
    base = {"params": {"light_cost": 2.0}, "zones": [{"id": "a", "name": "A"}]}
    sent = {"params": {"light_cost": 0}, "zones": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}
    assert rebase(base, sent, sent) == base
    # a zone the refused request added is gone again; one deleted meanwhile stays deleted
    now = {"params": {"light_cost": 0, "x": 1}, "zones": [{"id": "b", "name": "B"}]}
    assert rebase(base, sent, now) == {"params": {"light_cost": 2.0, "x": 1}, "zones": []}
