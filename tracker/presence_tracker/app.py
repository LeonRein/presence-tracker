"""The running app: frames in, tracker, Home Assistant entities and the web UI out."""

import asyncio
import base64
import collections
import faulthandler
import gzip
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

from . import __version__, code_hash, ha
from . import calibration
from .calibration import Calibrator
from .model import Config, check_config, limits_dict
from .filter import Tracker
from .destination import DestinationMap
from .ghostmap import GhostMap
from .sources import Clock, ReplayClock, mqtt_source, replay_source
from .frames import SensorClock, parse_frame
from .sensortracks import SensorTracks
from .util import Throttled, atomic_write

log = logging.getLogger(__name__)
STATIC = (pathlib.Path(__file__).parent / "static").resolve()
BLOCKED_DUMP = 15.0  # s the event loop may be busy before the watchdog logs where it is
REPORT_WINDOW = 15 * 60.0  # s of sensor data kept for an error report
CODE = code_hash()
REPORT_KINDS = {  # new kinds only add keys: old reports keep theirs
    "light_on": "Licht fälschlich an (niemand da)",
    "light_off": "Licht fälschlich aus (jemand da)",
    "light_late": "Licht kam zu spät",
    "lost": "Person verloren",
    "ghost": "Geist (Person, wo niemand ist)",
    "wrong_place": "Person am falschen Ort",
    "inaccurate": "Ungenaues Tracking",
    "latency": "Hohe Latenz",
    "other": "Sonstiges",
}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}
SEND_TIMEOUT = 5.0  # s a browser may take for one live message; a slower one is cut off
DOWN_AFTER = 3  # model failures without RETRY s of running between: the model is down
RESET_HOLD = 300.0  # s of each sensor's frames the LD2410C background does not learn from after a reset
RETRY = 60.0  # s: while down, the model is rebuilt and tried again this often (a rebuild is seconds of CPU)


class LiveClient:
    """One browser on /api/live. Only the newest message waits for it (a queue of one, an older one
    is replaced), sent by its own task: a browser that stops reading (a phone gone to sleep, a stalled
    proxy) fills the send buffer, and until 0.18 the loop that steps the model and publishes the rooms to
    Home Assistant waited there - the entities stayed "online" with the last state, a light on stayed on.
    One that takes longer than SEND_TIMEOUT for a message is cut off (it reconnects when it wakes up)."""

    def __init__(self, ws, transport):
        self.ws = ws
        self.transport = transport
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    def offer(self, text: str):
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(text)

    async def run(self):
        try:
            while True:
                text = await self.queue.get()
                await asyncio.wait_for(self.ws.send_str(text), SEND_TIMEOUT)
        except asyncio.TimeoutError:
            log.info("live view: a browser takes no messages, cut off")
        except (ConnectionError, RuntimeError):
            pass
        if self.transport is not None:
            self.transport.abort()  # a write cut off in the middle leaves the stream unusable


class App:
    def __init__(self, data_dir: pathlib.Path, prefix: str = "presence", publish: bool = True,
                 replay: list | None = None, speed: float = 1.0, mqtt: dict | None = None,
                 ha_url: str | None = None, ha_token: str | None = None):
        self.data_dir = data_dir
        self.images = data_dir / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.config_path = data_dir / "tracker.json"
        self.config = Config.load(self.config_path)
        if self.config_path.exists():  # stored, it loads as it is: say loudly if the model may fail with it
            try:
                check_config(json.loads(self.config_path.read_text()))
            except ValueError as e:
                log.error("tracker.json does not pass the check: %s - the model may fail with it", e)
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
        self.reports = data_dir / "reports"
        self.recent = collections.deque()  # (receive time, topic, payload) of the last REPORT_WINDOW s
        self.shown = collections.deque()  # (model time, what the app showed) per second, as long back
        self.throttled = Throttled(log)  # log lines about bad input: at most one a minute per kind and sensor
        self._saving = None  # the task saving what was learned (every 10 min)
        self.failures: list[float] = []  # when the model failed (monotonic), until it ran RETRY s without
        self.paused_until = -math.inf  # while down: the model rests until then (monotonic)
        self.available = None  # what this connection last said on the availability topic (None: nothing yet)
        self._reset_tracker()
        self.stats = {"frames": 0, "cpu": 0.0, "since": time.monotonic()}

    def _model_failed(self, where: str, count: bool = True):
        """An error in the model must not stop the app: 0.9.3 died on one LD2410C frame (7.10.,
        between 22:52 and 2:07), the MQTT client said goodbye cleanly, so its last will never went
        out, and Home Assistant kept the last states for hours. Logged with the traceback; the model
        starts over from what was learned and saved, about the people from what it knew just before
        (if that is still sound, else from the last save)."""
        log.exception("model failed on %s: starting over", where)
        now = time.monotonic()
        if count:  # (not a configuration refused in PUT /api/config: the old one holds)
            self.failures.append(now)
        if count and self.down:
            # a failure that comes back after every rebuild (a configuration the model can't run with,
            # a learned file that breaks it): until 0.18 the model was rebuilt at every frame (seconds of
            # CPU each, the frames piling up) and Home Assistant kept the states from before, "online"
            if len(self.failures) == DOWN_AFTER:
                log.error("THE MODEL FAILS AGAIN AND AGAIN (%d times, last on %s): Home Assistant gets "
                          "'unavailable' until it has run %.0f s without failing, tried again every %.0f s. "
                          "Check the configuration and the errors above.", len(self.failures), where, RETRY, RETRY)
            self.paused_until = now + RETRY
        try:
            if not self.replay:
                self._save_people()
        except Exception:  # noqa: BLE001 - the failed model may be broken: then the last save holds
            log.warning("the people could not be saved, starting from the last save")
        self._reset_tracker()

    @property
    def down(self) -> bool:
        """The model failed DOWN_AFTER times without running RETRY s between: it does not run (Home
        Assistant: unavailable), also when a frame of one sensor breaks it every time while the ticks
        between run."""
        return len(self.failures) >= DOWN_AFTER

    def _model_ran(self):
        """A good tick: once the model has run RETRY s since it last failed, it runs (again)."""
        if self.failures and time.monotonic() - self.failures[-1] >= RETRY:
            if self.down:
                log.warning("the model runs again (%.0f s without failing, after %d failures)", RETRY, len(self.failures))
            self.failures.clear()

    def _reset_tracker(self):
        """A new model with what was learned and saved. A file that can't be read (empty after a
        power cut, broken, of another type) is logged and that part starts with nothing learned: until
        0.18 an empty calibration.npz, people.json [1] or ghostmap.json [] kept the app from starting
        at all."""
        self.tracker = Tracker(self.config)
        # where the sensors start ghost tracks (learned online, MODEL.md 4.2): it holds only while the
        # sensors are where they were when it was learned, else it starts over
        gm_path = self.data_dir / "ghostmap.json"
        if gm_path.exists():
            try:
                if not self.tracker.use_ghost_map(GhostMap.load(gm_path)):
                    log.info("ghost map learned with other sensors or poses: starting a new one")
            except Exception as e:  # noqa: BLE001 - the map from the prior (Tracker) holds
                log.warning("ghostmap.json unreadable (%r), starting a new ghost map", e)
                self.tracker.use_ghost_map(None)
        # what each LD2410C sees without anybody (learned online, MODEL.md 4.3), per sensor only
        # while it is where it was
        ld_path = self.data_dir / "ld2410.json"
        if ld_path.exists():
            try:
                self.tracker.ld_background.load(ld_path)
            except Exception as e:  # noqa: BLE001
                log.warning("ld2410.json unreadable (%r), starting from the prior", e)
                self.tracker.ld_background.load_dict({})
            self.tracker.ld_background.use(self.config)
        # where walks go (learned online, MODEL.md 6 "Ziel"): it holds while the rooms and doors are the
        # same, else it starts empty
        dest_path = self.data_dir / "destinations.json"
        if dest_path.exists():
            try:
                if not self.tracker.use_dest_map(DestinationMap.from_dict(json.loads(dest_path.read_text()))):
                    log.info("destination map learned with other rooms or doors: starting a new one")
            except Exception as e:  # noqa: BLE001
                log.warning("destinations.json unreadable (%r), starting an empty destination map", e)
                self.tracker.use_dest_map(None)
        # what was known about the people when the app stopped (MODEL.md 5.3), moved on over the
        # time it did not run; without it (first start, another floor plan) nothing is known
        people_path = self.data_dir / "people.json"
        if not self.replay and people_path.exists():
            try:
                state = json.loads(people_path.read_text())
                if not self.tracker.restore_people(state if isinstance(state, dict) else None):
                    log.info("people.json unreadable or made for another floor plan: nothing known about the people")
            except Exception as e:  # noqa: BLE001
                log.warning("people.json unreadable (%r): nothing known about the people", e)
                self.tracker.reset_people()
        self._model_started(None)  # at its first frame
        self.last_model_save = time.monotonic()
        self.clocks = defaultdict(SensorClock)
        self.last_frame_t = -math.inf
        if getattr(self, "calibrator", None) is None:
            self.calibrator = Calibrator(self.config)
            cal_path = self.data_dir / "calibration.npz"
            if not self.replay and cal_path.exists():
                try:
                    self.calibrator.load(cal_path)
                except Exception as e:  # noqa: BLE001
                    log.warning("calibration.npz unreadable (%r), collecting anew", e)
                    self.calibrator.reset()
        self.calibrator.config = self.config

    def _model_started(self, t: float | None):
        """The model starts (app start, model failure, reset, new floor plan): when (None: at its
        first frame), what it had learned then and the people's state it started from, for an error
        report. Replayed from
        there with what was learned at the report instead, the replay was up to 0.12 off from what the
        app believed (6.10., 21:22); with this copy it is the same."""
        self.model_start = t
        self.start_learned = json.loads(json.dumps(self.tracker.learned()))
        self.start_state = self.tracker.started_from  # the saved state it started from (None: nothing known)

    # ------------------------------------------------------------------ input

    def on_message(self, topic: str, payload: bytes, recv: float):
        """One MQTT message. Never raises: whatever comes in must not stop the app (Node-RED keeps a
        light's last state while it is gone)."""
        try:
            self._on_message(topic, payload, recv)
        except Exception:  # noqa: BLE001 - logged; the next message is handled as always
            self.throttled(("message", topic), logging.ERROR, "message on %s not handled", topic, exc_info=True)

    def _on_message(self, topic: str, payload: bytes, recv: float):
        if self.discovery.retained(topic, payload):  # our own entities as the broker keeps them
            return
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != self.prefix:
            return
        sensor_id, kind = parts[1], parts[2]
        if kind == "frame":
            try:
                frame = parse_frame(payload)
            except ValueError as e:  # dropped like a frame lost on the way (and not in a report)
                self.throttled(("frame", sensor_id), logging.WARNING, "frame of %s dropped: %s (%r)",
                               sensor_id, e, bytes(payload[:200]))
                return
        self.recent.append((recv, topic, payload))
        while self.recent and self.recent[0][0] < recv - REPORT_WINDOW:
            self.recent.popleft()
        if kind == "status":
            self.sensor_status[sensor_id] = payload.decode(errors="replace")
            return
        if kind != "frame":
            return
        start = time.process_time()
        t = self.clocks[sensor_id](recv, frame.get("uptime_ms"))
        if self.replay and t < self.last_frame_t - 10:
            log.info("replay restarted, resetting tracker")
            self._reset_tracker()
            self.calibrator.reset()
            t = self.clocks[sensor_id](recv, frame.get("uptime_ms"))
        try:
            self.calibrator.on_frame(sensor_id, t, frame)
        except Exception:  # noqa: BLE001 - the calibration's data, not the model: logged only
            self.throttled(("calibration", sensor_id), logging.ERROR, "calibration: frame of %s not taken",
                           sensor_id, exc_info=True)
        if time.monotonic() < self.paused_until:  # down: the model rests until it is tried again
            pass
        else:
            try:
                self.tracker.process_frame(sensor_id, t, frame)
            except Exception:  # noqa: BLE001 - see _model_failed
                self._model_failed(f"frame of {sensor_id}")
        self.last_frame_t = t
        self.stats["frames"] += 1
        self.stats["cpu"] += time.process_time() - start

    async def _on_client(self, client):
        self.client = client
        self.available = None
        self.discovery.last_state.clear()  # every state goes out again with the next tick
        if client is not None and self.publish_enabled:
            # "online" only once fresh states are out (housekeeping): the broker may hold "online" and the
            # states of a run that ended without its last will, and Home Assistant would show them as now
            await self._set_available(False)
            self.discovery.published.clear()
            await self.discovery.sync(self.config.zones)
            # what the broker retains of earlier runs: entities no longer wanted are removed (ha.py)
            try:
                await client.subscribe(ha.DISCOVERY)
            except Exception as e:  # noqa: BLE001 - a lost connection is handled by the source loop
                log.debug("subscribe failed: %s", e)

    async def _publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        if self.client is None or not self.publish_enabled:
            return False
        try:
            await self.client.publish(topic, payload, qos=1 if retain else 0, retain=retain)
        except Exception as e:  # noqa: BLE001 - a lost connection is handled by the source loop
            log.debug("publish failed: %s", e)
            return False
        return True

    async def _set_available(self, on: bool):
        """The entities' availability (ha.AVAILABILITY), sent when it changes. Node-RED keeps a light's
        last state while they are unavailable (its choice); "online" means: these states are fresh."""
        state = "online" if on else "offline"
        if self.client is not None and self.available != state and await self._publish(ha.AVAILABILITY, state, True):
            self.available = state

    # ------------------------------------------------------------ main loops

    async def housekeeping(self):
        last_push = 0.0
        while True:
            # watchdog: if nothing comes back here for BLOCKED_DUMP s, the stack of every thread goes
            # to the log - where the app hangs (each call replaces the previous timer)
            faulthandler.dump_traceback_later(BLOCKED_DUMP)
            await asyncio.sleep(0.1)
            if self.down:
                await self._set_available(False)
            if self.tracker.start is None or time.monotonic() < self.paused_until:
                continue
            start = time.process_time()
            if not self.tick(self.clock()):
                continue
            self._model_ran()
            if (not self.replay and time.monotonic() - self.last_model_save > 600
                    and (self._saving is None or self._saving.done())):
                self.last_model_save = time.monotonic()
                self._saving = asyncio.create_task(self._save_in_background())
            self.stats["cpu"] += time.process_time() - start
            try:
                await self.discovery.states(self.zone_states, t=self.tracker.now)
                if not self.down:
                    await self._set_available(True)  # after the first fresh states of this connection
            except Exception:  # noqa: BLE001 - the loop must go on (it is all of the app's output)
                self.throttled("states", logging.ERROR, "states not published", exc_info=True)
            now = time.monotonic()
            if self.ws_clients and now - last_push >= 0.12:
                last_push = now
                try:
                    self._broadcast(self.live_message())
                except Exception:  # noqa: BLE001 - the live view must not stop the outputs
                    self.throttled("live", logging.ERROR, "live view not sent", exc_info=True)

    def tick(self, now: float) -> bool:
        """The model goes on to now; its outputs, and once a second what they show for an error
        report. False if the model failed."""
        try:
            self.tracker.step(now)
            states = self.tracker.zone_states()
            bad = [z for z, st in states.items() if not st.finite()]
            if bad:  # never "nobody there" for a NaN: the model starts over
                raise FloatingPointError(f"probabilities not finite in {', '.join(bad)}")
            self.zone_states = states
            t = self.tracker.now
            if not self.shown or t - self.shown[-1][0] >= 1.0:
                self.shown.append((t, self.shown_now()))
                while self.shown[0][0] < t - REPORT_WINDOW:
                    self.shown.popleft()
        except Exception:  # noqa: BLE001 - see _model_failed
            self._model_failed("step")
            return False
        return True

    def _learned_files(self) -> list:
        """What was learned and is known about the people, as it is now - taken in the loop, the model
        changes with every frame: [(file, its bytes, or a function packing a copy)]. A part that fails
        is logged and left out; a people's state the model refuses to save (not finite, hidden.py) is a
        model failure (_model_failed: it starts over from the last save)."""
        files = []
        parts = [("ghostmap.json", lambda: json.dumps(self.tracker.ghost_map.to_dict()).encode()),
                 ("ld2410.json", lambda: json.dumps(self.tracker.ld_background.to_dict()).encode()),
                 ("destinations.json", lambda: json.dumps(self.tracker.dest_map.to_dict()).encode()),
                 ("people.json", self._people_bytes),
                 ("calibration.npz", self.calibrator.snapshot)]
        for name, make in parts:
            try:
                data = make()
            except Exception:  # noqa: BLE001 - logged; the other files are saved
                log.exception("%s not saved", name)
                if name == "people.json":
                    self._model_failed("saving the people")
                continue
            if data is not None:
                files.append((self.data_dir / name, data))
        return files

    @staticmethod
    def _write_files(files: list):
        for path, data in files:
            try:
                atomic_write(path, data() if callable(data) else data)
            except Exception:  # noqa: BLE001 - a full disk must not stop the app: logged, tried again later
                log.exception("%s not saved", path.name)

    def _save_learned(self):
        """Save now (at the exit), in the loop."""
        self._write_files(self._learned_files())

    async def _save_in_background(self):
        """Every 10 min: taken in the loop, packed (calibration.npz: seconds for a day) and written in a
        thread, so that the ticks and Home Assistant's states go on meanwhile."""
        try:
            await asyncio.to_thread(self._write_files, self._learned_files())
        except Exception:  # noqa: BLE001
            log.exception("saving failed")

    def _people_bytes(self) -> bytes | None:
        if self.tracker.start is None:  # nothing happened since the start: what it started from holds
            return None
        return json.dumps(self.tracker.people_state(), allow_nan=False).encode()

    def _save_people(self):
        """What is known about the people, for the next start (MODEL.md 5.3)."""
        data = self._people_bytes()
        if data is not None:
            atomic_write(self.data_dir / "people.json", data)

    def shown_now(self) -> dict:
        """What Home Assistant and the map show, for an error report: per room [count, P(somebody
        there), occupied], the people [id, most probable place, its probability, x, y, lost]."""
        zones = {z: [st.count, None if st.probability is None else round(st.probability, 3), st.occupied]
                 for z, st in self.zone_states.items() if not z.startswith("_")}
        persons = []
        for d in self.tracker.persons():
            place, p = max(d["places"].items(), key=lambda kv: kv[1])
            persons.append([d["id"], place, p, d["x"], d["y"], d["lost"]])
        return {"zones": zones, "persons": persons}

    def live_message(self) -> str:
        snap = self.tracker.snapshot()
        snap["zones"] = {k: v.to_dict() for k, v in self.zone_states.items()}
        snap["status"] = self.sensor_status
        snap["calibration"] = self.calibrator.status()
        elapsed = time.monotonic() - self.stats["since"]
        snap["load"] = {"cpu": round(100 * self.stats["cpu"] / max(elapsed, 1e-3), 2),
                        "fps": round(self.stats["frames"] / max(elapsed, 1e-3), 1)}
        if elapsed > 30:
            self.stats = {"frames": 0, "cpu": 0.0, "since": time.monotonic()}
        return json.dumps({"type": "live", **snap})

    def _broadcast(self, text: str):
        """To every browser on the live view, without waiting for any (LiveClient)."""
        for client in self.ws_clients:
            client.offer(text)

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
                self.available = "offline"
            if not self.replay:
                if self._saving is not None:
                    await asyncio.gather(self._saving, return_exceptions=True)
                self._save_learned()
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
        app.router.add_get("/api/tiles", self.h_tiles)
        app.router.add_get("/api/reports", self.h_reports)
        app.router.add_post("/api/reports", self.h_report)
        app.router.add_get("/api/reports/{name}", self.h_report_file)
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
                                  "ha": bool(self.ha_url), "limits": limits_dict()})

    async def h_put_config(self, request):
        """An edit in the web UI. Values outside the limits (model.check_config) are refused with
        400 and a message: an emptied field must not switch every light on. The new configuration
        holds only once the model runs with it (reconfigure, and the zones' states once, as every tick
        computes them); else the model goes on with the old one (rebuilt: reconfigure may have changed
        half of it) and the edit is refused with 400 - the web UI then reloads what holds."""
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "Keine gültige Konfiguration (JSON)."}, status=400)
        try:
            config = Config.from_dict(data, check=True)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001 - whatever the check let through: refused, not a 500
            return web.json_response({"error": f"Konfiguration unvollständig oder falsch: {e!r}"}, status=400)
        # only sensors recalibrated: the people stay; else the model starts over as after a restart (MODEL.md 5.3)
        try:
            restarted = self.tracker.reconfigure(config)
            self.tracker.zone_states()
        except Exception as e:  # noqa: BLE001 - the old configuration holds
            self._model_failed("a new configuration", count=False)
            return web.json_response({"error": f"Das Modell läuft mit dieser Konfiguration nicht: {e!r}. "
                                               "Es gilt die vorige."}, status=400)
        self.config = config
        if restarted:
            self._model_started(self.tracker.now)
        self.calibrator.config = config
        if self.client is not None:
            await self.discovery.sync(config.zones)
        try:
            config.save(self.config_path)
        except OSError as e:
            log.exception("tracker.json not saved")
            return web.json_response({"error": f"In Gebrauch, aber nicht gespeichert: {e}"}, status=500)
        # rooms are derived from the walls here; the editor takes them over
        return web.json_response({"ok": True, "restarted": restarted,
                                  "rooms": [z.to_dict() for z in config.zones_of("room")]})

    async def h_live(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        client = LiveClient(ws, request.transport)
        client.offer(self.live_message())
        self.ws_clients.add(client)
        sender = asyncio.create_task(client.run())
        try:
            async for _ in ws:
                pass
        finally:
            self.ws_clients.discard(client)
            sender.cancel()
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
        if action == "reset":  # a sensor was turned or moved for real: its old measurements are wrong
            self.calibrator.reset()
        elif action == "solve":
            # seconds of numpy: in a thread, the frames keep coming (the data is copied first)
            body = await request.json() if request.can_read_body else {}
            data = self.calibrator.data()
            return web.json_response(await asyncio.to_thread(
                calibration.solve, self.config, data, bool(body.get("mirror"))))
        elif action != "status":
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

    async def h_tiles(self, request):
        """The tiles (MODEL.md 5.3), for drawing where unseen people may be: per tile its square
        (lower left corner) and its room, which cuts the square."""
        tiles = self.tracker.tiles
        rooms = self.tracker.rooms
        return web.json_response({"id": id(tiles), "size": tiles.square,
                                  "tiles": [[round(float(x), 2), round(float(y), 2), rooms[int(r)]]
                                            for (x, y), r in zip(tiles.squares, tiles.room)]})

    # ------------------------------------------------------------ error reports

    async def h_report(self, request):
        """Something looked wrong: what (room, kind, how long ago, a note), saved with the config,
        when the model last started and what it had learned then (ghost map, LD2410C background)
        and knew about the people (the saved state it started from, None: nothing), the code, and the sensor data of the last REPORT_WINDOW s in the recordings' format, with
        what the app showed per second in between (topic "app/shown"): the moment can be replayed and
        compared with what the app believed. These are the truth data of the evaluation (MODEL.md 8)."""
        body = await request.json()
        kind = body.get("kind")
        if kind not in REPORT_KINDS:
            return web.json_response({"error": "Art des Fehlers fehlt."}, status=400)
        room = str(body.get("room") or "")
        ago = min(max(float(body.get("minutes_ago") or 0), 0.0), REPORT_WINDOW / 60)
        now = self.clock()  # the wall clock, or the recording's time in a replay
        start = self.model_start or self.tracker.start
        # what was learned and known when the model started, if that is in the report (the replay starts there,
        # as the app did); else now: started from nothing known 15 min back, the replay forgets the
        # difference (6.10. 22:40 and 22:42, 20 min after the start: |dP| < 0.01; MODEL.md 8)
        covered = start is not None and start >= (self.recent[0][0] if self.recent else now) - 1
        meta = {"report": {"t": now, "t_event": now - 60 * ago, "room": room, "kind": kind,
                           "text": str(body.get("text") or "")[:2000], "version": __version__, "code": CODE,
                           "model_start": start, "learned_at": start if covered else now},
                "config": self.config.to_dict(), **(self.start_learned if covered else self.tracker.learned()),
                "people": self.start_state if covered else None}
        self.reports.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{re.sub(r'[^a-z0-9_]', '', room.lower()) or 'haus'}-{kind}"
        lines = [json.dumps(meta)]
        shown = collections.deque(self.shown)
        for recv, topic, payload in list(self.recent):
            while shown and shown[0][0] <= recv:
                t, data = shown.popleft()
                lines.append(json.dumps({"t": t, "topic": "app/shown", "payload": data}))
            try:
                data = json.loads(payload)
            except ValueError:
                data = payload.decode(errors="replace")
            lines.append(json.dumps({"t": recv, "topic": topic, "payload": data}))
        lines += [json.dumps({"t": t, "topic": "app/shown", "payload": data}) for t, data in shown]
        path = self.reports / f"{name}.jsonl.gz"
        await asyncio.to_thread(path.write_bytes, gzip.compress("\n".join(lines).encode()))
        log.info("error report %s: %d messages", path.name, len(lines) - 1)
        return web.json_response({"name": path.name, "messages": len(lines) - 1})

    async def h_reports(self, request):
        out = []
        for path in sorted(self.reports.glob("*.jsonl.gz"), reverse=True) if self.reports.is_dir() else []:
            try:
                with gzip.open(path, "rt") as f:
                    rep = json.loads(f.readline())["report"]
            except (OSError, ValueError, KeyError):
                continue
            out.append({"name": path.name, "size": path.stat().st_size, **rep,
                        "kind_text": REPORT_KINDS.get(rep.get("kind"), rep.get("kind"))})
        return web.json_response({"reports": out, "kinds": REPORT_KINDS, "window_min": REPORT_WINDOW / 60})

    async def h_report_file(self, request):
        name = request.match_info["name"]
        path = self.reports / name
        if "/" in name or not name.endswith(".jsonl.gz") or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def h_reset_tracks(self, request):
        """"Tracks zurücksetzen": nothing known about where anybody is, the data decide again. The
        sensors' tracks start over too, as after lost data (MODEL.md 4.1): their next frames are a start,
        so whoever a sensor is tracking now counts. Until 0.18 their running tracks stayed and fed
        measurements to tracks the model no longer had: somebody sitting was gone for good, and the
        LD2410C learned them as background. That learns nothing for RESET_HOLD s now either."""
        tr = self.tracker
        tr.reset_people()
        for sid in tr.tracks:
            tr.tracks[sid] = SensorTracks(sid, tr._ids)
        for rt in tr.runtime.values():
            rt.last_frame = -math.inf
        tr.ld_background.pause(RESET_HOLD)
        self._model_started(tr.now)
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
