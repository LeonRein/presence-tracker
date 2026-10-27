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
for more than SILENT_AFTER = 30 s has no data. Home Assistant gets its entities as unavailable, not as
empty (ha.py): with no data the app says "I don't know". 30 s, not LOST (6 s, when the filter ends its
tracks): in 64 h of recordings 6-20 s gaps of one board while the others sent came 88 times (the WLAN),
each would have made its room unavailable for seconds; a board that is gone stays silent for minutes. A sensor that sent nothing since the model
started counts from the start: at the app's start, before the first frames, nothing is silent.

Only output: nothing here changes the filter's state, weights or what it learns. The rule runs in the core
(room_seen.rs; filter.Tracker.seen reads it); here what the shell still gives it."""

MOUNT_MARGIN = 0.3  # m: a sensor hangs on a wall, its point lies at the room's outline
SILENT_AFTER = 30.0  # s without any frame: the board is gone, its room is silent (not the WLAN's short gaps)


def own_room(s, zones) -> str | None:
    """The room of sensor s among the observed rooms (zones): named like the room's id, else like its
    name, else the one it is mounted in (within MOUNT_MARGIN), if exactly one. Until the plan is built
    in the core (phase 6) the shell gives the core this (filter.Tracker._export_plan)."""
    name = (s.name or "").strip()
    for z in zones:
        if name and z.id == name:
            return z.id
    for z in zones:
        if name and z.name.strip().casefold() == name.casefold():
            return z.id
    inside = [z.id for z in zones if z.contains(float(s.x), float(s.y), MOUNT_MARGIN)]
    return inside[0] if len(inside) == 1 else None
