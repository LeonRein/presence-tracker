"""The app goes on whatever comes in: a browser that stops reading, broken MQTT payloads, broken files,
failing saves. Node-RED keeps a light's last state while the app is gone or frozen, so anything that
stops it or freezes its outputs leaves a light that is on burning."""

import asyncio
import base64
import json
import os
import socket
import time

import aiohttp
from aiohttp import web

from presence_tracker import app as app_module
from presence_tracker.app import App
from test_filter import flat_config


def test_a_browser_that_stops_reading_holds_up_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "SEND_TIMEOUT", 0.5)
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)

    async def main():
        runner = web.AppRunner(app.web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        # a client that does the handshake and then never reads (a phone gone to sleep, a stalled proxy)
        stuck = socket.socket()
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode()
        stuck.sendall(f"GET /api/live HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        received = []
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://127.0.0.1:{port}/api/live") as good:
                async def read():
                    async for msg in good:
                        received.append(msg.data)
                reader = asyncio.create_task(read())
                for _ in range(100):
                    if len(app.ws_clients) == 2:
                        break
                    await asyncio.sleep(0.02)
                assert len(app.ws_clients) == 2
                slowest = 0.0
                for i in range(300):  # 12 MB: far more than the socket buffers hold
                    t0 = time.monotonic()
                    app._broadcast(f"{i:04d}" + "x" * 40_000)
                    slowest = max(slowest, time.monotonic() - t0)
                    await asyncio.sleep(0.01)
                assert slowest < 0.05, slowest  # the housekeeping loop never waits for a browser
                for _ in range(200):
                    if len(app.ws_clients) == 1:
                        break
                    await asyncio.sleep(0.02)
                assert len(app.ws_clients) == 1  # the stuck one is cut off, the other one stays
                app._broadcast("last")
                for _ in range(100):
                    if received and received[-1] == "last":
                        break
                    await asyncio.sleep(0.02)
                assert received[-1] == "last"
                reader.cancel()
        stuck.close()
        await runner.cleanup()

    asyncio.run(main())


BAD_FRAMES = {  # valid JSON, but no frame (BUGS 3): each of these stopped the app until 0.18
    "null": b"null",
    "list": b"[]",
    "number": b"5",
    "uptime_ms a string": b'{"uptime_ms": "x", "targets": [], "ld2410": {}}',
    "a target without y": b'{"uptime_ms": 1100, "targets": [{"x": 100, "slot": 1}], "ld2410": {}}',
    "x null": b'{"uptime_ms": 1200, "targets": [{"x": null, "y": 1000, "slot": 1}], "ld2410": {}}',
    "targets null": b'{"uptime_ms": 1300, "targets": null, "ld2410": {}}',
    # caught before, but by a restart of the model (seconds of CPU each)
    "x NaN": b'{"uptime_ms": 1400, "targets": [{"x": NaN, "y": 1000, "slot": 1}]}',
    "ld2410 a list": b'{"uptime_ms": 1500, "targets": [], "ld2410": []}',
    "a gate a string": b'{"uptime_ms": 1600, "targets": [], "ld2410": {"move_gates": ["a"], "still_gates": []}}',
    "a target a list": b'{"uptime_ms": 1700, "targets": [[1]]}',
    "no JSON": b"{",
}


def test_no_payload_stops_the_app_or_restarts_the_model(tmp_path, caplog):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    tracker = app.tracker
    good = {"uptime_ms": 1000, "targets": [], "ld2410": {}}
    app.on_message("presence/a/frame", json.dumps(good).encode(), 1000.0)
    for name, payload in BAD_FRAMES.items():
        app.on_message("presence/a/frame", payload, 1001.0)  # must not raise
        assert app.tracker is tracker, name  # dropped, not a model failure
    assert app.tracker.runtime["a"].frames == 1
    assert sum("dropped" in r.message for r in caplog.records) == 1  # once a minute per sensor
    app.on_message("presence/a/frame", json.dumps({**good, "uptime_ms": 2000}).encode(), 1001.0)
    assert app.tracker.runtime["a"].frames == 2 and app.tracker is tracker
    assert [json.loads(p)["uptime_ms"] for _, topic, p in app.recent] == [1000, 2000]  # not in a report


def test_the_mqtt_loop_survives_whatever_a_message_does(monkeypatch):
    """Even if handling a message failed, the MQTT connection goes on."""
    import aiomqtt

    from presence_tracker import sources

    class Msg:
        def __init__(self, n):
            self.topic, self.payload = "presence/a/frame", str(n).encode()

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, topic):
            pass

        @property
        def messages(self):
            async def gen():
                for n in range(3):
                    yield Msg(n)
                raise ValueError("anything")
            return gen()

    monkeypatch.setattr(aiomqtt, "Client", Client)
    seen, clients = [], []

    def on_message(topic, payload, recv):
        seen.append(payload)
        raise RuntimeError("bad")

    async def on_client(c):
        clients.append(c)

    async def main():
        task = asyncio.create_task(sources.mqtt_source({"host": "x"}, "presence", on_message, on_client))
        await asyncio.sleep(0.1)
        assert not task.done()  # reconnecting
        task.cancel()

    asyncio.run(main())
    assert seen == [b"0", b"1", b"2"] and clients[-1] is None  # all messages, then reconnecting
