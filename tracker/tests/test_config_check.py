"""PUT /api/config refuses values the model can't run with: an emptied field in the web UI arrived as
0, and light_cost 0 put every room above its threshold (every light on)."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from presence_tracker.app import App
from presence_tracker.model import PARAM_LIMITS, Config, check_config
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
    assert PARAM_LIMITS[key][0] in body["error"]
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
    data["params"]["light_cost"] = 3.0
    status, body = put(app, data)
    assert status == 200 and body["restarted"] is True
    assert app.config.params.light_cost == 3.0
    data["params"]["target_threshold"] = 0.6  # an output only: the people stay
    status, body = put(app, data)
    assert status == 200 and body["restarted"] is False


def test_the_stored_config_loads_as_it_is():
    d = flat_config().to_dict()
    d["params"]["light_cost"] = 0
    with pytest.raises(ValueError):
        check_config(d)
    assert Config.from_dict(d).params.light_cost == 0  # without check: as stored


def test_the_ui_gets_the_limits(app):
    async def run():
        async with TestClient(TestServer(app.web_app())) as client:
            return await (await client.get("/api/config")).json()
    limits = asyncio.run(run())["limits"]
    assert limits["params"]["light_cost"] == [0.0, 1000.0, True, False]
    assert limits["sensor"]["height"][:2] == [0.1, 4.0]
