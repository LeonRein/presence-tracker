"""PUT /api/config refuses values the model can't run with: an emptied field in the web UI arrived as
0, and light_cost 0 put every room above its threshold (every light on). The core's problem goes to the web UI,
which words it (static/js/util.js problemText, LABELS: run in Node, skipped without it)."""

import asyncio
import json
import os
import pathlib
import re
import shutil
import subprocess

import presence_core
import pytest
from aiohttp.test_utils import TestClient, TestServer

from presence_tracker.app import App, REPORT_KIND
from presence_tracker.model import Config, problem
from test_filter import flat_config

UTIL = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js" / "util.js"
APP = UTIL.parents[2] / "app.py"
node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def ui(expression: str):
    """An expression of the web UI's util.js (its exports in scope), evaluated in Node."""
    script = f"import * as util from {json.dumps(UTIL.as_uri())};\nconst {{ LABELS, problemText }} = util;\n" \
             f"console.log(JSON.stringify({expression}));"
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def put(app, data):
    async def run():
        async with TestClient(TestServer(app.web_app())) as client:
            r = await client.put("/api/config", data=json.dumps(data, allow_nan=True),
                                 headers={"Content-Type": "application/json"})
            return r.status, await r.json()
    return asyncio.run(run())


@pytest.fixture
def app(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    return App(tmp_path, publish=False)


@pytest.mark.parametrize("key, value", [
    ("light_cost", 0), ("light_cost", -1), ("light_cost", None), ("light_cost", float("nan")),
    ("light_cost", "2"), ("approach_cost", 0), ("lead_time", 0), ("lead_time", float("inf")),
    ("target_threshold", 0), ("target_threshold", 1), ("dwell_spread", 0), ("range_sigma_base", 0),
])
def test_out_of_range_params_are_refused(app, tmp_path, key, value):
    before = (tmp_path / "tracker.json").read_text()
    data = app.config.to_dict()
    data["params"][key] = value
    status, body = put(app, data)
    assert status == 400
    assert (body["problem"]["part"], body["problem"]["field"]) == ("params", key)
    assert (tmp_path / "tracker.json").read_text() == before
    assert app.config.data["params"][key] == Config.from_dict({}).data["params"][key]


@pytest.mark.parametrize("key, value", [("height", 0), ("x", None), ("y", float("nan")), ("scale", 0),
                                        ("fov", 0), ("range", -2), ("heading", float("inf"))])
def test_out_of_range_sensor_values_are_refused(app, key, value):
    data = app.config.to_dict()
    data["sensors"][0][key] = value
    status, body = put(app, data)
    assert status == 400 and (body["problem"]["part"], body["problem"]["field"]) == ("sensor", key)


def test_per_room_threshold_and_door_are_checked(app):
    data = app.config.to_dict()
    data["params"]["target_thresholds"] = {"wohn": 0}
    assert put(app, data)[0] == 400
    data = app.config.to_dict()
    data["doors"][0]["width"] = 0
    assert put(app, data)[0] == 400


def test_a_valid_edit_says_whether_the_model_restarted(app):
    data = app.config.to_dict()
    data["params"]["range_sigma_base"] = 0.2  # a parameter of the model
    status, body = put(app, data)
    assert status == 200 and body["restarted"] is True
    assert app.config.data["params"]["range_sigma_base"] == 0.2
    data["params"]["light_cost"] = 3.0  # outputs only: the people stay
    data["params"]["target_threshold"] = 0.6
    data["zones"][0]["name"] = "Wohnzimmer"
    status, body = put(app, data)
    assert status == 200 and body["restarted"] is False
    assert app.config.data["params"]["light_cost"] == 3.0 and app.tracker.config.data["params"]["light_cost"] == 3.0


def test_the_stored_config_goes_through_the_core(tmp_path, caplog):
    d = flat_config().to_dict()
    d["params"]["light_cost"] = 0
    (tmp_path / "tracker.json").write_text(json.dumps(d))
    with pytest.raises(presence_core.ConfigError):  # the model can't run with it: said, not run
        App(tmp_path, publish=False)
    assert "does not pass the check" in caplog.text


def test_the_ui_gets_the_limits(app):
    async def run():
        async with TestClient(TestServer(app.web_app())) as client:
            return await (await client.get("/api/config")).json()
    limits = asyncio.run(run())["limits"]
    assert limits["params"]["light_cost"] == [0.0, 1000.0, True, False]
    assert limits["sensor"]["height"][:2] == [0.1, 4.0]


def _area(**kw):
    return {"id": "z", "name": "Z", "kind": "area", "shape": "rect", "points": [[1, 1], [2, 2]], **kw}


@pytest.mark.parametrize("change, reason", [
    (lambda d: d["sensors"].append(dict(d["sensors"][0])), "id_twice"),
    (lambda d: d["sensors"][0].update(id="a/b"), "id_chars"),
    (lambda d: d["walls"].append({"points": [[1, 1]], "kind": "wall"}), "not_a_segment"),
    (lambda d: d["walls"].append({"points": [[1, 1], [2, 2], [3, 3]], "kind": "wall"}), "not_a_segment"),
    (lambda d: d["walls"].append({"points": [[1, 1], [1, 1]], "kind": "wall"}), "not_a_segment"),
    (lambda d: d["walls"].append({"points": [[1, 1], [2, 2]], "kind": "glass"}), "unknown_kind"),
    (lambda d: d["zones"].append(_area(kind="foo")), "unknown_kind"),
    (lambda d: d["zones"].append(_area(shape="blob")), "unknown_shape"),
    (lambda d: d["zones"].append(_area(shape="circle", points=[], radius=1.0)), "no_centre"),
    (lambda d: d["zones"].append(_area(shape="polygon", points=[[1, 1], [2, 2]])), "polygon_corners"),
    (lambda d: d["zones"].append(_area(points=[[1, 1]])), "rect_corners"),
    (lambda d: d["zones"].append(_area(id=None)), "no_id"),
    (lambda d: d["zones"].append(dict(d["zones"][0])), "id_twice"),
    (lambda d: d.update(ld_view={"a<-b": -0.5}), "not_at_least_0"),
])
def test_what_the_model_cannot_run_with_is_refused_with_400(app, tmp_path, change, reason):
    """Until 0.18 a one-point wall gave a 500, and a circle without a center was saved: every tick
    after it failed, also after every restart."""
    before = (tmp_path / "tracker.json").read_text()
    data = app.config.to_dict()
    change(data)
    status, body = put(app, data)
    assert status == 400 and body["problem"]["reason"] == reason, body
    assert (tmp_path / "tracker.json").read_text() == before


def test_a_plan_drawn_in_meters_may_be_scaled_above_1(app):
    """An SVG drawn in meters (one unit a meter): "Ausrichten" gives a scale of about 1.02 m per unit,
    refused until 0.18 - and with it every later edit, until the image was removed. The background image is
    the web UI's (it checks the layers, static/js/util.js layersError): the server stores it as it comes."""
    data = app.config.to_dict()
    data["background"] = {"layers": [{"x": 0, "y": 0, "scale": 1.02, "rotation": 0, "opacity": 0.5}]}
    assert put(app, data)[0] == 200
    data["background"]["layers"][0]["scale"] = 11
    assert put(app, data)[0] == 200 and app.config.data["background"]["layers"][0]["scale"] == 11


def test_a_door_on_no_wall_is_refused(app):
    """The editor puts a door exactly on its wall; the model reads that wall and nothing else (one rule:
    the core's plan.wall_of_door)."""
    data = app.config.to_dict()
    data["doors"][0]["x"] += 0.05  # the balcony door on the wall x = 6
    status, body = put(app, data)
    assert status == 400 and (body["problem"]["part"], body["problem"]["reason"]) == ("door", "not_on_wall")
    data["doors"][0]["x"] -= 0.0495  # half a millimetre off: mm rounding
    assert put(app, data)[0] == 200


def test_a_configuration_the_model_fails_with_is_refused_and_the_old_one_holds(app, tmp_path, monkeypatch):
    """App and model never disagree about the configuration: if the model fails with a new one, the
    old one holds in both, and the file is not written."""
    from presence_tracker.filter import Tracker
    before = (tmp_path / "tracker.json").read_text()
    old = app.config
    calls = []
    real = Tracker.zone_states

    def fail(self):
        if self.config is not old:
            calls.append(1)
            raise TypeError("'NoneType' object is not subscriptable")
        return real(self)

    monkeypatch.setattr(Tracker, "zone_states", fail)
    data = app.config.to_dict()
    data["zones"].append(_area())
    status, body = put(app, data)
    assert status == 400 and body["error"] == "model_refused" and "NoneType" in body["detail"] and calls
    assert app.config is old and app.tracker.config is old
    assert (tmp_path / "tracker.json").read_text() == before
    app.tracker.zone_states()  # the model runs


@node
def test_every_field_of_the_ui_has_a_label():
    # the web UI's fields: the core's limits; each needs its words in the web UI (and nothing else is worded)
    limits = presence_core.config_limits()
    labels = ui("LABELS")
    assert {group: set(fields) for group, fields in limits.items()} == \
        {group: set(labels[group]) for group in limits}


def _sensor(**kw):
    return {"id": "s", "name": "Bad", "x": 1, "y": 1, **kw}


# every reason the core gives (ConfigProblem.reason), each with a configuration that has it
_PROBLEMS = {
    "not_an_object": [5, {"params": 5}, {"params": {"target_thresholds": 5}}, {"zones": [5]}, {"ld_view": 5}],
    "not_a_list": [{"zones": 5}],
    "empty": [{"params": {"light_cost": None}}],
    "not_a_number": [{"params": {"light_cost": "x"}}],
    "out_of_range": [{"params": {"light_cost": 0}}, {"params": {"target_thresholds": {"wohn": 1}}},
                     {"sensors": [_sensor(range=50)]}, {"doors": [{"x": 1, "y": 1, "width": 0}]}],
    "not_at_least_0": [{"zones": [_area(radius=-1)]}, {"ld_view": {"a<-b": "x"}}],
    "not_a_bool": [{"sensors": [_sensor(mirror=1)]}],
    "no_id": [{"sensors": [{"name": "x"}]}, {"zones": [_area(id=None)]}],
    "id_chars": [{"sensors": [_sensor(id="a#b")]}],
    "id_twice": [{"sensors": [_sensor(), _sensor()]}, {"zones": [_area(), _area()]}],
    "not_points": [{"walls": [{"points": [[1, "a"], [2, 2]]}]}, {"zones": [_area(points=[[1, None], [2, 2]])]}],
    "not_a_segment": [{"walls": [{"points": [[1, 1]]}]}],
    "unknown_kind": [{"walls": [{"points": [[1, 1], [2, 2]], "kind": "glass"}]}, {"zones": [_area(kind="x")]}],
    "unknown_shape": [{"zones": [_area(shape="blob")]}],
    "rect_corners": [{"zones": [_area(points=[[1, 1]])]}],
    "polygon_corners": [{"zones": [_area(shape="polygon")]}],
    "no_centre": [{"zones": [_area(shape="circle", points=[])]}],
    "no_position": [{"doors": [{"x": 1}]}],
    "not_on_wall": [{"walls": [{"points": [[0, 0], [4, 0]], "kind": "divider"}], "doors": [{"x": 1, "y": 0}]}],
    "too_many_sensors": [{"sensors": [_sensor(id=f"s{k}") for k in range(65)]}],
    "too_large": [{"zones": [{"id": "r", "name": "R", "kind": "room", "shape": "polygon",
                              "points": [[0, 0], [200, 0], [200, 200]]}]}],
    "unreadable": [],  # not JSON: below
}


@node
def test_every_problem_of_the_core_is_a_german_sentence():
    problems = []
    for reason, configs in _PROBLEMS.items():
        for config in configs:
            with pytest.raises(presence_core.ConfigError) as e:
                Config.from_dict(config)
            assert e.value.args[0].reason == reason
            problems.append(problem(e.value))
    with pytest.raises(presence_core.ConfigError) as e:
        presence_core.check_config("{")
    assert e.value.args[0].reason == "unreadable"
    problems.append(problem(e.value))
    json.dumps(problems)  # as the server sends them
    texts = ui(f"{json.dumps(problems)}.map(problemText)")
    for p, text in zip(problems, texts):
        assert text and text[0].isupper() and text.endswith((".", "?")) or "unvollständig" in text, (p, text)
        assert "None" not in text and "null" not in text and "undefined" not in text and "{" not in text, (p, text)
        assert not text.startswith("Konfiguration: " + p["reason"]), (p, text)  # a sentence of its own
    assert texts[-1].startswith("Konfiguration unvollständig oder falsch: ")
    light = next(t for p, t in zip(problems, texts) if p["field"] == "light_cost" and p["reason"] == "out_of_range")
    assert light == "Kosten: Licht ohne Person muss größer als 0 und höchstens 1000 sein, nicht 0."



@node
def test_every_reason_and_error_of_the_server_is_worded():
    # the calibration's reasons (the core's: kind and numbers), the server's error keys and the report kinds:
    # the web UI words them (util.js reasonText, errorText, REPORT_KINDS), the server sends only keys
    reasons = [{"kind": "few", "stretches": 1, "min": 3}, {"kind": "worse", "after": 0.12, "before": 0.05},
               {"kind": "conflict", "plan": 40.2, "pairs": 50.1}, {"kind": "ambiguous", "alt": 123.4},
               {"kind": "heading_uncertain", "sd": 6.2}, {"kind": "no_ruler"},
               {"kind": "scale_uncertain", "sd": 0.091}, {"kind": "mirrored", "gain": 12.3}]
    keys = sorted(set(re.findall(r'"error": "(\w+)"', APP.read_text())) | {"no_walking"})
    got = ui(f"[{json.dumps(reasons)}.map(util.reasonText), "
             f"{json.dumps(keys)}.map(e => util.errorText({{error: e, detail: 502}})), util.REPORT_KINDS]")
    for text in got[0] + got[1]:
        assert text[0].isupper() and text.endswith((".", "?")) or text.endswith("502"), text
        assert "{" not in text and "undefined" not in text, text
    assert "(12 % statt 5 %)" in got[0][1] and "± 0,09)" in got[0][6]
    assert len(keys) > 10 and all(k not in t for k, t in zip(keys, got[1])), got[1]
    assert "wrong_place" in got[2] and all(REPORT_KIND.fullmatch(k) for k in got[2])

@pytest.mark.skipif(not os.environ.get("PT_RELEASE_WHEEL"), reason="against the release wheel only (CI)")
def test_the_release_wheel_has_no_test_hooks():
    # here, not in a module that simulates: those are all "hooks" (conftest)
    hooks = ["build_export", "hidden_at", "panic_for_test", "Geometry", "model_defaults", "ld_prior", "ld_expected"]
    assert [h for h in hooks if hasattr(presence_core, h)] == []
    readings = ["normalize", "spoil_weights_for_test", "ld_last", "loglik", "hyp_weights", "check", "hypotheses",
                "tracks", "room_at", "tile_near", "sight", "observed", "background", "count_distribution",
                "place_distribution", "occupancy"]
    assert [h for h in readings if hasattr(presence_core.Core, h)] == []
