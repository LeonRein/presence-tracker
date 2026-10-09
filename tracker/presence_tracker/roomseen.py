"""The room's own LD2450 measures somebody in it (MODEL.md 6, "Belegt"): an output rule beside the filter.

A room is occupied if P(somebody there) is above its threshold or the room's own LD2450 had a measured
target inside the room within the last seen_hold s. Measured: not held by the sensor (frozen, coasted:
sensortracks.py), not behind a wall or outside all rooms (frames.py), not at the mount (filter.MOUNT_RADIUS),
in short what the filter itself takes as a measurement (Detection: not hidden, not stale). Inside: the
first observed room whose outline holds the point is this room (as tools/baseline_eval.py counted it).

The room's own sensor: the one named like the room's id (the convention of the floor plan: sensor name
"kueche" for the room "kueche"), else like the room's name, else the one mounted in exactly one observed
room (0.3 m around its outline, it hangs on a wall). Other sensors don't count: from 6-7 m a person at a
room's edge is measured in the next room (MODEL.md 6).

Silent (MODEL.md 6, "Belegt"): a room whose own sensors in use (enabled and placed) all sent no frame
for more than SILENT_AFTER = sensortracks.LOST s has no data. Home Assistant gets its entities as unavailable, not as
empty (ha.py): with no data the app says "I don't know". LOST, not filtermodel.IDLE (both 6 s): LOST is
the filter's "this sensor has lost its data" now, without waiting for its next frame (Tracker._end_silent
ends its tracks then); IDLE judges a gap between two frames. A sensor that sent nothing since the model
started counts from the start: at the app's start, before the first frames, nothing is silent.

Only output: nothing here changes the filter's state, weights or what it learns."""

from .sensortracks import LOST

MOUNT_MARGIN = 0.3  # m: a sensor hangs on a wall, its point lies at the room's outline
SILENT_AFTER = LOST  # s without any frame: the sensor has lost its data, its room is silent


class RoomSeen:
    def __init__(self, config):
        self.last: dict[str, float] = {}  # room id -> the last time its own LD2450 measured somebody in it
        self.heard: dict[str, float] = {}  # sensor id -> the time of its last frame (any)
        self.use(config)

    def use(self, config):
        """A new configuration: the observed rooms and which sensor is whose."""
        self.zones = list(config.observed_rooms())
        self.own = {}  # sensor id -> room id
        for s in config.sensors:
            room = self.own_room(s, self.zones)
            if room is not None:
                self.own[s.id] = room
        rooms = set(self.own.values())
        self.last = {r: t for r, t in self.last.items() if r in rooms}
        self.in_use = {s.id for s in config.sensors if s.enabled and s.placed}

    def watched(self) -> set:
        """The rooms with an own sensor in use: these may be silent (Home Assistant: their own availability)."""
        return {r for sid, r in self.own.items() if sid in self.in_use}

    def heard_from(self, sensor_id: str, t: float):
        """A frame of the sensor at t, whatever is in it (Tracker.process_frame, first thing)."""
        if t > self.heard.get(sensor_id, -float("inf")):
            self.heard[sensor_id] = t

    def silent(self, now: float, start: float | None, limit: float | None = None) -> set:
        """The rooms whose own sensors in use all sent no frame for more than limit s (default
        SILENT_AFTER); one that sent nothing since start (the model's start, None: not started, nothing
        silent) counts from start."""
        if start is None:
            return set()
        limit = SILENT_AFTER if limit is None else limit
        quiet: dict[str, bool] = {}
        for sid, room in self.own.items():
            if sid in self.in_use:
                quiet[room] = quiet.get(room, True) and now - self.heard.get(sid, start) > limit
        return {r for r, q in quiet.items() if q}

    @staticmethod
    def own_room(s, zones) -> str | None:
        name = (s.name or "").strip()
        for z in zones:
            if name and z.id == name:
                return z.id
        for z in zones:
            if name and z.name.strip().casefold() == name.casefold():
                return z.id
        inside = [z.id for z in zones if z.contains(float(s.x), float(s.y), MOUNT_MARGIN)]
        return inside[0] if len(inside) == 1 else None

    def frame(self, sensor, t: float, dets):
        """One frame of the sensor; dets as Tracker.process_frame marked them (hidden, stale)."""
        room = self.own.get(sensor.id)
        if room is None or not sensor.enabled or not sensor.placed:
            return
        for d in dets:
            if d.hidden or getattr(d, "stale", False):
                continue
            x, y = float(d.pos[0]), float(d.pos[1])
            z = next((z for z in self.zones if z.contains(x, y)), None)
            if z is not None and z.id == room:
                self.last[room] = t

    def seen(self, now: float, hold: float) -> set:
        """The rooms whose own LD2450 measured somebody in them within the last hold s (0: none)."""
        if not hold > 0:
            return set()
        return {r for r, t in self.last.items() if now - t <= hold}
