"""The running app: frames in, tracker, Home Assistant entities and the web UI out."""

import asyncio
import base64
import collections
import contextlib
import faulthandler
import gzip
import json
import logging
import math
import os
import pathlib
import re
import signal
import time
import uuid
from collections import defaultdict

import aiohttp
import presence_core
from aiohttp import web

from . import __version__, code_hash, ha
from . import calibration, debug_fenster, pause, realtime  # debug_fenster: DEBUGGING-WERKZEUG, zum späteren Entfernen
from .calibration import Calibrator
from . import model
from .model import Config
from .filter import Tracker
from .pause import Pauses
from .recording import Recorder
from .realtime import FRAME, PAUSE, Item, ModelThread
from .sources import Clock, ReplayClock, mqtt_source, replay_source
from .frames import Frame, SensorClock
from .util import Throttled, atomic_write, decoded

log = logging.getLogger(__name__)
STATIC = (pathlib.Path(__file__).parent / "static").resolve()
BLOCKED_DUMP = 15.0  # s the event loop may be busy before the watchdog logs where it is
REPORT_WINDOW = 15 * 60.0  # s of sensor data kept for an error report
CODE = code_hash()
REPORT_KIND = re.compile(r"[a-z_]{1,32}")  # a report's kind: a key (in its file name), worded by the web UI (util.js REPORT_KINDS)
REPORT_NAME = re.compile(r"[A-Za-z0-9_-]+\.jsonl\.gz")  # a report's file (h_report: time-room-kind)
# what a Home Assistant user who is no admin may call (App._roles: method, the route's canonical path)
USER_ROUTES = {("GET", path) for path in ("/", "/static/{stamp}/{path}", "/images/{name}", "/api/config", "/api/coverage",
                                          "/api/live", "/api/learned", "/api/tiles", "/api/reports", "/api/reports/{name}")}
USER_ROUTES.add(("POST", "/api/reports"))
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}
SEND_TIMEOUT = 5.0  # s a browser may take for one live message; a slower one is cut off
DOWN_AFTER = 3  # model failures without RETRY s of running between: the model is down
RESET_HOLD = 300.0  # s of each sensor's frames the LD2410C background does not learn from after a reset
RETRY = 60.0  # s: while down, the model is rebuilt and tried again this often (a rebuild is seconds of CPU)
LIVE_EVERY = 0.12  # s between two messages of the live view
DIAGNOSE_EVERY = 10.0  # s between two publishes of the diagnostics (ha.DIAGNOSTICS)
BEHIND = 5.0  # s of backlog that are logged (throttled): the model can't keep up
STOP_WAIT = 20.0  # s the stop waits for the model's thread to end its frame (then what it learned is not saved)
PENDING = 2000  # publishes handed to the MQTT client and not yet confirmed (PUBACK); more: dropped, sent again
# what was collected and learned under a geometry (the sensors' places, the plan: presence_core.geometry_key), each
# file with its key: the calibration's measurements, the LD2410C background, the destination map
COLLECTED = ("calibration.npz", "ld2410.json", "destinations.json")


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
                 ha_url: str | None = None, ha_token: str | None = None, ingress: bool = False,
                 offline_backlog: float = 0.0):
        self.data_dir = data_dir
        # Echtzeit (realtime.py): live, the model runs in its own thread from run() on (None: in the loop, as a
        # replay and the tests); its load and backlog. offline_backlog (the app's option, 0: off): with a backlog
        # of more than this many s every entity is unavailable until it is back below half of it
        self.worker: ModelThread | None = None
        self.loop = None  # the event loop (run)
        self.load = realtime.Load()
        self.offline_backlog = float(offline_backlog or 0.0)
        self.behind = False  # the backlog is above offline_backlog (the entities are unavailable)
        self.census = {}  # the model's size as of its last tick (Tracker.census)
        self.diagnostics = {}  # what the diagnostics said last (ha.DIAGNOSTICS)
        self._pending: set = set()  # publishes not yet confirmed (_publish)
        self._last_live = -math.inf  # when the last live view message was made (monotonic)
        self._dumped = None  # the model thread's busy start whose stack went to the log
        self.ingress = ingress
        self.images = data_dir / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.config_path = data_dir / "tracker.json"
        try:  # read through the core, in its form: the model can't run with one it refuses
            self.config = Config.load(self.config_path)
        except presence_core.ConfigError as e:
            log.error("tracker.json does not pass the check: %s", e)
            raise
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
        self._counting = None  # the task counting the calibration data for the live view (every 30 s)
        self.failures: list[float] = []  # when the model failed (monotonic), until it ran RETRY s without
        self.paused_until = -math.inf  # while down: the model rests until then (monotonic)
        self.available = None  # what this connection last said on the availability topic (None: nothing yet)
        # "Lernen pausieren" (pause.py): when it was on, kept with what was learned; the broker's retained
        # command, at the connect, decides from then on
        self.pauses = Pauses()
        self._pause_sent = None  # the switch's state as last published on this connection
        pause_path = data_dir / "pause.json"
        if not replay and pause_path.exists():
            try:
                self.pauses = Pauses.from_dict(json.loads(pause_path.read_text()))
            except Exception as e:  # noqa: BLE001 - learning then goes on until the switch says otherwise
                log.warning("pause.json unreadable (%r): learning not paused", e)
        self.recorder = Recorder(data_dir, self._config_line(), lambda: self.pauses.on, active=not replay)
        self._reset_tracker()

    def _model_failed(self, where: str, count: bool = True, people: dict | None = None):
        """An error in the model must not stop the app: 0.9.3 died on one LD2410C frame (7.10.,
        between 22:52 and 2:07), the MQTT client said goodbye cleanly, so its last will never went
        out, and Home Assistant kept the last states for hours. Logged with the traceback; the model
        starts over from what was learned and saved, about the people from what it knew just before
        (if that is still sound, else from the last save); people: that state as taken before (a new
        configuration the model failed with half-way)."""
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
            if self.replay:
                pass
            elif people is not None:
                atomic_write(self.data_dir / "people.json", json.dumps(people, allow_nan=False).encode())
            else:
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
        """A good tick: once the model has run RETRY s since it last failed, it runs (again). The rest
        while down (paused_until) is no running: until 0.28 it counted, and a model that one sensor's
        frames break was "online" for a moment after every rest."""
        if self.failures and time.monotonic() - max(self.failures[-1], self.paused_until) >= RETRY:
            if self.down:
                log.warning("the model runs again (%.0f s without failing, after %d failures)", RETRY, len(self.failures))
            self.failures.clear()

    def _reset_tracker(self):
        """A new model with what was learned and saved. A file that can't be read (empty after a
        power cut, broken, of another type) is logged and that part starts with nothing learned: until
        0.18 an empty calibration.npz or people.json [1] kept the app from starting
        at all."""
        self.tracker = Tracker(self.config)
        self.tracker.pauses = self.pauses  # Lernen pausieren: the app's switch (pause.py)
        # what was learned online, each part only under the geometry it was learned under (_load_learned): what
        # each LD2410C sees without anybody (MODEL.md 4.3), where walks go (6 "Ziel")
        self._load_learned("ld2410.json", self.tracker.use_ld_background, "LD2410C background")
        self._load_learned("destinations.json", self.tracker.use_dest_map, "destination map",
                           "learned on another grid: starting a new one")
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
                    if not self.calibrator.load(cal_path):
                        self._discard(["calibration.npz"])
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
        # the configuration it started with, and each one taken over since without a restart (sensors
        # recalibrated, outputs): [(model time, config)]. Replayed with the one at the report, a
        # recalibration 40 s in put the replay up to 0.12 off from what the app believed (BUGS 6)
        self.configs = [(t, self.config.to_dict())]

    def _config_line(self) -> str:
        """What a recording keeps of the configuration in use (recording.py)."""
        return json.dumps({"version": __version__, "config": self.config.to_dict()})

    def _config_changed(self):
        """A configuration taken over while the model goes on: when, for an error report."""
        if self.tracker.start is None:  # not started yet: it starts with this one
            self.configs = [(self.model_start, self.config.to_dict())]
        else:
            self.configs.append((self.tracker.now, self.config.to_dict()))

    # ------------------------------------------------------------------ input

    def on_message(self, topic: str, payload: bytes, recv: float, queued: float | None = None):
        """One MQTT message, received at recv (Unix time: when aiomqtt queued it, realtime.StampedQueue; queued:
        then on time.monotonic, for the backlog). Never raises: whatever comes in must not stop the app
        (Node-RED keeps a light's last state while it is gone). With the model's thread (live) its frames and
        the switch go to it; else (a replay, the tests) the model takes them here, at once."""
        try:
            self._on_message(topic, payload, recv, queued)
        except Exception:  # noqa: BLE001 - logged; the next message is handled as always
            self.throttled(("message", topic), logging.ERROR, "message on %s not handled", topic, exc_info=True)

    def _on_message(self, topic: str, payload: bytes, recv: float, queued: float | None = None):
        worker = self.worker
        if topic in pause.TOPICS:  # Lernen pausieren (live: the command; in a replayed recording both)
            self.recorder.add(recv, topic, payload)
            if worker is not None:  # in order with the frames, at its arrival (realtime.py)
                worker.put(Item(PAUSE, payload, recv, queued))
            else:
                self._on_pause(payload, recv)
            return
        if self.discovery.retained(topic, payload):  # our own entities as the broker keeps them
            return
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != self.prefix:
            return
        sensor_id, kind = parts[1], parts[2]
        if kind == "frame":
            try:
                frame = Frame.parse(payload)
            except ValueError as e:  # dropped like a frame lost on the way (and not in a report)
                self.throttled(("frame", sensor_id), logging.WARNING, "frame of %s dropped: %s (%r)",
                               sensor_id, e, bytes(payload[:200]))
                return
        self.recorder.add(recv, topic, payload)
        self.recent.append((recv, topic, payload))
        while self.recent and self.recent[0][0] < recv - REPORT_WINDOW:
            self.recent.popleft()
        if kind == "status":
            self.sensor_status[sensor_id] = payload.decode(errors="replace")
            return
        if kind != "frame":
            return
        if worker is not None:
            self.clock.arrived(recv)  # frames come: the model's time waits for them (sources.Clock)
            worker.put(Item(FRAME, frame, recv, queued, sensor=sensor_id))
            return
        start = time.process_time()
        self._frame(sensor_id, frame, recv)
        self.load.processed(0.0, True)
        self.load.cpu += time.process_time() - start

    def _frame(self, sensor_id: str, frame: Frame, recv: float):
        """A frame of a sensor, received at recv, into the model (its thread, or in the loop)."""
        t = self.clocks[sensor_id](recv, frame, self.tracker.now)
        if self.replay and t < self.last_frame_t - 10:
            log.info("replay restarted, resetting tracker")
            self._reset_tracker()
            self.calibrator.reset()
            t = self.clocks[sensor_id](recv, frame, self.tracker.now)
        self.clock.frame(t, recv)
        try:
            self.calibrator.on_frame(sensor_id, t, frame, learn=not self.tracker.paused(t))
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

    # ------------------------------------------------------------ the model's thread (realtime.py)

    def _process(self, item: Item, backlog: float):
        """A message in the model's thread (backlog: s it waited since it came)."""
        if item.kind == FRAME:
            self._frame(item.sensor, item.payload, item.recv)
        elif item.kind == PAUSE:
            self._on_pause(item.payload, item.recv)

    def _data_lost(self, dropped: int):
        """Frames were dropped (realtime.Inbox: the model could not keep up): to the core, all sensors lost data
        (realtime.py: their next frames are a start, the time between says nothing)."""
        self.tracker.reset_tracks()
        self.throttled("compacted", logging.WARNING,
                       "the model is %.1f s behind: %d waiting frames dropped, the newest of each sensor kept; "
                       "the model takes it as lost data", self.worker.backlog() if self.worker else 0.0, dropped)

    def _frames_lost(self, n: int):
        """n frames lost before they reached the model's queue (aiomqtt's queue full: the loop hung)."""
        if self.worker is not None:
            self.worker.inbox.mark_lost(n)
        else:
            self.tracker.reset_tracks()

    def _thread_tick(self):
        """Every realtime.TICK s in the model's thread: the model goes on to the clock (_model_tick)."""
        return self._model_tick()

    async def in_model(self, fn, *args):
        """fn(*args) where the model is (its thread: between two of its messages, the loop goes on meanwhile;
        else here). Everything that reads or changes the model, the calibration data or what is shown goes
        through this: the core must never be used by two threads at once."""
        worker = self.worker
        if worker is None or not worker.alive:
            return fn(*args)
        return await worker.call(fn, *args)

    def _on_pause(self, payload: bytes, t: float):
        """The switch "Lernen pausieren" (pause.py) set to ON or OFF at time t (its receive time: the
        frames' times are theirs too)."""
        on = pause.parse(payload)
        if on is None:
            self.throttled("pause", logging.WARNING, "Lernen pausieren: %r is neither ON nor OFF", bytes(payload[:50]))
            return
        if not self.pauses.set(on, t):
            return
        log.info("learning %s (Lernen pausieren)", "paused" if on else "goes on")
        self.pauses.prune(t - 2 * REPORT_WINDOW)  # a report needs the last REPORT_WINDOW s
        if not self.replay:
            try:
                atomic_write(self.data_dir / "pause.json", self._pause_bytes())
            except Exception:  # noqa: BLE001 - it is saved again with what was learned
                log.exception("pause.json not saved")

    def _pause_bytes(self) -> bytes:
        return json.dumps(self.pauses.to_dict()).encode()

    async def _send_pause(self):
        """The switch's state to Home Assistant, retained, when it changed (or a new connection)."""
        if self.client is not None and self.publish_enabled and self._pause_sent != self.pauses.on:
            if await self._publish(pause.STATE, "ON" if self.pauses.on else "OFF", True):
                self._pause_sent = self.pauses.on

    async def _on_client(self, client):
        self.client = client
        if client is not None:  # aiomqtt warns from 10 unconfirmed publishes on (at every start: discovery and
            client.pending_calls_threshold = PENDING  # states at once); the app's own limit and warning: _publish
        self.available = None
        self._pause_sent = None
        self.discovery.reconnected()  # every state and room availability goes out again with the next tick
        if client is not None:
            # Lernen pausieren: the broker holds Home Assistant's last command (retained), also one sent
            # while the app was down; read only, so also without publishing
            try:
                await client.subscribe(pause.COMMAND)
            except Exception as e:  # noqa: BLE001 - a lost connection is handled by the source loop
                log.debug("subscribe failed: %s", e)
        if client is not None and self.publish_enabled:
            # "online" only once fresh states are out (housekeeping): the broker may hold "online" and the
            # states of a run that ended without its last will, and Home Assistant would show them as now
            await self._set_available(False)
            self.discovery.published.clear()
            await self.discovery.sync(*await self.in_model(self._zones))
            # what the broker retains of earlier runs: entities no longer wanted are removed (ha.py)
            try:
                await client.subscribe(ha.DISCOVERY)
            except Exception as e:  # noqa: BLE001 - a lost connection is handled by the source loop
                log.debug("subscribe failed: %s", e)

    def _zones(self) -> tuple:
        """The model's zones of the home and the rooms with an own sensor in use (ha.Discovery.sync)."""
        return self.tracker.plan["home"], self.tracker.heard.watched()

    async def _publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        """Hands a message to the MQTT client without waiting for the broker's confirmation (PUBACK): until
        Echtzeit the loop waited for every retained one (QoS 1), up to 10 s each, and the model, the web UI
        and every other state waited with it. Retained ones (states, availability, discovery) stay QoS 1: the
        broker keeps what Home Assistant reads after its restart, and the client sends it again until the
        broker has it. Handed over in order (eager: paho has it when this returns). False if not handed over
        (no connection, or more than PENDING not confirmed: the broker hangs); the caller sends it again."""
        if self.client is None or not self.publish_enabled:
            return False
        if len(self._pending) >= PENDING:
            self.throttled("pending", logging.WARNING, "MQTT: %d messages not confirmed by the broker, %s dropped",
                           len(self._pending), topic)
            return False
        try:
            task = asyncio.Task(self.client.publish(topic, payload, qos=1 if retain else 0, retain=retain),
                                loop=asyncio.get_running_loop(), eager_start=True)
        except Exception as e:  # noqa: BLE001 - a lost connection is handled by the source loop
            log.debug("publish failed: %s", e)
            return False
        if task.done():
            return self._published(task)
        self._pending.add(task)
        task.add_done_callback(self._published)
        return True

    def _published(self, task) -> bool:
        """A publish ended (confirmed, or failed: logged; a lost connection is handled by the source loop)."""
        self._pending.discard(task)
        if task.cancelled():
            return False
        e = task.exception()
        if e is not None:
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
        """Every 0.1 s in the loop: the recording, the switch, the availability; the model's outputs to Home
        Assistant and the live view (from its thread, or the model moved on here: a replay, the tests); every
        DIAGNOSE_EVERY s the diagnostics."""
        last = None  # the model thread's outputs last published
        next_diagnosis = time.monotonic()
        while True:
            # watchdog: if nothing comes back here for BLOCKED_DUMP s, the stack of every thread goes
            # to the log - where the app hangs (each call replaces the previous timer)
            faulthandler.dump_traceback_later(BLOCKED_DUMP)
            await asyncio.sleep(0.1)
            self.recorder.tick()
            await self._send_pause()
            self._watch_backlog()
            if self.down or self.behind:
                await self._set_available(False)
            if self.worker is None:
                start = time.process_time()
                out = self._model_tick()
                self.load.cpu += time.process_time() - start
            else:
                out = self.worker.outputs
                out, last = (None, last) if out is last else (out, out)
            if out is not None:
                await self._outputs(out)
            if time.monotonic() >= next_diagnosis:
                next_diagnosis = time.monotonic() + DIAGNOSE_EVERY
                await self._diagnose()

    def _model_tick(self) -> dict | None:
        """Where the model is (its thread every realtime.TICK s, or the housekeeping): it goes on to the
        clock (tick); its outputs for the loop: the zones' states at the model's time, its size, the live view's
        part of the model if a browser watches (at most every LIVE_EVERY s). Every 10 min what was learned is
        taken (copies) and written in a thread. None if the model does not run (not started, down, failed)."""
        if self.tracker.start is None or time.monotonic() < self.paused_until:
            return None
        if not self.tick(self.clock()):
            return None
        self._model_ran()
        out = {"states": self.zone_states, "t": self.tracker.now, "census": self.tracker.census()}
        if (not self.replay and time.monotonic() - self.last_model_save > 600
                and (self._saving is None or self._saving.done())):
            self.last_model_save = time.monotonic()
            self._save_soon(self._learned_files())
        now = time.monotonic()
        if self.ws_clients and now - self._last_live >= LIVE_EVERY:
            self._last_live = now
            try:
                out["live"] = self._live_model()
            except Exception:  # noqa: BLE001 - the live view must not stop the outputs
                self.throttled("live", logging.ERROR, "live view not made", exc_info=True)
        return out

    def _save_soon(self, files: list):
        """What was learned, taken in the model's place, written in a thread (from the loop)."""
        def start():
            self._saving = asyncio.ensure_future(self._save_in_background(files))
        if self.worker is not None and self.loop is not None:
            with contextlib.suppress(RuntimeError):  # the loop closed (the app stops: it saves at the end)
                self.loop.call_soon_threadsafe(start)
        else:
            start()

    async def _outputs(self, out: dict):
        """The model's outputs (_model_tick) to Home Assistant and the live view."""
        self.census = out.get("census") or {}
        try:
            await self.discovery.states(out["states"], t=out["t"])
            if not self.down and not self.behind:
                await self._set_available(True)  # after the first fresh states of this connection
        except Exception:  # noqa: BLE001 - the loop must go on (it is all of the app's output)
            self.throttled("states", logging.ERROR, "states not published", exc_info=True)
        if out.get("live") is not None and self.ws_clients:
            try:
                self._broadcast(self._live_text(out["live"]))
            except Exception:  # noqa: BLE001 - the live view must not stop the outputs
                self.throttled("live", logging.ERROR, "live view not sent", exc_info=True)

    def backlog(self) -> float:
        """How long the oldest message the model has not done yet waits (s; 0 without its thread)."""
        return self.worker.backlog() if self.worker is not None else 0.0

    def _watch_backlog(self):
        """The backlog against offline_backlog (the option, `behind`: every entity unavailable, back below half
        of it), logged above BEHIND s; a model thread busy with one message for BLOCKED_DUMP s: its stack to the
        log (the loop's watchdog no longer sees it)."""
        backlog = self.backlog()
        if backlog > BEHIND:
            self.throttled("behind", logging.WARNING, "the model is %.1f s behind the frames", backlog)
        if self.offline_backlog > 0:
            if not self.behind and backlog > self.offline_backlog:
                self.behind = True
                log.warning("the model is %.1f s behind (option offline_backlog %.0f s): Home Assistant gets "
                            "'unavailable'", backlog, self.offline_backlog)
            elif self.behind and backlog <= self.offline_backlog / 2:
                self.behind = False
                log.info("the model caught up (%.1f s behind): available again with its next states", backlog)
        busy = self.worker.busy if self.worker is not None else None
        if busy is not None and time.monotonic() - busy[0] > BLOCKED_DUMP and self._dumped != busy[0]:
            self._dumped = busy[0]
            log.warning("the model's thread has been busy with one %s for %.0f s; the threads' stacks:", busy[1],
                        time.monotonic() - busy[0])
            faulthandler.dump_traceback(all_threads=True)

    async def _diagnose(self):
        """The diagnostics (ha.DIAGNOSTICS) to Home Assistant and the live view: the backlog (the highest since
        the last time, and now), the model thread's CPU (% of one core), its size, frames/s processed."""
        w = self.load.window()
        worker = self.worker
        self.diagnostics = {
            "backlog": round(max(w["backlog_max"], self.backlog()), 1),
            "cpu": round(w["cpu"], 1),
            "hypotheses": self.census.get("hypotheses"),
            "untracked": self.census.get("untracked"),
            "fps": round(w["fps"], 1),
            "fps_in": round(w["fps_in"], 1),
            "waiting": worker.inbox.frames if worker is not None else 0,
            "dropped": w["dropped"],
            "compactions": w["compactions"],
            "thread": worker is not None,
        }
        if self.client is not None and self.publish_enabled:
            try:
                await self.discovery.diagnostics(self.diagnostics)
            except Exception:  # noqa: BLE001
                self.throttled("diagnostics", logging.ERROR, "diagnostics not published", exc_info=True)

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

    @property
    def geometry(self) -> str:
        """The key of the configuration's geometry (the core's: where the sensors hang and how they see and are
        read, the walls, doors and zones): what is collected and learned now holds under it only."""
        return presence_core.geometry_key(self.config.model_json())

    def _discard(self, names: list):
        """Delete these files of what was collected or learned under another geometry (logged)."""
        for name in names:
            (self.data_dir / name).unlink(missing_ok=True)
        log.info("%s: collected under another geometry (a sensor's place or the floor plan), discarded", ", ".join(names))

    def _forget(self) -> dict:
        """A configuration of another geometry is in use (h_put_config): everything collected and learned under the
        old one is discarded, for every sensor - the calibration's measurements and the files of what was learned
        (the tracker started its parts over itself, the core's Learned::follow). What it was, for the web UI: the
        measurements."""
        rows = self.calibrator.count()
        self.calibrator.reset()
        self._discard(list(COLLECTED))
        return {"calibration": rows}

    def _load_learned(self, name: str, use, what: str, not_used: str | None = None):
        """A learned part from its file in the data directory, if there is one: used by the tracker (it
        checks it) if it was learned under the geometry of the configuration (the key saved with it,
        _learned_files), else discarded (the sensors or the floor plan changed while the app did not run);
        unreadable, the tracker starts the part over (logged)."""
        path = self.data_dir / name
        if not path.exists():
            return
        try:
            stored = json.loads(path.read_text())
            if stored["geometry"] != self.geometry:
                self._discard([name])
                return
            used = use(stored["learned"])
            if used is False and not_used:
                log.info("%s %s", what, not_used)
            for why in used if isinstance(used, list) else ():  # the LD2410C background's parts dropped
                log.warning("%s: a part dropped, it starts over: %s", name, why)
        except Exception as e:  # noqa: BLE001 - the part from the prior / empty holds
            log.warning("%s unreadable (%r), starting a new %s", name, e, what)
            use(None)

    def _learned_files(self) -> list:
        """What was learned and is known about the people, as it is now - taken in the loop, the model
        changes with every frame: [(file, its bytes, or a function packing a copy)]. A part that fails
        is logged and left out; a people's state the model refuses to save (not finite: the core's filter/saved.rs) is a
        model failure (_model_failed: it starts over from the last save)."""
        files = []
        part = self._learned_packer(self.tracker.learned_snapshot(), self.geometry)
        parts = [("ld2410.json", lambda: part("ld_background")),
                 ("destinations.json", lambda: part("dest_map")),
                 ("people.json", self._people_bytes),
                 ("pause.json", self._pause_bytes),
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
    def _learned_packer(learned, geometry: str):
        """Per part of a copy of what was learned a function packing it with the key of the geometry it was
        learned under ({"geometry", "learned"}), all packed at once at the first call - in the thread that
        writes them (the destination map's compression takes milliseconds)."""
        packed = {}

        def part(name: str):
            def pack() -> bytes:
                if not packed:
                    packed.update(json.loads(learned.json()))
                return json.dumps({"geometry": geometry, "learned": packed[name]}).encode()
            return pack
        return part

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

    async def _save_in_background(self, files: list | None = None):
        """Every 10 min: taken where the model is (files; None: here), packed (calibration.npz: seconds for a
        day) and written in a thread, so that the ticks and Home Assistant's states go on meanwhile."""
        try:
            await asyncio.to_thread(self._write_files, self._learned_files() if files is None else files)
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
        persons = [[d["id"], d["place"], d["places"].get(d["place"], 0.0), d["x"], d["y"], d["lost"]]
                   for d in self.tracker.persons()]
        return {"zones": zones, "persons": persons}

    def live_message(self) -> str:
        """The live view's message, all of it here (a replay, the tests; with the model's thread: _live_model
        there, _live_text in the loop)."""
        return self._live_text(self._live_model())

    def _live_model(self) -> dict:
        """The live view's part of the model (where the model is)."""
        snap = self.tracker.snapshot()
        snap["zones"] = {k: v.to_dict() for k, v in self.zone_states.items()}
        # rooms whose own sensors went silent: unavailable in Home Assistant (the core's silent.rs)
        snap["unavailable"] = sorted(k for k, v in self.zone_states.items() if not v.available)
        snap["learning_paused"] = {"on": self.pauses.on, "since": self.pauses.since}
        return snap

    def _live_text(self, snap: dict) -> str:
        """The live view's message of the model's part (in the loop): with the sensors' status, the calibration
        data's count and the load (the diagnostics: the model's CPU, frames/s, backlog)."""
        snap = dict(snap)
        snap["status"] = self.sensor_status
        snap["calibration"] = self._calibration_status()
        d = self.diagnostics
        snap["load"] = {"cpu": d.get("cpu"), "fps": d.get("fps"), "backlog": d.get("backlog"),
                        "hypotheses": d.get("hypotheses"), "untracked": d.get("untracked")}
        return json.dumps({"type": "live", **snap})

    def _calibration_status(self) -> dict | None:
        """The calibration data's last count (None: none yet); when it is 30 s old, a new one is
        counted in a thread (Calibrator.status_job), the live view gets it with a later message."""
        cal = self.calibrator
        if cal.status_due() and (self._counting is None or self._counting.done()):
            self._counting = asyncio.ensure_future(self._count_calibration())
        return cal.last_status()

    async def _count_calibration(self):
        cal = self.calibrator
        try:
            cal.use_status(await asyncio.to_thread(await self.in_model(cal.status_job)))
        except Exception:  # noqa: BLE001 - the live view goes on with the last count
            self.throttled("calibration", logging.ERROR, "calibration data not counted", exc_info=True)

    def _broadcast(self, text: str):
        """To every browser on the live view, without waiting for any (LiveClient)."""
        for client in self.ws_clients:
            client.offer(text)

    async def run(self, host: str, port: int):
        runner = web.AppRunner(self.web_app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        log.info("web UI on http://%s:%d", host, port)
        self.loop = asyncio.get_running_loop()
        tasks = [self.housekeeping()]
        if self.replay:  # in the loop, every frame: the replay waits for the model (sources.ReplayClock)
            tasks.append(replay_source(self.replay, self.clock, self.on_message))
        elif self.mqtt_settings:
            # live: the model in its own thread (realtime.py), the loop receives and publishes
            self.worker = ModelThread(self._process, self._thread_tick, self._data_lost, self.load_inbox())
            self.worker.start()
            tasks.append(mqtt_source(self.mqtt_settings, self.prefix, self.on_message, self._on_client,
                                     publish=self.publish_enabled, on_lost=self._frames_lost))
            if self.ha_url and self.ha_token:  # DEBUGGING-WERKZEUG, zum späteren Entfernen (debug_fenster.py)
                tasks.append(debug_fenster.Fenster(self.ha_url, self.ha_token, self.recorder.add,
                                                   lambda: self.recorder.on).run())
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
            # "offline" went out before the MQTT connection closed (mqtt_source): here it is closed
            stopped = True
            if self.worker is not None:  # its frame first (the core can't be stopped in one)
                self.worker.stop()
                stopped = await asyncio.to_thread(self.worker.join, STOP_WAIT)
            if not self.replay:
                if self._saving is not None:
                    await asyncio.gather(self._saving, return_exceptions=True)
                if stopped:
                    self._save_learned()
                else:
                    log.error("the model's thread is still busy after %.0f s: what was learned since the last save "
                              "is not saved", STOP_WAIT)
                await self.recorder.flush(close=True)
            await runner.cleanup()

    def load_inbox(self) -> realtime.Inbox:
        """The model thread's queue (realtime.Inbox), counting into the app's load."""
        return realtime.Inbox(self.load)

    # ------------------------------------------------------------------- web

    def web_app(self) -> web.Application:
        app = web.Application(client_max_size=20 * 1024 * 1024, middlewares=[self._roles])
        app.router.add_get("/", self.h_index)
        app.router.add_get("/static/{stamp}/{path:.+}", self.h_static)
        app.router.add_get("/images/{name}", self.h_image)
        app.router.add_get("/api/config", self.h_get_config)
        app.router.add_put("/api/config", self.h_put_config)
        app.router.add_get("/api/coverage", self.h_coverage)
        app.router.add_get("/api/live", self.h_live)
        app.router.add_post("/api/background", self.h_upload)
        app.router.add_post("/api/calibration/{action}", self.h_calibration)
        app.router.add_get("/api/ha/cameras", self.h_cameras)
        app.router.add_get("/api/ha/map", self.h_map)
        app.router.add_post("/api/tracks/reset", self.h_reset_tracks)
        app.router.add_get("/api/learned", self.h_learned)
        app.router.add_get("/api/tiles", self.h_tiles)
        app.router.add_get("/api/reports", self.h_reports)
        app.router.add_post("/api/reports", self.h_report)
        app.router.add_get("/api/reports/{name}", self.h_report_file)
        app.router.add_delete("/api/reports/{name}", self.h_delete_report)
        app.router.add_get("/api/recording", self.h_recording)
        app.router.add_put("/api/recording", self.h_recording_switch)
        app.router.add_delete("/api/recording", self.h_recording_delete)
        app.router.add_get("/api/recording/download", self.h_recording_download)
        return app

    def is_admin(self, request) -> bool:
        """Behind ingress the Supervisor (2026.10.4 on) names the user's Core groups and drops any the browser sent;
        without the header (an older Supervisor, a session from while Core was down) the user is no admin. Started
        outside the Supervisor (development, replay) there is no ingress: everyone is."""
        return not self.ingress or "system-admin" in request.headers.get("X-Remote-User-Groups", "").split(",")

    @web.middleware
    async def _roles(self, request, handler):
        """Every Home Assistant user sees the live map and what was learned and may report an error; the rest
        (editing, calibration, recordings, Home Assistant's cameras) is for admins. New routes are admin only."""
        route = (request.method, getattr(request.match_info.route.resource, "canonical", None))
        if route not in USER_ROUTES and not self.is_admin(request):
            return web.json_response({"error": "admin_only"}, status=403)
        return await handler(request)

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
        """An uploaded image. Served from the ingress origin, that is Home Assistant's: an SVG opened
        directly could run a script there. The policy forbids any (as an <img> or <image> it can't run
        one anyway), and the type is the one of the name, never sniffed."""
        name = request.match_info["name"]
        path = self.images / name
        if "/" in name or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={
            "Cache-Control": "max-age=3600", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"})

    async def h_get_config(self, request):
        known, config = await self.in_model(lambda: (sorted(set(self.tracker.runtime) | set(self.config.sensor_ids)),
                                                      self.config.to_dict()))
        return web.json_response({"config": config, "derived": model.derive(config), "sensors_seen": known,
                                  "admin": self.is_admin(request), "status": self.sensor_status, "replay": bool(self.replay),
                                  "ha": bool(self.ha_url), "limits": model.limits(), "defaults": model.defaults()})

    async def h_coverage(self, request):
        """The coverage of the saved configuration and each sensor's sight per tile, of the world the model runs in
        (the core's)."""
        try:
            return web.json_response(await self.in_model(lambda: self.tracker.coverage()))
        except Exception as e:  # noqa: BLE001 - the model is down (restarting after an error)
            return web.json_response({"error": "model_down", "detail": repr(e)}, status=503)

    async def h_put_config(self, request):
        """A save in the web UI: the configuration in the core's form (Config.from_dict: checked, what it leaves
        out filled in) is stored as it is and goes back ("config"): the web UI goes on with exactly that.
        Refused with 400 and the core's problem ("problem", model.problem; the web UI words it): an emptied field
        must not switch every light on. The new configuration holds only once the model runs with it
        (reconfigure, and the zones' states once, as every tick computes them); else the model goes on with the
        old one (rebuilt: reconfigure may have changed half of it) and the save is refused with 400. The map
        comes finished from the web UI's editor (rooms between the walls, doors on their walls). Answers
        "derived", what the web UI shows of the model of the saved configuration (None: the model's did not
        change). A change of what only the web UI reads (the background image: it checks the layers; the rooms'
        label points) is only stored: the model never reads it. A configuration of another geometry (the core's
        key: a sensor placed, moved, turned, read otherwise, its view; the walls, doors, zones; also a
        calibration taken over) discards everything collected and learned under the old one, for every sensor
        (_forget); "discarded" says what (None: nothing)."""
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "not_json"}, status=400)
        try:
            config = Config.from_dict(data)
        except presence_core.ConfigError as e:
            return web.json_response({"problem": model.problem(e)}, status=400)
        took = await self.in_model(self._take_config, config)
        if "error" in took:
            return web.json_response({"error": "model_refused", "detail": took["error"]}, status=400)
        if took["same"]:
            try:
                config.save(self.config_path)
            except OSError as e:
                log.exception("tracker.json not saved")
                return web.json_response({"error": "not_saved", "detail": str(e)}, status=500)
            self.recorder.config(self._config_line())
            return web.json_response({"ok": True, "restarted": False, "derived": None, "config": config.to_dict(),
                                      "discarded": None})
        if self.client is not None:
            await self.discovery.sync(*took["zones"])
        try:
            config.save(self.config_path)
        except OSError as e:
            log.exception("tracker.json not saved")
            return web.json_response({"error": "in_use_not_saved", "detail": str(e)}, status=500)
        self.recorder.config(self._config_line())
        return web.json_response({"ok": True, "restarted": took["restarted"], "derived": model.derive(config.to_dict()),
                                  "config": config.to_dict(), "discarded": took["discarded"]})

    def _take_config(self, config: Config) -> dict:
        """The model takes a new configuration (where it is, h_put_config): {"same": True} if its part is the
        same (it goes on as it is); else whether it started over, what was discarded and its zones for Home
        Assistant; {"error"} if it can't run with it (the old one holds)."""
        if config.model_json() == self.config.model_json():
            # the model's part is the same: it goes on as it is (with this configuration, the same to it)
            self.config = self.tracker.config = self.calibrator.config = config
            return {"same": True}
        moved = presence_core.geometry_key(config.model_json()) != self.geometry
        # only sensors recalibrated: the people stay; else the model starts over as after a restart (MODEL.md 5.3)
        try:
            restarted = self.tracker.reconfigure(config)
            self.tracker.zone_states()
        except Exception as e:  # noqa: BLE001 - the old configuration holds
            # and the people as they were before it (the model may have the new tiles by now: saved from
            # it, people.json had their fingerprint, and the old model restored nothing until 0.28)
            self._model_failed("a new configuration", count=False,
                               people=getattr(self.tracker, "reconfigured_from", None))
            return {"error": repr(e)}
        self.config = config
        discarded = self._forget() if moved else None
        if restarted:
            self._model_started(self.tracker.now)
        else:
            self._config_changed()
        self.calibrator.config = config
        return {"same": False, "restarted": restarted, "discarded": discarded, "zones": self._zones()}

    async def h_live(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        client = LiveClient(ws, request.transport)
        client.offer(self._live_text(await self.in_model(self._live_model)))
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
                    return web.json_response({"error": "https_only"}, status=400)
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                        if r.status != 200:
                            return web.json_response({"error": "download", "detail": r.status}, status=502)
                        data = await r.content.read(20 * 1024 * 1024)
                filename = url.rsplit("/", 1)[-1].split("?")[0]
            else:
                filename, data = body.get("filename", ""), base64.b64decode(body.get("data", ""))
        else:
            field = await (await request.multipart()).next()
            filename, data = field.filename or "", await field.read()
        suffix = pathlib.Path(filename).suffix.lower()
        if suffix not in IMAGE_TYPES:
            return web.json_response({"error": "image_type"}, status=400)
        name = f"{uuid.uuid4().hex[:12]}{suffix}"
        (self.images / name).write_bytes(data)
        return web.json_response({"name": name, "url": f"images/{name}"})

    async def h_calibration(self, request):
        action = request.match_info["action"]
        cal = self.calibrator
        if action == "reset":  # a sensor turned or moved for real, its drawing not: its old measurements are wrong
            await self.in_model(cal.reset)
        elif action == "solve":
            # seconds of numpy: in a thread, the frames keep coming (the data is copied first)
            body = await request.json() if request.can_read_body else {}
            config, data = await self.in_model(lambda: (self.config, cal.data()))
            return web.json_response(await asyncio.to_thread(
                calibration.solve, config, data, bool(body.get("mirror"))))
        elif action != "status":
            raise web.HTTPNotFound()
        if cal.status_due():
            cal.use_status(await asyncio.to_thread(await self.in_model(cal.status_job)))
        return web.json_response(cal.last_status())

    async def h_learned(self, request):
        """What the running model learned (the core's, computed only when asked; the model is only read): per sensor
        in use its LD2410C's background, the destination map."""
        try:
            return web.json_response(await self.in_model(lambda: self.tracker.learned_view()))
        except Exception as e:  # noqa: BLE001 - the model is down (restarting after an error)
            return web.json_response({"error": "model_down", "detail": repr(e)}, status=503)

    async def h_tiles(self, request):
        """The tiles (MODEL.md 5.3), for drawing where unseen people may be: per tile its square
        (lower left corner) and its room, which cuts the square."""
        tiles = await self.in_model(lambda: self.tracker.tiles())
        return web.json_response({"id": tiles["id"], "size": tiles["size"],
                                  "tiles": [[round(x, 2), round(y, 2), r] for (x, y), r in zip(tiles["squares"], tiles["room"])]})

    # ------------------------------------------------------------ error reports

    async def h_report(self, request):
        """Something looked wrong: what (room, kind, how long ago, a note), saved with the config,
        when the model last started and what it had learned then (LD2410C background, destination map)
        and knew about the people (the saved state it started from, None: nothing), the code, and the
        sensor data of the last REPORT_WINDOW s in the recordings' format, with what the app showed per
        second in between (topic "app/shown"): the moment can be replayed and compared with what the
        app believed. These are the truth data of the evaluation (MODEL.md 8). "config" is the
        configuration the replay starts with, "config_changes" [[model time, config], ...] the ones
        taken over later without a restart (a sensor recalibrated): the replay takes them over at the
        same times (tools/replay.py)."""
        body = await request.json()
        kind = body.get("kind")
        if not isinstance(kind, str) or not REPORT_KIND.fullmatch(kind):
            return web.json_response({"error": "report_kind"}, status=400)
        room = str(body.get("room") or "")
        try:
            ago = float(body.get("minutes_ago") or 0)
        except (TypeError, ValueError):
            ago = math.nan
        if not math.isfinite(ago):  # (NaN passed min and max: comparisons with it are false)
            return web.json_response({"error": "minutes"}, status=400)
        ago = min(max(ago, 0.0), REPORT_WINDOW / 60)
        recent = list(self.recent)  # copies of the buffers the frames go on into (here and in the model's place)
        now, head, shown = await self.in_model(self._report_head, body, room, kind, ago,
                                               recent[0][0] if recent else None)
        self.reports.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{re.sub(r'[^a-z0-9_]', '', room.lower()) or 'haus'}-{kind}"
        # the header was made where the model is (the learned parts may be the model's own, still changing);
        # lines and gzip in a thread (15 min of frames: 0.2 s and 0.6 s here, many times that on Home Assistant,
        # MODEL.md 10)
        # one of the same second, room and kind: a file of its own. The name is taken now (an empty file,
        # O_EXCL; the list skips it), not when the thread writes: a second report sent meanwhile (a double
        # click) took the same name and overwrote the first until 0.28
        k = 1
        while True:
            path = self.reports / (f"{name}.jsonl.gz" if k == 1 else f"{name}-{k}.jsonl.gz")
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                k += 1
        try:
            n = await asyncio.to_thread(self._write_report, path, head, recent, shown)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        log.info("error report %s: %d messages", path.name, n)
        return web.json_response({"name": path.name, "messages": n})

    def _report_head(self, body: dict, room: str, kind: str, ago: float, first: float | None) -> tuple:
        """An error report's header (where the model is: h_report), its time and what the app showed:
        (now, the header's JSON, [(model time, shown)]). first: the time of the report's first message."""
        now = self.clock()  # the wall clock, or the recording's time in a replay
        start = self.model_start or self.tracker.start
        # what was learned and known when the model started, if that is in the report (the replay starts there,
        # as the app did); else now: started from nothing known 15 min back, the replay forgets the
        # difference (6.10. 22:40 and 22:42, 20 min after the start: |dP| < 0.01; MODEL.md 8)
        first = now if first is None else first
        covered = start is not None and start >= first - 1
        # the configuration at the replay's start (the model's, else the report's first frame), and the
        # ones taken over after it without a restart, with their model times
        begin = start if covered else first
        k = max([i for i, (t, _) in enumerate(self.configs) if t is None or t <= begin], default=0)
        meta = {"report": {"t": now, "t_event": now - 60 * ago, "room": room, "kind": kind,
                           "text": str(body.get("text") or "")[:2000], "version": __version__, "code": CODE,
                           "model_start": start, "learned_at": start if covered else now},
                "config": self.configs[k][1], "config_changes": [[t, c] for t, c in self.configs[k + 1:]],
                # Lernen pausieren: the intervals from the replay's start on (end None: still paused)
                "pauses": self.pauses.between(begin),
                **(self.start_learned if covered else self.tracker.learned()),
                "people": self.start_state if covered else None}
        return now, json.dumps(meta), list(self.shown)

    @staticmethod
    def _write_report(path, head: str, recent: list, shown: list) -> int:
        """The report's file: the header, then the messages in the recordings' format with what the app
        showed in between; the number of messages."""
        lines = [head]
        shown = collections.deque(shown)
        for recv, topic, payload in recent:
            while shown and shown[0][0] <= recv:
                t, data = shown.popleft()
                lines.append(json.dumps({"t": t, "topic": "app/shown", "payload": data}))
            lines.append(json.dumps({"t": recv, "topic": topic, "payload": decoded(payload)}))
        lines += [json.dumps({"t": t, "topic": "app/shown", "payload": data}) for t, data in shown]
        # whole or not at all: a report cut off (a power cut) is no truth, and the list must not see one half written
        atomic_write(path, gzip.compress("\n".join(lines).encode(), compresslevel=6))
        return len(lines) - 1

    async def h_reports(self, request):
        out = []
        for path in sorted(self.reports.glob("*.jsonl.gz"), reverse=True) if self.reports.is_dir() else []:
            try:
                with gzip.open(path, "rt") as f:
                    rep = json.loads(f.readline())["report"]
            except (OSError, EOFError, ValueError, KeyError, TypeError):  # (EOFError: a gzip cut off)
                continue
            if not isinstance(rep, dict):
                continue
            out.append({"name": path.name, "size": path.stat().st_size, **rep})
        return web.json_response({"reports": out, "window_min": REPORT_WINDOW / 60})

    def _report_path(self, name: str) -> pathlib.Path:
        """The file of an existing report, by its name as listed (h_reports); anything else is 404: a
        plain name (letters, digits, - and _, as h_report makes them), no path, no link, no other file."""
        if not REPORT_NAME.fullmatch(name):
            raise web.HTTPNotFound()
        path = self.reports / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.reports.resolve():
            raise web.HTTPNotFound()
        return path

    async def h_report_file(self, request):
        name = request.match_info["name"]
        path = self._report_path(name)
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def h_delete_report(self, request):
        """A report deleted (made by mistake, or while sensors were offline: it is no truth)."""
        path = self._report_path(request.match_info["name"])
        path.unlink()
        log.info("error report %s deleted", path.name)
        return web.json_response({"ok": True, "name": path.name})

    # ------------------------------------------------------------ the recording

    async def h_recording(self, request):
        return web.json_response(self.recorder.status())

    async def h_recording_switch(self, request):
        if self.replay:  # a replay records nothing: it would only record itself
            return web.json_response({"error": "recording_replay"}, status=409)
        await self.recorder.switch((await request.json()).get("on") is True)
        return web.json_response(self.recorder.status())

    async def h_recording_delete(self, request):
        try:
            await self.recorder.delete()
        except OSError as e:
            log.exception("recordings not deleted")
            return web.json_response({"error": "recording_not_deleted", "detail": str(e)}, status=500)
        return web.json_response(self.recorder.status())

    async def h_recording_download(self, request):
        """All recordings as one tar, streamed (recording.py); no name in the request, nothing to choose but them."""
        name = time.strftime("aufnahmen-%Y%m%d-%H%M.tar")
        response = web.StreamResponse(headers={"Content-Type": "application/x-tar",
                                               "Content-Disposition": f'attachment; filename="{name}"'})
        await response.prepare(request)
        await self.recorder.stream(response.write)
        await response.write_eof()
        return response

    async def h_reset_tracks(self, request):
        """"Tracks zurücksetzen": nothing known about where anybody is, the data decide again. The
        sensors' tracks start over too, as after lost data (MODEL.md 4.1): their next frames are a start,
        so whoever a sensor is tracking now counts. Until 0.18 their running tracks stayed and fed
        measurements to tracks the model no longer had: somebody sitting was gone for good, and the
        LD2410C learned them as background. That learns nothing for RESET_HOLD s now either."""
        def reset():
            tr = self.tracker
            tr.reset_people()
            tr.reset_tracks()
            tr.hold_background(RESET_HOLD)
            self._model_started(tr.now)
        await self.in_model(reset)
        return web.json_response({"ok": True})

    # -------------------------------------------------- Home Assistant (Dobby)

    async def _ha_get(self, path: str, binary: bool = False):
        if not self.ha_url:
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "no_ha"}), content_type="application/json")
        headers = {"Authorization": f"Bearer {self.ha_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(self.ha_url + path, headers=headers) as r:
                if r.status != 200:
                    raise web.HTTPBadGateway(text=json.dumps({"error": "ha_http", "detail": r.status}),
                                             content_type="application/json")
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
