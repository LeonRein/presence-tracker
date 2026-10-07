"""The app goes on whatever comes in: a browser that stops reading, broken MQTT payloads, broken files,
failing saves. Node-RED keeps a light's last state while the app is gone or frozen, so anything that
stops it or freezes its outputs leaves a light that is on burning."""

import asyncio
import base64
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
