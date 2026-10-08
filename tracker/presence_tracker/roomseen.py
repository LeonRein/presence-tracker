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

Only output: nothing here changes the filter's state, weights or what it learns."""

MOUNT_MARGIN = 0.3  # m: a sensor hangs on a wall, its point lies at the room's outline


class RoomSeen:
    def __init__(self, config):
        self.last: dict[str, float] = {}  # room id -> the last time its own LD2450 measured somebody in it
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
