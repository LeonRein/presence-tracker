"""Echtzeit: the model in a thread of its own, the event loop only receives and publishes (DOCS.md "Echtzeit").

On 17.11. (0.65.0) the core took longer per frame than the frames' time. The frames waited in aiomqtt's
unbounded queue, 17:51-18:30 linearly up to 2547 s (42 min); the app published states 42 min old as fresh
("online"), the light came that late, and a restart dropped 80,386 waiting frames. Three things made it so:
- the model ran in the event loop: MQTT in, the core, Home Assistant out and the web UI waited for each other;
- a frame's receive time was taken when it was processed: the backlog was nowhere to be seen, and the
  frame times (the boards' uptimes, frames.SensorClock) kept the frames at their own time, so the model went
  on that far behind the clock without anybody knowing;
- nothing bounded the waiting frames.

Now:
- **Arrival.** A message's receive time is taken when aiomqtt queues it (StampedQueue): the time it came, not
  when it is processed. The backlog of a message is the time from then to the start of its processing.
- **The model's thread.** The model runs in its own thread (ModelThread) on its own bounded queue (Inbox).
  The core releases the GIL while it computes (presence-py core.rs: frame, step, zone_states): the loop
  receives, publishes and answers the web UI meanwhile. Everything that reads or changes the model goes
  through this thread (App.in_model): the core is never used by two threads at once.
- **Compaction.** When the oldest waiting frame waited more than COMPACT_AFTER s, or more than MAX_WAITING
  frames wait, the waiting frames are compacted (Inbox.compact): of each sensor only its newest frame stays,
  in its place; messages that are no frames (the switch "Lernen pausieren", calls of the web UI) all stay,
  in their order. Before the next message the core is told that data was lost (ModelThread: the app's
  `lost`, Tracker.reset_tracks).

Why this rule:
- The newest frame of each sensor brings the model back to now at once: what HA gets is at most one frame's
  computation behind the house, not the backlog. A backlog the core can't work off is lost anyway (a restart
  drops it, as on 17.11.); worked off late, it is worth nothing for the light.
- The core must not see the dropped frames as "nothing happened". A gap of a sensor up to 6 s counts as
  watched and empty (MODEL.md 4.4: no track begun meanwhile, the LD2410C with the energies of the frame before
  it): frames dropped silently would weigh everybody by measurements nobody made. A lost-data gap says
  nothing (MODEL.md 4.4, as a restart of the app, 5.3): the people are moved over it, not weighed. The core
  takes that from Tracker.reset_tracks (the core's Tracker::reset_tracks, unchanged): every sensor's next
  frame is a start (its time since the last frame is not watched, sensors.rs `live`; who has no track then
  was not tracked, `censor`), the live tracks end as for lost data (`end_silent`: data_lost, their end says
  nothing about a ghost's life), and the time between counts as watched by no sensor.
- All sensors, not only some: the backlog is the app's, all sensors' frames waited alike; and reset_tracks
  is the core's one way to say "lost" without changing the core.
- The LD2410C: its gathering takes a gap of more than 6 s (IDLE) as lost data (one frame, ld2410/sensor.rs).
  COMPACT_AFTER = 10 s is longer: of a sensor that sends (10-11 Hz) the gap between its last processed frame
  and its newest is more than 9.9 s. Only a sensor that sends nothing but its 5-s heartbeat can have a gap of
  5-6 s, which the LD2410C fills with the energies of the frame before (what a heartbeat says: nothing changed).
- Never in normal operation: the backlog of 0.64 on Inge is below 1.2 s (17.11. up to 16:38), the longest frame
  of 0.65 in its worst minutes 1.1 s. 10 s is a model that can no longer keep up.
- Not in a replay: it waits for its frames (sources.ReplayClock) and runs in the loop as before, every frame.
"""

import asyncio
import collections
import contextlib
import logging
import threading
import time

log = logging.getLogger(__name__)

COMPACT_AFTER = 10.0  # s the oldest waiting frame may wait; then the waiting frames are compacted
# frames waiting at most (about 75 s of 6 sensors at 11 Hz: a broker that held them 35 s, 18.10., hands them
# over all at once, at their arrival they are fresh); more are compacted at once, whatever their age
MAX_WAITING = 5000
STAMPED_LIMIT = 20000  # frames in aiomqtt's queue at most (the loop takes them at once; only if it hangs)
TICK = 0.1  # s between the model's steps and outputs (wall clock), as the housekeeping did

FRAME, PAUSE, CALL = "frame", "pause", "call"


class Item:
    """A message for the model: kind (FRAME, PAUSE, CALL), sensor (frames), payload (a Frame, the switch's
    payload, (function, args, done) of a call), recv (its receive time, Unix: the model's), queued (when it
    came, time.monotonic: the backlog's)."""

    __slots__ = ("kind", "sensor", "payload", "recv", "queued")

    def __init__(self, kind: str, payload, recv: float = 0.0, queued: float | None = None, sensor: str | None = None):
        self.kind = kind
        self.sensor = sensor
        self.payload = payload
        self.recv = recv
        self.queued = time.monotonic() if queued is None else queued


class StampedQueue(asyncio.Queue):
    """aiomqtt's queue of incoming messages (Client(queue_type=...)): each message with when it came
    ((time.time(), time.monotonic(), message)), taken when aiomqtt queues it, not when the loop gets to it.
    Frames beyond STAMPED_LIMIT are dropped and counted (`lost`: the model is told, Inbox.mark_lost); other
    messages always go in. aiomqtt's own bound would drop any message, also the switch, without saying so."""

    def __init__(self, maxsize: int = 0):
        super().__init__(0)
        self.limit = maxsize or STAMPED_LIMIT
        self.lost = 0

    def put_nowait(self, item):
        if self.qsize() >= self.limit and str(getattr(item, "topic", "")).endswith("/frame"):
            self.lost += 1
            return
        super().put_nowait((time.time(), time.monotonic(), item))


class Load:
    """What the model's thread did, for the diagnostics (DOCS.md "Echtzeit"): frames come (arrived), done,
    dropped by a compaction; the backlog per message (max of the window); the thread's CPU. Counted in both
    threads, read by the loop: under a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self.arrived = 0  # frames that came (in total)
        self.done = 0  # frames the model processed
        self.dropped = 0  # frames dropped (compaction, aiomqtt's queue full)
        self.compactions = 0
        self.backlog = 0.0  # of the last processed message
        self.cpu = 0.0  # s of the model thread's CPU (time.thread_time), as of its last item or tick
        self._window = None  # (monotonic, done, arrived, cpu) at the window's start
        self._backlog_max = 0.0

    def came(self):
        with self._lock:
            self.arrived += 1

    def processed(self, backlog: float, frame: bool):
        with self._lock:
            self.backlog = backlog
            self._backlog_max = max(self._backlog_max, backlog)
            if frame:
                self.done += 1

    def compacted(self, dropped: int):
        with self._lock:
            self.dropped += dropped
            self.compactions += 1

    def window(self, now: float | None = None) -> dict:
        """Since the last call: frames/s processed and arrived, the CPU (% of one core), the highest backlog
        of a message (s); the totals. The first call starts the window (rates 0)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            start = self._window or (now, self.done, self.arrived, self.cpu)
            dt = max(now - start[0], 1e-6)
            out = {"fps": (self.done - start[1]) / dt if self._window else 0.0,
                   "fps_in": (self.arrived - start[2]) / dt if self._window else 0.0,
                   "cpu": 100.0 * (self.cpu - start[3]) / dt if self._window else 0.0,
                   "backlog_max": self._backlog_max, "dropped": self.dropped, "compactions": self.compactions}
            self._window = (now, self.done, self.arrived, self.cpu)
            self._backlog_max = 0.0
        return out


class Inbox:
    """The model's queue: messages in arrival order, calls (App.in_model) before them. Bounded by the
    compaction (module doc): when the oldest waiting frame waited more than `after` s or more than `limit`
    frames wait, only the newest frame of each sensor stays. That data was lost is kept (`lost`) until the
    model takes it with the next message (get)."""

    def __init__(self, load: Load | None = None, after: float | None = None, limit: int | None = None):
        self.load = load or Load()
        self.after = COMPACT_AFTER if after is None else after
        self.limit = MAX_WAITING if limit is None else limit
        self._cond = threading.Condition()
        self._items: collections.deque = collections.deque()  # messages, in arrival order
        self._calls: collections.deque = collections.deque()
        self._frames = 0  # frames among _items
        self._lost = 0  # frames dropped since the model was last told
        self.waiting_max = 0  # the most frames that ever waited (diagnostics, tests)

    def __len__(self):
        with self._cond:
            return len(self._items) + len(self._calls)

    @property
    def frames(self) -> int:
        return self._frames

    def put(self, item: Item):
        """A message, in arrival order (the loop)."""
        with self._cond:
            if item.kind == CALL:
                self._calls.append(item)
            else:
                self._items.append(item)
                if item.kind == FRAME:
                    self._frames += 1
                    self.load.came()
                    if self._frames > self.limit:
                        self._compact()
                    self.waiting_max = max(self.waiting_max, self._frames)
            self._cond.notify()

    def put_ordered(self, item: Item):
        """A call after the messages that wait now (not before them, as put does): the tools' replay through
        the thread (it ticks at the frames' times)."""
        with self._cond:
            self._items.append(item)
            self._cond.notify()

    def mark_lost(self, n: int):
        """n frames were lost before they came here (StampedQueue full): told to the model as a compaction's."""
        if n > 0:
            with self._cond:
                self._lost += n
            self.load.compacted(n)

    def wake(self):
        with self._cond:
            self._cond.notify_all()

    def oldest(self) -> float | None:
        """When the oldest waiting frame came (monotonic; None: none waits)."""
        with self._cond:
            return self._oldest()

    def _oldest(self) -> float | None:
        for item in self._items:
            if item.kind == FRAME:
                return item.queued
        return None

    def get(self, timeout: float) -> tuple:
        """The next message (calls first) and the frames lost since the last get (> 0: the model is told
        before this message); (None, lost) if nothing came within timeout s."""
        with self._cond:
            if not (self._items or self._calls):
                self._cond.wait(timeout)
            oldest = self._oldest()
            if oldest is not None and time.monotonic() - oldest > self.after:
                self._compact()
            item = None
            if self._calls:
                item = self._calls.popleft()
            elif self._items:
                item = self._items.popleft()
                if item.kind == FRAME:
                    self._frames -= 1
            lost, self._lost = (self._lost, 0) if item is not None else (0, self._lost)
            return item, lost

    def compact(self) -> int:
        with self._cond:
            return self._compact()

    def _compact(self) -> int:
        """Of each sensor's waiting frames only the newest stays, in its place; the rest stays in its order.
        Returns how many frames were dropped."""
        newest = {}
        for k, item in enumerate(self._items):
            if item.kind == FRAME:
                newest[item.sensor] = k
        keep = set(newest.values())
        dropped = self._frames - len(keep)
        if dropped <= 0:
            return 0
        self._items = collections.deque(item for k, item in enumerate(self._items)
                                        if item.kind != FRAME or k in keep)
        self._frames = len(keep)
        self._lost += dropped
        self.load.compacted(dropped)
        return dropped


class ModelThread:
    """The model's thread (module doc): takes the Inbox's messages one by one (process(item, backlog)), tells
    the model of lost data before the next message (lost(n)), and every TICK s (wall clock) moves it on and
    takes its outputs (tick(): what the loop publishes, kept in `outputs`). A call (App.in_model) runs here
    between two messages. An exception in process, tick or lost is logged, the thread goes on (the app's own
    handlers start the model over)."""

    def __init__(self, process, tick, lost, inbox: Inbox | None = None, every: float | None = None):
        self.load = inbox.load if inbox is not None else Load()
        self.inbox = inbox or Inbox(self.load)
        self._process, self._tick, self._lost = process, tick, lost
        self.every = TICK if every is None else every
        self.outputs = None  # the last tick's outputs (set here, read by the loop: one assignment)
        self.busy = None  # (monotonic start, kind) of the message or tick in progress, None while waiting
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="model", daemon=True)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        self.inbox.wake()

    def join(self, timeout: float | None = None) -> bool:
        """Waits for the thread to end; whether it did."""
        if self._thread.ident is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def put(self, item: Item):
        self.inbox.put(item)

    def backlog(self) -> float:
        """How long the oldest message not yet done has waited (s): the one in progress or the oldest waiting
        frame; 0 if none. Grows also while the core hangs in one frame."""
        now = time.monotonic()
        waits = [t for t in (self.inbox.oldest(), self._busy_queued) if t is not None]
        return max(0.0, now - min(waits)) if waits else 0.0

    _busy_queued = None  # when the message in progress came (monotonic)

    async def call(self, fn, *args, ordered: bool = False):
        """fn(*args) in the model's thread, between two messages (before the waiting ones; ordered: after
        them); its result or exception."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def settle(ok, value):
            if not fut.done():
                (fut.set_result if ok else fut.set_exception)(value)

        def done(ok, value):
            with contextlib.suppress(RuntimeError):  # the loop closed meanwhile (the app stops)
                loop.call_soon_threadsafe(settle, ok, value)

        item = Item(CALL, (fn, args, done))
        (self.inbox.put_ordered if ordered else self.inbox.put)(item)
        return await fut

    def _run(self):
        next_tick = time.monotonic()
        while not self._stop:
            item, lost = self.inbox.get(max(0.0, next_tick - time.monotonic()))
            if self._stop:
                break
            if lost:
                self._guarded(self._lost, lost)
            if item is not None:
                self._handle(item)
            now = time.monotonic()
            if now >= next_tick:
                next_tick = now + self.every
                self.busy = (now, "tick")
                out = self._guarded(self._tick)
                self.busy = None
                if out is not None:
                    self.outputs = out
                self.load.cpu = time.thread_time()
        # a call still waiting: it gets an error, not silence
        while True:
            item, _ = self.inbox.get(0.0)
            if item is None:
                break
            if item.kind == CALL:
                item.payload[2](False, RuntimeError("the model's thread stopped"))

    def _handle(self, item: Item):
        start = time.monotonic()
        self.busy, self._busy_queued = (start, item.kind), item.queued
        try:
            if item.kind == CALL:
                fn, args, done = item.payload
                try:
                    value = fn(*args)
                except BaseException as e:  # noqa: BLE001 - goes to the caller
                    done(False, e)
                else:
                    done(True, value)
            else:
                backlog = start - item.queued
                self._guarded(self._process, item, backlog)
                self.load.processed(backlog, item.kind == FRAME)
        finally:
            self.busy, self._busy_queued = None, None
            self.load.cpu = time.thread_time()

    @staticmethod
    def _guarded(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 - the thread is the model's only one: it must go on
            log.exception("model thread: %s failed", getattr(fn, "__name__", fn))
            return None
