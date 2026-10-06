"""The running app: frames in, tracker, Home Assistant entities and the web UI out."""

import asyncio
import base64
import faulthandler
import json
import logging
import math
import pathlib
import re
import signal
import time
import uuid
from collections import defaultdict

import aiohttp
from aiohttp import web

from . import ha
from .calibration import Calibrator
from .model import Config
from .filter import Tracker
from .ghostmap import GhostMap
from .sources import Clock, ReplayClock, mqtt_source, replay_source
from .frames import SensorClock

log = logging.getLogger(__name__)
STATIC = (pathlib.Path(__file__).parent / "static").resolve()
BLOCKED_DUMP = 15.0  # s the event loop may be busy before the watchdog logs where it is
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}


class App:
    def __init__(self, data_dir: pathlib.Path, prefix: str = "presence", publish: bool = True,
                 replay: list | None = None, speed: float = 1.0, mqtt: dict | None = None,
                 ha_url: str | None = None, ha_token: str | None = None):
        self.data_dir = data_dir
        self.images = data_dir / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.config_path = data_dir / "tracker.json"
        self.config = Config.load(self.config_path)
        self.prefix = prefix
        self.publish_enabled = publish
        self.replay = replay
        self.clock = ReplayClock(speed) if replay else Clock()
        self.mqtt_settings = mqtt
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.client = None
        self.discovery = ha.Discovery(self._publish)
        self.sensor_status: dict[str, str] = {}
        self.ws_clients: set = set()
        self.zone_states: dict = {}
        self._reset_tracker()
        self.stats = {"frames": 0, "cpu": 0.0, "since": time.monotonic()}

    def _reset_tracker(self):
        self.tracker = Tracker(self.config)
        # where the sensors start ghost tracks (learned online, MODEL.md 4.2): it holds only while the
        # sensors are where they were when it was learned, else it starts over
        gm_path = self.data_dir / "ghostmap.json"
        if gm_path.exists():
            try:
                if not self.tracker.use_ghost_map(GhostMap.load(gm_path)):
                    log.info("ghost map learned with other sensors or poses: starting a new one")
            except (ValueError, KeyError):
                log.warning("ghostmap.json unreadable, starting without a ghost map")
        self.last_model_save = time.monotonic()
        self.clocks = defaultdict(SensorClock)
        self.last_frame_t = -math.inf
        self.calibrator = getattr(self, "calibrator", None) or Calibrator(self.config)
        self.calibrator.config = self.config
        self.tracker.listeners.append(self._on_tracker_event)

    def _on_tracker_event(self, event, data):
        if event == "frame":
            self.calibrator.on_frame(*data)

    # ------------------------------------------------------------------ input

    def on_message(self, topic: str, payload: bytes, recv: float):
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != self.prefix:
            return
        sensor_id, kind = parts[1], parts[2]
        if kind == "status":
            self.sensor_status[sensor_id] = payload.decode(errors="replace")
            return
        if kind != "frame":
            return
        try:
            frame = json.loads(payload)
        except ValueError:
            return
        start = time.process_time()
        t = self.clocks[sensor_id](recv, frame.get("uptime_ms"))
        if self.replay and t < self.last_frame_t - 10:
            log.info("replay restarted, resetting tracker")
            self._reset_tracker()
            t = self.clocks[sensor_id](recv, frame.get("uptime_ms"))
        self.tracker.process_frame(sensor_id, t, frame)
        self.last_frame_t = t
        self.stats["frames"] += 1
        self.stats["cpu"] += time.process_time() - start

    async def _on_client(self, client):
        self.client = client
        if client is not None and self.publish_enabled:
            await self._publish(ha.AVAILABILITY, "online", True)
            self.discovery.published.clear()
            await self.discovery.sync(self.config.zones)

    async def _publish(self, topic: str, payload: str, retain: bool = False):
        if self.client is None or not self.publish_enabled:
            return
        try:
            await self.client.publish(topic, payload, qos=1 if retain else 0, retain=retain)
        except Exception as e:  # noqa: BLE001 - a lost connection is handled by the source loop
            log.debug("publish failed: %s", e)

    # ------------------------------------------------------------ main loops

    async def housekeeping(self):
        last_push = 0.0
        while True:
            # watchdog: if nothing comes back here for BLOCKED_DUMP s, the stack of every thread goes
            # to the log - where the app hangs (each call replaces the previous timer)
            faulthandler.dump_traceback_later(BLOCKED_DUMP)
            await asyncio.sleep(0.1)
            if self.tracker.start is None:
                continue
            start = time.process_time()
            self.tracker.step(self.clock())
            self.zone_states = self.tracker.zone_states()
            if not self.replay and time.monotonic() - self.last_model_save > 600:
                self.last_model_save = time.monotonic()
                self.tracker.ghost_map.save(self.data_dir / "ghostmap.json")
            self.stats["cpu"] += time.process_time() - start
            await self.discovery.states(self.zone_states)
            now = time.monotonic()
            if self.ws_clients and now - last_push >= 0.12:
                last_push = now
                await self._broadcast(self.live_message())

    def live_message(self) -> str:
        snap = self.tracker.snapshot()
        snap["zones"] = {k: v.to_dict() for k, v in self.zone_states.items()}
        snap["status"] = self.sensor_status
        snap["calibration"] = self.calibrator.status() if self.calibrator.active else None
        elapsed = time.monotonic() - self.stats["since"]
        snap["load"] = {"cpu": round(100 * self.stats["cpu"] / max(elapsed, 1e-3), 2),
                        "fps": round(self.stats["frames"] / max(elapsed, 1e-3), 1)}
        if elapsed > 30:
            self.stats = {"frames": 0, "cpu": 0.0, "since": time.monotonic()}
        return json.dumps({"type": "live", **snap})

    async def _broadcast(self, text: str):
        dead = []
        for ws in self.ws_clients:
            try:
                await ws.send_str(text)
            except (ConnectionError, RuntimeError):
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    async def run(self, host: str, port: int):
        runner = web.AppRunner(self.web_app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        log.info("web UI on http://%s:%d", host, port)
        tasks = [self.housekeeping()]
        if self.replay:
            tasks.append(replay_source(self.replay, self.clock, self.on_message))
        elif self.mqtt_settings:
            tasks.append(mqtt_source(self.mqtt_settings, self.prefix, self.on_message, self._on_client))
        else:
            log.warning("no MQTT broker and no replay: nothing to track")
        main = asyncio.gather(*tasks)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, main.cancel)
        try:
            await main
        except asyncio.CancelledError:
            log.info("stopping")
        finally:
            if self.client is not None:
                await self._publish(ha.AVAILABILITY, "offline", True)
            if not self.replay:
                self.tracker.ghost_map.save(self.data_dir / "ghostmap.json")
            await runner.cleanup()

    # ------------------------------------------------------------------- web

    def web_app(self) -> web.Application:
        app = web.Application(client_max_size=20 * 1024 * 1024)
        app.router.add_get("/", self.h_index)
        app.router.add_get("/static/{stamp}/{path:.+}", self.h_static)
        app.router.add_get("/images/{name}", self.h_image)
        app.router.add_get("/api/config", self.h_get_config)
        app.router.add_put("/api/config", self.h_put_config)
        app.router.add_get("/api/live", self.h_live)
        app.router.add_post("/api/background", self.h_upload)
        app.router.add_post("/api/calibration/{action}", self.h_calibration)
        app.router.add_get("/api/ha/cameras", self.h_cameras)
        app.router.add_get("/api/ha/map", self.h_map)
        app.router.add_post("/api/tracks/reset", self.h_reset_tracks)
        app.router.add_get("/api/sensormodel", self.h_sensormodel)
        app.router.add_post("/api/learned", self.h_import_learned)
        return app

    async def h_index(self, request):
        html = (STATIC / "index.html").read_text()
        # all static files live under a path that changes with any of them, so the browser never
        # mixes modules of two versions and may cache them for good
        stamp = str(int(max(p.stat().st_mtime for p in STATIC.rglob("*") if p.is_file())))
        return web.Response(text=html.replace("{{v}}", stamp), content_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    async def h_static(self, request):
        path = (STATIC / request.match_info["path"]).resolve()
        if STATIC not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "max-age=31536000, immutable"})

    async def h_image(self, request):
        name = request.match_info["name"]
        path = self.images / name
        if "/" in name or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "max-age=3600"})

    async def h_get_config(self, request):
        known = sorted(set(self.tracker.runtime) | set(self.config.sensor_by_id))
        return web.json_response({"config": self.config.to_dict(), "sensors_seen": known,
                                  "status": self.sensor_status, "replay": bool(self.replay),
                                  "ha": bool(self.ha_url)})

    async def h_put_config(self, request):
        data = await request.json()
        try:
            config = Config.from_dict(data)
        except (TypeError, ValueError, KeyError) as e:
            return web.json_response({"error": str(e)}, status=400)
        self.config = config
        self.tracker.reconfigure(config)  # a moved sensor: the ghost map starts over (MODEL.md 4.2)
        self.calibrator.config = config
        config.save(self.config_path)
        if self.client is not None:
            await self.discovery.sync(config.zones)
        # rooms are derived from the walls here; the editor takes them over
        return web.json_response({"ok": True, "rooms": [z.to_dict() for z in config.zones_of("room")]})

    async def h_live(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.ws_clients.add(ws)
        try:
            await ws.send_str(self.live_message())
            async for _ in ws:
                pass
        finally:
            self.ws_clients.discard(ws)
        return ws

    async def h_upload(self, request):
        """Image upload: multipart (web UI), or JSON {"filename", "data": base64} or {"url"} (scripts)."""
        if request.content_type == "application/json":
            body = await request.json()
            if body.get("url"):
                url = body["url"]
                if not url.startswith("https://"):
                    return web.json_response({"error": "Nur https-URLs."}, status=400)
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                        if r.status != 200:
                            return web.json_response({"error": f"HTTP {r.status}"}, status=502)
                        data = await r.content.read(20 * 1024 * 1024)
                filename = url.rsplit("/", 1)[-1].split("?")[0]
            else:
                filename, data = body.get("filename", ""), base64.b64decode(body.get("data", ""))
        else:
            field = await (await request.multipart()).next()
            filename, data = field.filename or "", await field.read()
        suffix = pathlib.Path(filename).suffix.lower()
        if suffix not in IMAGE_TYPES:
            return web.json_response({"error": "Nur PNG, JPEG, WebP oder SVG."}, status=400)
        name = f"{uuid.uuid4().hex[:12]}{suffix}"
        (self.images / name).write_bytes(data)
        return web.json_response({"name": name, "url": f"images/{name}"})

    async def h_calibration(self, request):
        action = request.match_info["action"]
        if action == "start":
            self.calibrator.start()
        elif action == "stop":
            self.calibrator.stop()
        elif action == "solve":
            return web.json_response(self.calibrator.solve())
        else:
            raise web.HTTPNotFound()
        return web.json_response(self.calibrator.status())

    async def h_sensormodel(self, request):
        """What one sensor sees (from the geometry) and where it starts ghost tracks (learned)."""
        return web.json_response({"maps": self.tracker.sensor_model.maps(request.query.get("sensor", ""),
                                                                         self.tracker.ghost_map)})

    async def h_import_learned(self, request):
        """Take over a ghost map learned elsewhere, e.g. offline from recordings: {"ghost_map":
        <tools/ghostmap.py output>}. Replaces the current one."""
        data = await request.json()
        if "ghost_map" in data:
            gm = GhostMap.from_dict(data["ghost_map"])
            gm.save(self.data_dir / "ghostmap.json")
            used = self.tracker.use_ghost_map(gm)
            log.info("ghost map imported, %s", "in use" if used else "not used: learned with other sensor poses")
        return web.json_response({"ghost_map": self.tracker.ghost_map is not None})

    async def h_reset_tracks(self, request):
        self.tracker.reset_people()  # nothing known about where anybody is: the data decides again
        return web.json_response({"ok": True})

    # -------------------------------------------------- Home Assistant (Dobby)

    async def _ha_get(self, path: str, binary: bool = False):
        if not self.ha_url:
            raise web.HTTPServiceUnavailable(text="Keine Verbindung zu Home Assistant konfiguriert.")
        headers = {"Authorization": f"Bearer {self.ha_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(self.ha_url + path, headers=headers) as r:
                if r.status != 200:
                    raise web.HTTPBadGateway(text=f"Home Assistant: HTTP {r.status}")
                return (await r.read(), r.content_type) if binary else await r.json()

    async def h_cameras(self, request):
        states = await self._ha_get("/api/states")
        cams = [{"entity_id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"])}
                for s in states
                if s["entity_id"].startswith("camera.") and isinstance(s["attributes"].get("rooms"), dict)]
        return web.json_response(cams)

    async def h_map(self, request):
        """Vacuum map: rooms in meters, and the map image saved as a background."""
        entity = request.query.get("entity", "")
        if not re.fullmatch(r"camera\.[a-z0-9_]+", entity):
            raise web.HTTPBadRequest(text="entity")
        state = await self._ha_get(f"/api/states/{entity}")
        attrs = state["attributes"]
        rooms = []
        for room in (attrs.get("rooms") or {}).values():
            rooms.append({"name": room.get("name", "Raum"),
                          "x0": room["x0"] / 1000, "y0": room["y0"] / 1000,
                          "x1": room["x1"] / 1000, "y1": room["y1"] / 1000})
        image, ctype = await self._ha_get(f"/api/camera_proxy/{entity}", binary=True)
        suffix = {"image/png": ".png", "image/jpeg": ".jpg"}.get(ctype, ".png")
        name = f"{entity.split('.')[1]}{suffix}"
        (self.images / name).write_bytes(image)
        return web.json_response({"rooms": rooms, "image": f"images/{name}",
                                  "charger": attrs.get("charger_position"),
                                  "calibration_points": attrs.get("calibration_points")})
