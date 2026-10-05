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
    fov: float = 120.0
    range: float = 6.0
    enabled: bool = True
    placed: bool = False  # set once the user has put it on the map

    def __post_init__(self):
        self._update()

    def _update(self):
        h = math.radians(self.heading)
        self._cos, self._sin = math.cos(h), math.sin(h)

    def ground_local(self, lx: float, ly: float, target_height: float) -> tuple:
        """LD2450 target -> sensor frame on the floor (x right, y forward), mirror applied."""
        if self.mirror:
            lx = -lx
        slant = math.hypot(lx, ly)
        dh = self.height - target_height
        ground = math.sqrt(max(slant * slant - dh * dh, 0.01))
        scale = ground / slant if slant > 0 else 1.0
        return lx * scale, ly * scale

    def to_world(self, lx: float, ly: float, target_height: float) -> tuple:
        """LD2450 target (m, sensor frame) -> house frame. Returns (x, y, ground_range, slant_range).

        The radar measures the slant distance from its antenna; the height difference to the
        reflecting body is taken out so that the position lies on the floor plane.
        """
        if self.mirror:
            lx = -lx
        slant = math.hypot(lx, ly)
        dh = self.height - target_height
        ground = math.sqrt(max(slant * slant - dh * dh, 0.01))
        scale = ground / slant if slant > 0 else 1.0
        gx, gy = lx * scale, ly * scale
        # sensor frame: y forward along the heading, x to the right
        wx = self.x + gy * self._cos + gx * self._sin
        wy = self.y + gy * self._sin - gx * self._cos
        return wx, wy, ground, slant

    def to_local(self, wx: float, wy: float) -> tuple:
        """House frame -> sensor frame on the floor plane (x right, y forward)."""
        dx, dy = wx - self.x, wy - self.y
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
    kind: str = "area"  # room | area | ignore | entry
    shape: str = "rect"  # rect | circle | polygon
    points: list = field(default_factory=list)
    center: list | None = None
    radius: float = 0.0
    anchor: list | None = None  # rooms from walls: a point inside, identifies the room after edits
    entry: bool = False  # rooms: people may appear and leave here (stairwell, front door)

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
    sigma_speed: float = 0.25  # m/s
    stale_frames: int = 5  # frames with bit-identical coordinates until an LD2450 target counts as frozen
    wall_margin: float = 0.4  # m, detections farther behind a wall or outside all rooms are reflections
    # rooms without a sensor, see unobserved.py
    dwell_median: float = 120.0  # s, prior: typical stay in such a room, until visits are learned
    dwell_spread: float = 1.5  # prior: spread of ln(duration), wide: long stays stay possible
    dwell_prior_weight: float = 3.0  # the prior counts like this many visits
    dwell_median_open: float = 1800.0  # s, the same for an open region (bedroom, the way out): hours are normal
    dwell_spread_open: float = 2.0
    residents: int = 2  # people who live here (the particle model tracks this many until arrivals are built)
    # LD2410C
    ld2410_hold: float = 1.5  # s, gaps in the LD2410C presence up to this long are bridged (display)
    ld2410_fov: float = 50.0  # degrees, where the LD2410C is trusted to see
    ld2410_beam: float = 120.0  # degrees, where people still put energy into its gates
    # outputs
    lead_time: float = 1.0  # s, "approaching" looks this far ahead
    approach_min_speed: float = 0.3  # m/s

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
        self.exit_doors = self._unobserved()
        self.entry_zones = [z for z in self.zones if z.kind == "entry" or (z.kind == "room" and z.entry)] + self.exit_doors

    def _unobserved(self) -> list:
        """Rooms that no sensor covers, grouped into regions connected by doors.

        A region is *open* if people can come from elsewhere into it (an entry room such as the
        stairs, an entry zone, or a door to the outside), otherwise *closed* (balcony, kitchen
        with a single door): whoever comes out of a closed region must have gone in before.
        Sets self.regions {id: {name, rooms, open}} and self.closed_doors; returns the circles
        around doors into open regions, which work like entries. Derived, not stored."""
        self.regions, self.closed_doors, self.closed_rooms = {}, [], []
        self.portals = []  # all ways out of the observed area: door circles with .region, .closed
        placed = [s for s in self.sensors if s.enabled and s.placed]
        rooms = self.zones_of("room")
        if not placed or not rooms:
            return []
        covered = {z.id: self._room_covered(z, placed) for z in rooms}
        pieces = wall_pieces(self.walls)
        walls = [p for p in pieces if p[2] == "wall"]

        def sides(c, piece):
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
        for root, members in groups.items():
            outside = any((a is None and b is not None and b.id in members) or (b is None and a is not None and a.id in members)
                          for (a, b), _ in links)
            def middle(z):
                x0, y0, x1, y1 = z.geometry.bounds()
                return (x0 + x1) / 2, (y0 + y1) / 2
            entry = any(by_id[r].entry for r in members) or any(
                by_id[r].contains(*middle(z)) for z in user_entries for r in members)
            self.regions[root] = {"name": " + ".join(by_id[r].name for r in sorted(members)), "rooms": sorted(members),
                                  "open": outside or entry}

        self.closed_rooms = [by_id[r] for region in self.regions.values() if not region["open"] for r in region["rooms"]]
        out = []
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
            else:
                out.append(zone)
        # user-drawn entry zones and entry rooms (stairs) in the observed area lead outside
        for z in self.zones:
            if z.kind == "entry" or (z.kind == "room" and z.entry and covered.get(z.id)):
                x0, y0, x1, y1 = z.geometry.bounds()
                portal = ZoneConfig(f"entry-{z.id}", z.name, kind="entry", shape="circle",
                                    center=[(x0 + x1) / 2, (y0 + y1) / 2],
                                    radius=max(0.5 * math.hypot(x1 - x0, y1 - y0) + 0.3, 1.0))
                portal.region, portal.closed, portal.watch = "outside", False, (portal.center[0], portal.center[1])
                self.portals.append(portal)
        return out

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
    def from_dict(cls, d: dict) -> "Config":
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
