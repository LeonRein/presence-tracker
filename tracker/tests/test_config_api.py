"""The configuration over HTTP (app.App: GET/PUT /api/config, GET /api/coverage). The map comes finished from the
web UI's editor (rooms between the walls, doors on walls); the core checks what the model reads and answers with
the model's quantities of the saved configuration (derived: plan, thresholds, the numbers the editor reads; the
coverage and each sensor's sight per tile of the running world): no geometry for drawing, the web UI draws. The
background image is the web UI's: an edit of it alone is only stored, the core never sees it."""

import asyncio
import json

import presence_core

from presence_tracker.app import App
from presence_tracker.filter import Tracker
from aiohttp.test_utils import TestClient, TestServer
from test_filter import flat_config


def an_app(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    return App(tmp_path, publish=False)


def call(app, *requests):
    """(method, path, json) -> [(status, body)]"""
    async def main():
        out = []
        async with TestClient(TestServer(app.web_app())) as client:
            for method, path, body in requests:
                r = await client.request(method, path, json=body)
                out.append((r.status, await r.json()))
        return out
    return asyncio.run(main())


def test_the_saved_configuration_comes_with_the_model_of_it(tmp_path):
    app = an_app(tmp_path)

    async def main():
        for k, sid in enumerate(("a", "b")):
            frame = {"uptime_ms": 1000, "targets": [], "ld2410": {}}
            app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + k)
        app.tick(1001.0)
        live = json.loads(app.live_message())
        async with TestClient(TestServer(app.web_app())) as client:
            got = await (await client.get("/api/config")).json()
            edited = json.loads(json.dumps(got["config"]))
            edited["params"]["light_cost"] = 3.0
            put = await (await client.put("/api/config", json=edited)).json()
            again = await (await client.put("/api/config", json=edited)).json()
        return live, got, put, again
    live, got, put, again = asyncio.run(main())
    assert live["zones"]["wohn"]["c_occupied"] == 0.667 and "c_occupied" not in live["zones"]["_total"]
    assert set(live["seen"]) == {"count", "moving", "still"}
    assert put["derived"]["thresholds"]["occupied"] == 0.75
    assert {k: v for k, v in got["derived"].items() if k != "thresholds"} == \
        {k: v for k, v in put["derived"].items() if k != "thresholds"}
    # nothing of the model changed: nothing new to show
    assert again == {"ok": True, "restarted": False, "derived": None}
    d = got["derived"]
    assert set(d) == {"plan", "thresholds", "ld2410_gate"}  # the model's numbers; nothing to draw
    assert d["plan"]["observed"] == ["wohn"] and abs(d["thresholds"]["occupied"] - 2 / 3) < 1e-12  # light_cost 2
    assert d["plan"]["door_on_wall"] == 0.01 and d["ld2410_gate"] == 0.75
    # the people for the map: no trail breaks, no polygons; the rooms of their Gaussians
    assert all("jumped" not in p for p in live["tracks"]) and all(set(c) == {"id", "gauss", "rooms", "tiles"}
                                                                   for c in live["clouds"])


def test_the_rooms_are_taken_as_the_editor_sent_them(tmp_path):
    app = an_app(tmp_path)
    data = app.config.to_dict()
    data["walls"].append({"points": [[3.0, 0.0], [3.0, 5.0]], "kind": "wall"})  # splits wohn, as the editor's rooms don't
    [(status, put)] = call(app, ("PUT", "/api/config", data))
    assert status == 200 and app.config.to_dict()["zones"] == data["zones"]
    assert json.loads((tmp_path / "tracker.json").read_text())["zones"] == data["zones"]


def test_the_coverage_is_of_the_running_world(tmp_path):
    app = an_app(tmp_path)
    [(status, cov)] = call(app, ("GET", "/api/coverage", None))
    assert status == 200 and cov["size"] > 0 and {t[1] for t in cov["tiles"]} == {"wohn"}
    wohn = next(x for x in cov["rooms"] if x["id"] == "wohn")
    assert wohn["observed"] and wohn["area"] == 30.0 and wohn["blind"] + wohn["single"] <= 30.0
    assert next(x for x in cov["rooms"] if x["id"] == "balkon")["observed"] is False
    # each sensor in use: its sight per tile (aligned with the tiles), what the filter weighs with
    assert cov["sees"] == 0.5 and set(cov["sight"]) == {"a", "b"}
    assert all(len(v) == len(cov["tiles"]) and max(v) > 0.9 for v in cov["sight"].values())


def test_an_edit_of_the_background_image_alone_does_not_touch_the_core(tmp_path, monkeypatch):
    app = an_app(tmp_path)
    tracker, config = app.tracker, app.config.to_dict()

    def untouchable(*args, **kw):
        raise AssertionError("the core was asked")
    for name in ("check_config", "derive", "changes", "plan", "World"):
        monkeypatch.setattr(presence_core, name, untouchable)
    monkeypatch.setattr(Tracker, "reconfigure", untouchable)
    data = json.loads(json.dumps(config))
    # out of what the web UI allows (it checks the layers): stored as it comes
    data["background"] = {"layers": [{"id": "l1", "name": "Plan", "url": "images/x.png", "x": 1.5, "y": 2, "scale": 11,
                                      "rotation": 0, "opacity": 0.5, "visible": True}]}
    [(status, put)] = call(app, ("PUT", "/api/config", data))
    assert status == 200 and put["derived"] is None and put["restarted"] is False
    assert app.tracker is tracker and app.config.background == data["background"]
    assert json.loads((tmp_path / "tracker.json").read_text())["background"] == data["background"]
    assert {k: v for k, v in app.config.to_dict().items() if k != "background"} == \
        {k: v for k, v in config.items() if k != "background"}
