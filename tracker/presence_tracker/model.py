"""Configuration: map, sensors, zones and tracker parameters. Stored as one JSON file.

All lengths in meters, angles in degrees, in the house frame (the axes of the Dobby map).
"""

import json
import math
import pathlib
from dataclasses import asdict, dataclass, field, fields

from .geometry import Shape, line_of_sight, wall_segments


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

    def sees(self, wx: float, wy: float, walls: list, margin: float = 0.0) -> bool:
        # start the sight line just in front of the sensor, so the wall it hangs on doesn't count
        start = (self.x + 0.05 * self._cos, self.y + 0.05 * self._sin)
        return self.in_fov(wx, wy, margin) and line_of_sight(start, (wx, wy), walls)


@dataclass
class ZoneConfig:
    id: str
    name: str
    kind: str = "area"  # room | area | ignore | entry
    shape: str = "rect"  # rect | circle | polygon
    points: list = field(default_factory=list)
    center: list | None = None
    radius: float = 0.0

    def __post_init__(self):
        self.geometry = Shape(self.shape, self.points, self.center, self.radius)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return self.geometry.contains(x, y, margin)

    def to_dict(self) -> dict:
        d = {k.name: getattr(self, k.name) for k in fields(self)}
        if self.shape != "circle":
            d.pop("center")
            d.pop("radius")
        return d


@dataclass
class TrackerParams:
    target_height: float = 1.0  # height of the reflecting body (chest), for the slant correction
    # LD2450 measurement noise, inflated because its frames are smoothed and correlated
    sigma_range: float = 0.15
    sigma_angle: float = 5.0  # degrees
    sigma_speed: float = 0.25  # m/s
    # motion models
    walk_accel: float = 2.0  # m/s^2, process noise of the walking model
    still_jitter: float = 0.08  # m/sqrt(s), position wander of a sitting/standing person
    walk_to_still: float = 0.7  # switching rates, 1/s
    still_to_walk: float = 0.3
    # association
    gate: float = 13.8  # chi-square, 2 dof, 99.9 %
    max_gate_radius: float = 1.0  # m, gating radius cap for tracks that were lost for a while
    split_radius: float = 0.7  # m, a second detection this close to an updated track is the same person
    # track lifecycle
    confirm_time_entry: float = 0.4  # s of detections needed for a track that starts in an entry zone
    confirm_time: float = 2.0  # s of detections needed elsewhere ("people don't appear out of nowhere")
    confirm_ratio: float = 0.5  # share of sensor frames that must contain the new track
    tentative_timeout: float = 0.8  # s without detection until a new track is dropped
    lost_after: float = 1.5  # s without detection until a track counts as lost (keeps its place)
    coast_time: float = 0.7  # s a lost track keeps moving before it stops
    exit_timeout: float = 3.0  # s until a lost track in an entry zone or outside coverage is removed
    absence_time: float = 60.0  # s without LD2410C support until a lost track is removed
    ld2410_support_window: float = 0.8  # m, LD2410C distance this close to a lost track supports it
    ld2410_min_presence: float = 6.0  # s, shorter LD2410C presence is a ghost (trigger + ~5 s hold)
    max_lost_time: float = 4 * 3600.0  # s, upper bound for a lost track without any support
    merge_distance: float = 0.5  # m, two tracks this close for merge_time are one person
    merge_time: float = 1.0
    warmup: float = 30.0  # s after start in which tracks may appear anywhere
    # outputs
    lead_time: float = 1.0  # s, "approaching" looks this far ahead
    approach_min_speed: float = 0.3  # m/s
    zone_hysteresis: float = 0.15  # m


@dataclass
class Config:
    sensors: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    walls: list = field(default_factory=list)  # polylines [[x, y], ...]
    background: dict = field(default_factory=dict)  # image placement, see web UI
    params: TrackerParams = field(default_factory=TrackerParams)

    def __post_init__(self):
        self.rebuild()

    def rebuild(self):
        """Recompute derived data after an edit."""
        for s in self.sensors:
            s._update()
        self.wall_segments = wall_segments(self.walls)
        self.sensor_by_id = {s.id: s for s in self.sensors}

    def zones_of(self, kind: str) -> list:
        return [z for z in self.zones if z.kind == kind]

    def visible_sensors(self, x: float, y: float, margin: float = 0.0) -> list:
        return [s for s in self.sensors if s.enabled and s.placed and s.sees(x, y, self.wall_segments, margin)]

    # --- persistence ---

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "sensors": [{k.name: getattr(s, k.name) for k in fields(s)} for s in self.sensors],
            "zones": [z.to_dict() for z in self.zones],
            "walls": self.walls,
            "background": self.background,
            "params": asdict(self.params),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        def pick(dc, data):
            names = {f.name for f in fields(dc)}
            return dc(**{k: v for k, v in data.items() if k in names})

        return cls(
            sensors=[pick(SensorConfig, s) for s in d.get("sensors", [])],
            zones=[pick(ZoneConfig, z) for z in d.get("zones", [])],
            walls=d.get("walls", []),
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
