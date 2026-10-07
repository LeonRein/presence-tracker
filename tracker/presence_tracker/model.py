"""Configuration: map, sensors, zones and tracker parameters. Stored as one JSON file.

All lengths in meters, angles in degrees, in the house frame (the axes of the Dobby map).
"""

import json
import math
import pathlib
from dataclasses import asdict, dataclass, field, fields

from .floorplan import sight_segments, sync_rooms, wall_pieces
from .geometry import Shape, distance_to_segment, line_of_sight


SIGHT_OFFSET = 0.2  # m, see SensorConfig.sight_origin

@dataclass
class SensorConfig:
    id: str  # ESPHome name, also the MQTT topic: presence/<id>/frame
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0  # direction the sensor looks, degrees from the +x axis, counterclockwise
    height: float = 1.5  # mounting height above the floor
    mirror: bool = False  # LD2450 x axis points to the left instead of the right
    scale: float = 1.0  # true distance on the floor / measured one (from the calibration)
    fov: float = 120.0
    range: float = 6.0
    enabled: bool = True
    placed: bool = False  # set once the user has put it on the map

    def __post_init__(self):
        self._update()

    def _update(self):
        h = math.radians(self.heading)
        self._cos, self._sin = math.cos(h), math.sin(h)

    def to_world(self, lx: float, ly: float, target_height: float) -> tuple:
        """LD2450 target (m, sensor frame) -> house frame. Returns (x, y, ground_range, slant_range).

        The radar measures the slant distance from its antenna; the height difference to the
        reflecting body is taken out so that the position lies on the floor plane. Then the
        sensor's scale corrects the distance.
        """
        if self.mirror:
            lx = -lx
        slant = math.hypot(lx, ly)
        dh = self.height - target_height
        ground = math.sqrt(max(slant * slant - dh * dh, 0.01))
        k = self.scale * ground / slant if slant > 0 else self.scale
        gx, gy = lx * k, ly * k
        # sensor frame: y forward along the heading, x to the right
        wx = self.x + gy * self._cos + gx * self._sin
        wy = self.y + gy * self._sin - gx * self._cos
        return wx, wy, self.scale * ground, self.scale * slant

    def to_local(self, wx: float, wy: float) -> tuple:
        """House frame -> sensor frame on the floor plane (x right, y forward), as the sensor
        measures it (scale taken out)."""
        dx, dy = (wx - self.x) / self.scale, (wy - self.y) / self.scale
        gy = dx * self._cos + dy * self._sin
        gx = dx * self._sin - dy * self._cos
        return (-gx if self.mirror else gx), gy

    def in_fov(self, wx: float, wy: float, margin: float = 0.0) -> bool:
        lx, ly = self.to_local(wx, wy)
        r = math.hypot(lx, ly)
        if r > self.range + margin or ly <= 0:
            return False
        return abs(math.degrees(math.atan2(lx, ly))) <= self.fov / 2 + math.degrees(margin / max(r, 0.3))

    def sight_origin(self) -> tuple:
        """Where sight lines start: 20 cm in front of the sensor. It hangs on a wall (often in a
        corner), and a few centimeters of drawing or calibration error would otherwise put it
        behind that wall, blind for everything."""
        return (self.x + SIGHT_OFFSET * self._cos, self.y + SIGHT_OFFSET * self._sin)

    def sees(self, wx: float, wy: float, walls: list, margin: float = 0.0) -> bool:
        return self.in_fov(wx, wy, margin) and line_of_sight(self.sight_origin(), (wx, wy), walls)


@dataclass
class ZoneConfig:
    id: str
    name: str
    kind: str = "area"  # room | area | entry
    shape: str = "rect"  # rect | circle | polygon
    points: list = field(default_factory=list)
    center: list | None = None
    radius: float = 0.0
    anchor: list | None = None  # rooms from walls: a point inside, identifies the room after edits
    entry: bool = False  # rooms: outside the home, the public space beyond its door (stairwell; MODEL.md 2)

    def __post_init__(self):
        self.geometry = Shape(self.shape, self.points, self.center, self.radius)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return self.geometry.contains(x, y, margin)

    def to_dict(self) -> dict:
        d = {k.name: getattr(self, k.name) for k in fields(self)}
        if self.shape != "circle":
            d.pop("center")
            d.pop("radius")
        if self.kind != "room":
            d.pop("anchor")
            d.pop("entry")
        return d


@dataclass
class TrackerParams:
    target_height: float = 1.0  # height of the reflecting body (chest), for the slant correction
    # LD2450 measurement error (1 sigma) = base + slope * distance on the floor; values from a
    # sweep over the 2026-10-03 recordings
    range_sigma_base: float = 0.15  # m, along the line of sight
    range_sigma_slope: float = 0.02
    lateral_sigma_base: float = 0.10  # m, across it
    lateral_sigma_slope: float = 0.05
    wall_margin: float = 0.4  # m, detections farther behind a wall or outside all rooms are reflections
    # rooms without a sensor, see unobserved.py (assumed)
    dwell_median: float = 120.0  # s, typical stay in such a room
    dwell_spread: float = 1.5  # spread of ln(duration), wide: long stays stay possible
    # LD2410C
    ld2410_hold: float = 1.5  # s, gaps in the LD2410C presence up to this long are bridged (display)
    # outputs
    light_cost: float = 2.0  # a second of light without anybody costs as much as this many seconds dark
                             # with somebody there (assumed; Leon: light without a person is the worst)
    # "wird betreten" (MODEL.md 6): P(a walker enters within lead_time) above approach_cost / (approach_cost
    # + 1); approach_cost: a light switched on in vain costs as much as this many entries into a dark room.
    # 2 s and 0.05 (measured 6./7.10., MODEL.md 6): light >= 1 s / 1 m before 60 / 62 % of the entries, 4.0
    # vain 30-s lights per hour; Leon's trial of "1 s / 1 m ahead"
    lead_time: float = 2.0  # s
    approach_cost: float = 0.05
    # "Ziel" (MODEL.md 6): somebody walking goes into the room next, from their motion and the learned
    # map of where walks went; on at P >= the room's threshold c = K_false / (K_false + K_late) (a light
    # switched on in vain against an entry into a dark room). 0.8: a vain light costs as much as 4 late
    # ones. Per room (room id -> threshold) where it differs; where nothing is learned, "Ziel" is "wird
    # betreten" whatever the threshold
    target_threshold: float = 0.8
    target_thresholds: dict = field(default_factory=dict)

# What the web UI may set (PUT /api/config, Config.from_dict(check=True)) and what its fields accept:
# key -> (label, low, high, low excluded, high excluded). A value outside is a typo, not a setting:
# light_cost 0 (an emptied field) put every room above its threshold, every light on.
PARAM_LIMITS = {
    "target_height": ("Höhe des Oberkörpers", 0.0, 2.5, False, False),
    "range_sigma_base": ("Messfehler in Blickrichtung, Grundwert", 0.0, 2.0, True, False),
    "range_sigma_slope": ("Messfehler in Blickrichtung, Anstieg", 0.0, 1.0, False, False),
    "lateral_sigma_base": ("Messfehler seitlich, Grundwert", 0.0, 2.0, True, False),
    "lateral_sigma_slope": ("Messfehler seitlich, Anstieg", 0.0, 1.0, False, False),
    "wall_margin": ("Toleranz an Wänden", 0.0, 2.0, False, False),
    "dwell_median": ("Typischer Aufenthalt", 0.0, 7 * 86400.0, True, False),  # ln(dwell_median)
    "dwell_spread": ("Streuung des Aufenthalts", 0.0, 10.0, True, False),  # divides
    "ld2410_hold": ("Haltezeit LD2410C", 0.0, 60.0, False, False),
    "light_cost": ("Kosten: Licht ohne Person", 0.0, 1000.0, True, False),  # threshold c / (c + 1) > 0
    "lead_time": ("Vorausschau „wird betreten“", 0.0, 5.0, True, False),
    "approach_cost": ("Kosten: Einschalten auf Verdacht", 0.0, 1000.0, True, False),
    "target_threshold": ("Schwelle „Ziel“", 0.0, 1.0, True, True),
}
SENSOR_LIMITS = {
    "x": ("x", -1000.0, 1000.0, False, False),
    "y": ("y", -1000.0, 1000.0, False, False),
    "heading": ("Blickrichtung", -720.0, 720.0, False, False),
    "height": ("Montagehöhe", 0.1, 4.0, False, False),
    "fov": ("Öffnungswinkel", 0.0, 180.0, True, False),
    "range": ("Reichweite", 0.0, 20.0, True, False),
    "scale": ("Maßstab", 0.25, 4.0, False, False),
}
DOOR_LIMITS = {"width": ("Breite der Tür", 0.0, 20.0, True, False)}
LAYER_LIMITS = {
    "x": ("x des Bildes", -1e4, 1e4, False, False),
    "y": ("y des Bildes", -1e4, 1e4, False, False),
    "scale": ("Maßstab des Bildes", 0.0, 1.0, True, False),
    "rotation": ("Drehung des Bildes", -3600.0, 3600.0, False, False),
    "opacity": ("Deckkraft des Bildes", 0.0, 1.0, False, False),
}


def limits_dict() -> dict:
    """The limits for the web UI: per group key -> [low, high, low excluded, high excluded]."""
    groups = {"params": PARAM_LIMITS, "sensor": SENSOR_LIMITS, "door": DOOR_LIMITS, "layer": LAYER_LIMITS}
    return {g: {k: list(v[1:]) for k, v in lim.items()} for g, lim in groups.items()}


def _num(v) -> str:
    return f"{v:g}".replace(".", ",") if isinstance(v, (int, float)) and not isinstance(v, bool) else repr(v)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_value(where: str, v, lim):
    label, lo, hi, lo_open, hi_open = lim
    if v is None or v == "":
        raise ValueError(f"{where}{label}: leer, eine Zahl fehlt.")
    if not _finite(v):
        raise ValueError(f"{where}{label}: keine Zahl ({_num(v)}).")
    if v < lo or v > hi or (lo_open and v == lo) or (hi_open and v == hi):
        low = f"größer als {_num(lo)}" if lo_open else f"mindestens {_num(lo)}"
        high = f"kleiner als {_num(hi)}" if hi_open else f"höchstens {_num(hi)}"
        raise ValueError(f"{where}{label} muss {low} und {high} sein, nicht {_num(v)}.")


def _check_points(where: str, pts):
    if not isinstance(pts, list) or not all(isinstance(p, (list, tuple)) and len(p) == 2
                                            and all(_finite(c) for c in p) for p in pts):
        raise ValueError(f"{where}: Punkte sind keine endlichen Koordinaten.")


def check_config(d: dict):
    """Rejects what the model can't run with (ValueError with a German message for the UI): the
    parameters and sensor values outside PARAM_LIMITS / SENSOR_LIMITS, coordinates that are no finite
    numbers. Only what is present is checked; what is missing takes its default."""
    if not isinstance(d, dict):
        raise ValueError("Konfiguration ist kein Objekt.")
    params = d.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("Einstellungen sind kein Objekt.")
    for k, lim in PARAM_LIMITS.items():
        if k in params:
            _check_value("", params[k], lim)
    per_room = params.get("target_thresholds") or {}
    if not isinstance(per_room, dict):
        raise ValueError("Schwelle „Ziel“ je Raum ist kein Objekt.")
    for room, v in per_room.items():
        _check_value(f"Raum {room}: ", v, PARAM_LIMITS["target_threshold"])
    for s in d.get("sensors") or []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"]:
            raise ValueError("Sensor ohne ID.")
        where = f"Sensor {s.get('name') or s['id']}: "
        for k, lim in SENSOR_LIMITS.items():
            if k in s:
                _check_value(where, s[k], lim)
        for k in ("mirror", "enabled", "placed"):
            if k in s and not isinstance(s[k], bool):
                raise ValueError(f"{where}{k} ist nicht ja/nein.")
    for i, w in enumerate(d.get("walls") or []):
        _check_points(f"Wand {i + 1}", w.get("points") if isinstance(w, dict) else None)
    for door in d.get("doors") or []:
        if not isinstance(door, dict) or not (_finite(door.get("x")) and _finite(door.get("y"))):
            raise ValueError("Tür ohne endliche Position.")
        if "width" in door:
            _check_value("", door["width"], DOOR_LIMITS["width"])
    for z in d.get("zones") or []:
        if not isinstance(z, dict):
            raise ValueError("Zone ist kein Objekt.")
        name = z.get("name") or z.get("id")
        if "points" in z:
            _check_points(f"Zone {name}", z["points"])
        if z.get("center") is not None:
            _check_points(f"Zone {name}", [z["center"]])
        if "radius" in z and not (_finite(z["radius"]) and z["radius"] >= 0):
            raise ValueError(f"Zone {name}: Radius ist keine Zahl ≥ 0.")
    for layer in (d.get("background") or {}).get("layers") or []:
        for k, lim in LAYER_LIMITS.items():
            if k in layer:
                _check_value("", layer[k], lim)


@dataclass
class Config:
    sensors: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    walls: list = field(default_factory=list)  # [{"points": [a, b], "kind": "wall" | "divider"}], one segment each
    doors: list = field(default_factory=list)  # [{"id", "x", "y", "width"}] on a wall
    background: dict = field(default_factory=dict)  # image placement, see web UI
    params: TrackerParams = field(default_factory=TrackerParams)

    def __post_init__(self):
        self.rebuild()

    def rebuild(self):
        """Recompute derived data after an edit."""
        for s in self.sensors:
            s._update()
        self.wall_segments = sight_segments(self.walls, self.doors)
        self.sensor_by_id = {s.id: s for s in self.sensors}
        self._places()

    def _places(self):
        """The places of the people model (MODEL.md 2), derived from the plan, not stored.

        Rooms marked as entry (the stairwell) are outside: public space beyond the flat's door, no
        part of the home. A door into one is a door to the outside, like a door in an outer wall.
        The other rooms that no sensor covers are grouped into regions connected by doors. A region
        is *open* if it has ways out (doors to the outside, entry zones drawn in it; "exits" counts
        them), otherwise *closed* (balcony, kitchen with a single door): whoever comes out of a
        closed region must have gone in before.
        Sets self.regions {id: {name, rooms, open, exits}}; self.portals, all ways out of the
        observed area (circles with .region: a region id or "outside", .closed, .watch);
        self.closed_doors; self.outside_rooms; self.closed_rooms, the rooms in which the sensors see
        nobody of the home (those of closed regions, and the outside rooms): what shows up there is
        a reflection or nobody of the home."""
        self.regions, self.closed_doors, self.closed_rooms, self.portals = {}, [], [], []
        self.outside_rooms = [z for z in self.zones_of("room") if z.entry]
        placed = [s for s in self.sensors if s.enabled and s.placed]
        rooms = [z for z in self.zones_of("room") if not z.entry]
        if not placed or not rooms:
            self.closed_rooms = list(self.outside_rooms)
            return
        covered = {z.id: self._room_covered(z, placed) for z in rooms}
        pieces = wall_pieces(self.walls)
        walls = [p for p in pieces if p[2] == "wall"]

        def sides(c, piece):
            """The rooms on both sides of a door or divider; None: outside (no room, or an outside room)."""
            (ax, ay), (bx, by), _ = piece
            length = math.hypot(bx - ax, by - ay)
            nx, ny = -(by - ay) / length, (bx - ax) / length
            return [next((z for z in rooms if z.contains(c[0] + k * nx, c[1] + k * ny)), None) for k in (0.4, -0.4)]

        links = []  # ([room or None, room or None], door or None)
        normals = {}  # id(door) -> normal of its wall, pointing to the first side
        for d in self.doors:
            c = (d["x"], d["y"])
            piece = min(walls, key=lambda p: distance_to_segment(c[0], c[1], p[0], p[1]), default=None)
            if piece is not None:
                links.append((sides(c, piece), d))
                (ax, ay), (bx, by), _ = piece
                length = math.hypot(bx - ax, by - ay)
                normals[id(d)] = (-(by - ay) / length, (bx - ax) / length)
        for piece in pieces:
            if piece[2] == "divider":
                (ax, ay), (bx, by), _ = piece
                links.append((sides(((ax + bx) / 2, (ay + by) / 2), piece), None))

        parent = {z.id: z.id for z in rooms if not covered[z.id]}

        def find(r):
            while parent[r] != r:
                r = parent[r]
            return r
        for (a, b), _ in links:
            if a is not None and b is not None and a.id in parent and b.id in parent:
                parent[find(a.id)] = find(b.id)

        groups = {}
        for rid in parent:
            groups.setdefault(find(rid), []).append(rid)
        by_id = {z.id: z for z in rooms}
        user_entries = self.zones_of("entry")

        def middle(z):
            x0, y0, x1, y1 = z.geometry.bounds()
            return (x0 + x1) / 2, (y0 + y1) / 2
        for root, members in groups.items():
            exits = sum(1 for (a, b), _ in links if (a is None) != (b is None) and (a or b).id in members)
            exits += sum(1 for z in user_entries if any(by_id[r].contains(*middle(z)) for r in members))
            self.regions[root] = {"name": " + ".join(by_id[r].name for r in sorted(members)), "rooms": sorted(members),
                                  "open": exits > 0, "exits": exits}

        self.closed_rooms = [by_id[r] for region in self.regions.values() if not region["open"] for r in region["rooms"]]
        self.closed_rooms += self.outside_rooms
        for (a, b), d in links:
            if d is None:
                continue
            inner = [r for r in (a, b) if r is not None and covered[r.id]]
            other = [r for r in (a, b) if r is None or not covered[r.id]]
            if len(inner) != 1 or len(other) != 1:
                continue
            region = "outside" if other[0] is None else find(other[0].id)
            zone = ZoneConfig(f"door-{d['id']}", "Tür", kind="entry", shape="circle", center=[d["x"], d["y"]],
                              radius=max(1.2, d.get("width", 0.9) / 2 + 0.5))
            zone.region = region
            # a point 0.5 m into the observed room: if a sensor sees it, it sees people at the door
            (nx, ny) = normals[id(d)]
            k = 0.5 if a is inner[0] else -0.5
            zone.watch = (d["x"] + k * nx, d["y"] + k * ny)
            zone.center = (d["x"], d["y"])
            zone.closed = region != "outside" and not self.regions[region]["open"]
            self.portals.append(zone)
            if zone.closed:
                self.closed_doors.append(zone)
        # entry zones drawn in the observed area lead outside (in a region they make it open)
        unseen = [by_id[r] for region in self.regions.values() for r in region["rooms"]]
        for z in user_entries:
            if any(r.contains(*middle(z)) for r in unseen):
                continue
            x0, y0, x1, y1 = z.geometry.bounds()
            portal = ZoneConfig(f"entry-{z.id}", z.name, kind="entry", shape="circle",
                                center=[(x0 + x1) / 2, (y0 + y1) / 2],
                                radius=max(0.5 * math.hypot(x1 - x0, y1 - y0) + 0.3, 1.0))
            portal.region, portal.closed, portal.watch = "outside", False, (portal.center[0], portal.center[1])
            self.portals.append(portal)

    def _room_covered(self, room, sensors) -> bool:
        """Sensors see most of the room. Seeing a part through a door (31 % of the balcony
        through the glass door) doesn't make it observed."""
        x0, y0, x1, y1 = room.geometry.bounds()
        n = seen = 0
        y = y0 + 0.2
        while y < y1:
            x = x0 + 0.2
            while x < x1:
                if room.contains(x, y):
                    n += 1
                    seen += any(s.sees(x, y, self.wall_segments) for s in sensors)
                x += 0.4
            y += 0.4
        return n > 0 and seen / n >= 0.6

    def closed_region_at(self, x: float, y: float) -> str | None:
        """The closed unobserved region at this point: in one of its rooms or at its door."""
        for z in self.closed_doors:
            if z.contains(x, y):
                return z.region
        for rid, region in self.regions.items():
            if not region["open"] and any(z.id in region["rooms"] and z.contains(x, y) for z in self.zones_of("room")):
                return rid
        return None

    def door_of(self, region: str):
        return next((z for z in self.closed_doors if z.region == region), None)

    def zones_of(self, kind: str) -> list:
        return [z for z in self.zones if z.kind == kind]

    def home_zones(self, *kinds) -> list:
        """The zones of these kinds that belong to the home: not the outside rooms (the stairwell,
        MODEL.md 2), which get no count, no state and no entity."""
        return [z for z in self.zones if z.kind in kinds and not (z.kind == "room" and z.entry)]

    def observed_rooms(self) -> list:
        """The rooms of the home that the sensors see (in no region without a sensor)."""
        unseen = {r for region in self.regions.values() for r in region["rooms"]}
        return [z for z in self.home_zones("room") if z.id not in unseen]

    def hidden(self, s: SensorConfig, pos, u, margin: float) -> bool:
        """The radar can't see through the (concrete) walls: a point more than `margin` behind
        one, outside all rooms or inside a closed room without a sensor is a reflection.
        u: unit vector from the sensor to the point."""
        rooms = self.zones_of("room")
        if rooms and not any(z.contains(pos[0], pos[1], margin) for z in rooms):
            return True
        # a closed room without a sensor (balcony) is not observed, not even through its glass
        # door: what shows up in there is a reflection; people are counted at the door instead
        if any(z.contains(pos[0], pos[1], -margin) for z in self.closed_rooms):
            return True
        back = (float(pos[0] - u[0] * margin), float(pos[1] - u[1] * margin))
        return not line_of_sight(s.sight_origin(), back, self.wall_segments)

    def visible_sensors(self, x: float, y: float, margin: float = 0.0) -> list:
        return [s for s in self.sensors if s.enabled and s.placed and s.sees(x, y, self.wall_segments, margin)]

    # --- persistence ---

    def to_dict(self) -> dict:
        return {
            "sensors": [{k.name: getattr(s, k.name) for k in fields(s)} for s in self.sensors],
            "zones": [z.to_dict() for z in self.zones],
            "walls": self.walls,
            "doors": self.doors,
            "background": self.background,
            "params": asdict(self.params),
        }

    @classmethod
    def from_dict(cls, d: dict, check: bool = False) -> "Config":
        """check: reject values outside the limits (check_config), as for an edit in the web UI; a
        stored configuration loads as it is."""
        if check:
            check_config(d)

        def pick(dc, data):
            names = {f.name for f in fields(dc)}
            return dc(**{k: v for k, v in data.items() if k in names})

        walls = [{"points": [list(p) for p in w["points"]], "kind": w.get("kind", "wall")} for w in d.get("walls", [])]
        # rooms are the closed areas between the walls
        zones = sync_rooms(d.get("zones", []), walls)
        return cls(
            sensors=[pick(SensorConfig, s) for s in d.get("sensors", [])],
            zones=[pick(ZoneConfig, z) for z in zones],
            walls=walls,
            doors=[{"id": str(x.get("id", "")), "x": float(x["x"]), "y": float(x["y"]),
                    "width": float(x.get("width", 0.9))} for x in d.get("doors", [])],
            background=d.get("background", {}),
            params=pick(TrackerParams, d.get("params", {})),
        )

    @classmethod
    def load(cls, path: pathlib.Path) -> "Config":
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))

    def save(self, path: pathlib.Path):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False))
        tmp.replace(path)
