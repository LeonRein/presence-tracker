"""DEBUGGING-WERKZEUG (vorübergehend, zum späteren Entfernen, mit presence_tracker/debug_fenster.py): die
Fensterkontakte in der Aufzeichnung."""

import asyncio
import json

import aiohttp
from aiohttp import web

from presence_tracker import debug_fenster


def fake_ha(states: dict):
    async def all_states(request):
        assert request.headers["Authorization"] == "Bearer tok"
        return web.json_response([{"entity_id": e, "state": s, "last_changed": c} for e, (s, c) in states.items()])

    async def one(request):
        e = request.match_info["entity_id"]
        s, c = states[e]
        return web.json_response({"entity_id": e, "state": s, "last_changed": c})

    app = web.Application()
    app.router.add_get("/api/states", all_states)
    app.router.add_get("/api/states/{entity_id}", one)
    return app


def test_records_window_contacts_on_change_only():
    states = {"binary_sensor.kuche_fenster_contact": ("off", "a"),
              "binary_sensor.wohnzimmer_fenster_contact": ("off", "b"),
              "binary_sensor.kuche_fenster_battery_plus_low": ("off", "x"),  # kein Kontakt
              "light.kueche": ("on", "y")}
    lines = []

    async def run():
        runner = web.AppRunner(fake_ha(states))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        f = debug_fenster.Fenster(f"http://127.0.0.1:{port}", "tok", lambda t, topic, p: lines.append((topic, p)),
                                  lambda: True)
        async with aiohttp.ClientSession() as session:
            await f.poll(session)
            await f.poll(session)  # nichts geändert: keine Zeile
            states["binary_sensor.kuche_fenster_contact"] = ("on", "c")
            await f.poll(session)
        await runner.cleanup()

    asyncio.run(run())
    got = [(topic, json.loads(p)) for topic, p in lines]
    assert all(topic == debug_fenster.DEBUG_TOPIC for topic, _ in got)
    assert [(d["entity_id"], d["state"]) for _, d in got] == [
        ("binary_sensor.kuche_fenster_contact", "off"), ("binary_sensor.wohnzimmer_fenster_contact", "off"),
        ("binary_sensor.kuche_fenster_contact", "on")]


def test_topic_is_no_frame_and_no_switch():
    """Kein Leser der Aufnahmen nimmt die Zeilen als Frame (…/frame) oder als Schalter (pause.TOPICS)."""
    from presence_tracker import pause
    assert not debug_fenster.DEBUG_TOPIC.endswith("/frame")
    assert debug_fenster.DEBUG_TOPIC not in pause.TOPICS
    assert len(debug_fenster.DEBUG_TOPIC.split("/")) != 3  # App._on_message: nur <Präfix>/<Sensor>/<Art>
