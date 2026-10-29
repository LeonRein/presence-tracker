"""The editor's preview (app.App.h_preview, POST /api/preview): the core applies an edit of the plan editor
(snapping, what moves along) and derives what the web UI draws (walls with door openings, rooms, sight
areas, coverage); nothing is saved, the model is left alone. GET/PUT /api/config bring the derived view of
the saved configuration along; the live message the core's numbers (zones' thresholds, the total)."""

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from presence_tracker.app import App
from test_filter import flat_config


def preview(app, body):
    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            r = await client.post("/api/preview", json=body)
            return r.status, await r.json()
    return asyncio.run(main())


def an_app(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    return App(tmp_path, publish=False)


def test_a_wall_drawn_and_released_snaps_and_splits_the_room(tmp_path):
    app = an_app(tmp_path)
    saved = (tmp_path / "tracker.json").read_bytes()
    tracker, config = app.tracker, app.config.to_dict()
    # two clicks near the walls at the bottom and the top (10 px at 50 px/m: 0.2 m): the first onto the bottom
    # wall, the second straight up from it (45° steps) onto the top wall; the room splits
    status, r = preview(app, {"config": config, "edit": {"op": "add_walls", "points": [[3.04, 0.03], [2.97, 4.96]],
                                                         "kind": "wall", "tol": 0.2}})
    assert status == 200
    walls = [w["points"] for w in r["config"]["walls"]]
    assert walls[-1] == [[3.04, 0.0], [3.04, 5.0]] and len(walls) == len(config["walls"]) + 1
    rooms = [z for z in r["config"]["zones"] if z["kind"] == "room"]
    assert len(rooms) == 4 and all(z["id"] for z in rooms)  # wohn split in two (the new one has an id), balkon, flur
    d = r["derived"]
    assert d["lengths"][-1] == 5.0 and d["doors"]["balkon"] == [[6.0, 1.55], [6.0, 2.45]]
    assert sorted(d["plan"]["observed"]) == sorted(z["id"] for z in rooms if z["id"] not in ("balkon", "flur"))
    # nothing saved, the model as it was
    assert (tmp_path / "tracker.json").read_bytes() == saved
    assert app.tracker is tracker and app.config.to_dict() == config
    # while drawing: where the clicks snap to, nothing else
    status, r = preview(app, {"config": config, "edit": {"op": "snap_path", "points": [[3.04, 0.03], [3.5, 2.0]],
                                                         "closed": False, "tol": 0.2}})
    assert status == 200 and r["points"] == [[3.04, 0.0], [3.5, 2.0]] and r["config"] == config


def test_a_sensor_dragged_and_released_gets_its_sight_and_the_coverage(tmp_path):
    app = an_app(tmp_path)
    config = app.config.to_dict()
    status, r = preview(app, {"config": config, "coverage": True,
                              "edit": {"op": "move_sensor", "sensor": "a", "to": [0.1, 2.48], "tol": 0.2}})
    assert status == 200
    a = next(s for s in r["config"]["sensors"] if s["id"] == "a")
    assert (a["x"], a["y"]) == (0.0, 2.48)  # snapped onto the wall x = 0
    sight = r["derived"]["sensors"]["a"]
    assert len(sight) >= 3 and all(x <= 6.0 + 1e-9 for x, _ in sight)  # cut at the wall x = 6 (through the door to x < 0)
    cov = r["coverage"]
    assert cov["size"] > 0 and cov["tiles"] and {t[1] for t in cov["tiles"]} == {"wohn"}
    wohn = next(x for x in cov["rooms"] if x["id"] == "wohn")
    assert wohn["observed"] and wohn["area"] == 30.0 and wohn["blind"] + wohn["single"] <= 30.0
    assert next(x for x in cov["rooms"] if x["id"] == "balkon")["observed"] is False


def test_a_draft_or_an_edit_the_core_refuses_is_a_400_with_its_sentence(tmp_path):
    app = an_app(tmp_path)
    config = app.config.to_dict()
    bad = json.loads(json.dumps(config))
    bad["sensors"][0]["range"] = 50
    status, r = preview(app, {"config": bad})
    assert status == 400 and "Reichweite" in r["error"]
    status, r = preview(app, {"config": config, "edit": {"op": "add_door", "id": "d1", "at": [3.0, 2.5], "tol": 0.4}})
    assert status == 400 and r["code"] == "no_wall" and "Wand" in r["error"]
    # a rectangle too small to be meant: nothing to say
    status, r = preview(app, {"config": config, "edit": {"op": "add_zone", "tol": 0.2, "zone": {
        "id": "z1", "kind": "area", "shape": "rect", "points": [[1, 1], [1.05, 3]]}}})
    assert status == 400 and r["code"] == "too_small" and r["error"] is None


def test_the_saved_configuration_comes_with_its_derived_view_and_the_live_view_with_the_cores_numbers(tmp_path):
    app = an_app(tmp_path)
    for k, sid in enumerate(("a", "b")):
        frame = {"uptime_ms": 1000, "targets": [], "ld2410": {}}
        app.on_message(f"presence/{sid}/frame", json.dumps(frame).encode(), 1000.0 + k)
    app.tick(1001.0)

    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            got = await (await client.get("/api/config")).json()
            put = await (await client.put("/api/config", json=got["config"])).json()
            return got, put, json.loads(app.live_message())
    got, put, live = asyncio.run(main())
    assert got["derived"] == put["derived"] and got["derived"]["plan"]["observed"] == ["wohn"]
    assert abs(got["derived"]["thresholds"]["occupied"] - 2 / 3) < 1e-12  # light_cost 2
    assert live["zones"]["wohn"]["c_occupied"] == 0.667 and "c_occupied" not in live["zones"]["_total"]
    assert set(live["seen"]) == {"count", "moving", "still"}
