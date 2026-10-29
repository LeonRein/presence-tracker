"""PUT /api/config refuses values the model can't run with: an emptied field in the web UI arrived as
0, and light_cost 0 put every room above its threshold (every light on)."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from presence_tracker.app import App
from presence_tracker.model import LABELS, Config, check
from test_filter import flat_config


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
    assert LABELS["params"][key] in body["error"]
    assert (tmp_path / "tracker.json").read_text() == before
    assert getattr(app.config.params, key) == getattr(Config().params, key)


@pytest.mark.parametrize("key, value", [("height", 0), ("x", None), ("y", float("nan")), ("scale", 0),
                                        ("fov", 0), ("range", -2), ("heading", float("inf"))])
def test_out_of_range_sensor_values_are_refused(app, key, value):
    data = app.config.to_dict()
    data["sensors"][0][key] = value
    status, body = put(app, data)
    assert status == 400 and body["error"].startswith("Sensor ")


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
    assert app.config.params.range_sigma_base == 0.2
    data["params"]["light_cost"] = 3.0  # outputs only: the people stay
    data["params"]["target_threshold"] = 0.6
    data["zones"][0]["name"] = "Wohnzimmer"
    status, body = put(app, data)
    assert status == 200 and body["restarted"] is False
    assert app.config.params.light_cost == 3.0 and app.tracker.p.light_cost == 3.0


def test_the_stored_config_loads_as_it_is():
    d = flat_config().to_dict()
    d["params"]["light_cost"] = 0
    with pytest.raises(ValueError):
        check(d)
    assert Config.from_dict(d).params.light_cost == 0  # without check: as stored


def test_the_ui_gets_the_limits(app):
    async def run():
        async with TestClient(TestServer(app.web_app())) as client:
            return await (await client.get("/api/config")).json()
    limits = asyncio.run(run())["limits"]
    assert limits["params"]["light_cost"] == [0.0, 1000.0, True, False]
    assert limits["sensor"]["height"][:2] == [0.1, 4.0]


def _area(**kw):
    return {"id": "z", "name": "Z", "kind": "area", "shape": "rect", "points": [[1, 1], [2, 2]], **kw}


@pytest.mark.parametrize("change, message", [
    (lambda d: d["sensors"].append(dict(d["sensors"][0])), "doppelt"),
    (lambda d: d["sensors"][0].update(id="a/b"), "MQTT"),
    (lambda d: d["walls"].append({"points": [[1, 1]], "kind": "wall"}), "zwei verschiedene Punkte"),
    (lambda d: d["walls"].append({"points": [[1, 1], [2, 2], [3, 3]], "kind": "wall"}), "zwei verschiedene Punkte"),
    (lambda d: d["walls"].append({"points": [[1, 1], [1, 1]], "kind": "wall"}), "zwei verschiedene Punkte"),
    (lambda d: d["walls"].append({"points": [[1, 1], [2, 2]], "kind": "glass"}), "Art"),
    (lambda d: d["zones"].append(_area(kind="foo")), "Art"),
    (lambda d: d["zones"].append(_area(shape="blob")), "Form"),
    (lambda d: d["zones"].append(_area(shape="circle", points=[], radius=1.0)), "Mittelpunkt"),
    (lambda d: d["zones"].append(_area(shape="polygon", points=[[1, 1], [2, 2]])), "drei"),
    (lambda d: d["zones"].append(_area(points=[[1, 1]])), "zwei Ecken"),
    (lambda d: d["zones"].append(_area(id=None)), "ohne ID"),
    (lambda d: d["zones"].append(dict(d["zones"][0])), "doppelt"),
    (lambda d: d.update(ld_view={"a<-b": -0.5}), "Blick des LD2410C"),
])
def test_what_the_model_cannot_run_with_is_refused_with_400(app, tmp_path, change, message):
    """Until 0.18 a one-point wall gave a 500, and a circle without a center was saved: every tick
    after it failed, also after every restart."""
    before = (tmp_path / "tracker.json").read_text()
    data = app.config.to_dict()
    change(data)
    status, body = put(app, data)
    assert status == 400 and message in body["error"], body
    assert (tmp_path / "tracker.json").read_text() == before


def test_a_plan_drawn_in_meters_may_be_scaled_above_1(app):
    """An SVG drawn in meters (one unit a meter): "Ausrichten" gives a scale of about 1.02 m per unit,
    refused until 0.18 - and with it every later edit, until the image was removed. The background image is
    the web UI's (it checks the layers, static/js/util.js layersError): the server stores it as it comes."""
    data = app.config.to_dict()
    data["background"] = {"layers": [{"x": 0, "y": 0, "scale": 1.02, "rotation": 0, "opacity": 0.5}]}
    assert put(app, data)[0] == 200
    data["background"]["layers"][0]["scale"] = 11
    assert put(app, data)[0] == 200 and app.config.background["layers"][0]["scale"] == 11


def test_a_door_on_no_wall_is_refused(app):
    """The editor puts a door exactly on its wall; the model reads that wall and nothing else (one rule:
    the core's plan.wall_of_door)."""
    data = app.config.to_dict()
    data["doors"][0]["x"] += 0.05  # the balcony door on the wall x = 6
    status, body = put(app, data)
    assert status == 400 and "keiner Wand" in body["error"]
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
    assert status == 400 and "läuft mit dieser Konfiguration nicht" in body["error"] and calls
    assert app.config is old and app.tracker.config is old
    assert (tmp_path / "tracker.json").read_text() == before
    app.tracker.zone_states()  # the model runs


def test_every_field_of_the_ui_has_a_label():
    # the web UI's fields: the core's limits; each needs its words here (and nothing else is worded)
    import presence_core
    limits = presence_core.config_limits()
    assert {group: set(fields) for group, fields in limits.items()} == \
        {group: set(LABELS[group]) for group in limits}


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


@pytest.mark.parametrize("reason, config", [(r, c) for r, cs in _PROBLEMS.items() for c in cs])
def test_every_problem_of_the_core_is_a_german_sentence(reason, config):
    import presence_core
    with pytest.raises(presence_core.ConfigError) as e:
        presence_core.check_config(json.dumps(config))
    problem = e.value.args[0]
    assert problem.reason == reason
    with pytest.raises(ValueError) as words:
        check(config)
    text = str(words.value)
    assert text and text[0].isupper() and text.endswith((".", "?")) or "unvollständig" in text, text
    assert "None" not in text and "{" not in text, text


def test_an_unreadable_configuration_is_a_german_sentence():
    import presence_core
    from presence_tracker.model import message
    with pytest.raises(presence_core.ConfigError) as e:
        presence_core.check_config("{")
    assert e.value.args[0].reason == "unreadable"
    assert message(e.value.args[0]).startswith("Konfiguration unvollständig oder falsch: ")
