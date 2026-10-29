"""Configuration: map, sensors, zones and tracker parameters. Stored as one JSON file.

All lengths in meters, angles in degrees, in the house frame (the axes of the Dobby map).
"""

import json
import math
import pathlib
from dataclasses import asdict, dataclass, field, fields

import presence_core

from .floorplan import sync_rooms
from .geometry import Shape
from .util import atomic_write


@dataclass
class SensorConfig:
    """A sensor: where it hangs and how it looks (its geometry is the core's: Config.geometry)."""
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

    def to_dict(self) -> dict:
        return {k.name: getattr(self, k.name) for k in fields(self)}


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
    # rooms without a sensor (MODEL.md 3.3, assumed)
    dwell_median: float = 120.0  # s, typical stay in such a room
    dwell_spread: float = 1.5  # spread of ln(duration), wide: long stays stay possible
    # LD2410C
    ld2410_hold: float = 1.5  # s, gaps in the LD2410C presence up to this long are bridged (display)
    # outputs
    light_cost: float = 2.0  # a second of light without anybody costs as much as this many seconds dark
                             # with somebody there (assumed; Leon: light without a person is the worst)
    # "Belegt" (MODEL.md 6): occupied also while the room's own LD2450 measured somebody in it within
    # this many s (the core's room_seen.rs); 0: the filter alone. 10 s: measured on the truth of 6.-8.10.
    seen_hold: float = 10.0  # s
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

# The configuration is checked by the core (presence_core.check_config: what the model can't run with, and
# the limits of every number, which the web UI reads for its fields). Here only its problems in the words of
# the web UI: the labels of the numbers, the sentences.
LABELS = {
    "params": {
        "target_height": "Höhe des Oberkörpers",
        "range_sigma_base": "Messfehler in Blickrichtung, Grundwert",
        "range_sigma_slope": "Messfehler in Blickrichtung, Anstieg",
        "lateral_sigma_base": "Messfehler seitlich, Grundwert",
        "lateral_sigma_slope": "Messfehler seitlich, Anstieg",
        "wall_margin": "Toleranz an Wänden",
        "dwell_median": "Typischer Aufenthalt",
        "dwell_spread": "Streuung des Aufenthalts",
        "ld2410_hold": "Haltezeit LD2410C",
        "light_cost": "Kosten: Licht ohne Person",
        "seen_hold": "Haltezeit eigener LD2450",
        "lead_time": "Vorausschau „wird betreten“",
        "approach_cost": "Kosten: Einschalten auf Verdacht",
        "target_threshold": "Schwelle „Ziel“",
    },
    "sensor": {"x": "x", "y": "y", "heading": "Blickrichtung", "height": "Montagehöhe", "fov": "Öffnungswinkel",
               "range": "Reichweite", "scale": "Maßstab"},
    "door": {"width": "Breite der Tür"},
    "layer": {"x": "x des Bildes", "y": "y des Bildes", "scale": "Maßstab des Bildes", "rotation": "Drehung des Bildes",
              "opacity": "Deckkraft des Bildes"},
}
LABELS["threshold"] = LABELS["params"]

# (part, reason[, field]) -> the sentence; {where} the part with its name, {label} the field's, {name}, {field},
# {value} (as _num writes it), {raw} (the value as it is), {low}/{high} the limits. Missing: the reason alone.
_SENTENCES = {
    (None, "empty"): "{where}{label}: leer, eine Zahl fehlt.",
    (None, "not_a_number"): "{where}{label}: keine Zahl ({value}).",
    (None, "out_of_range"): "{where}{label} muss {low} und {high} sein, nicht {value}.",
    (None, "not_an_object"): "{field} ist kein Objekt.",
    (None, "not_a_list"): "{field} ist keine Liste.",
    (None, "unreadable"): "Konfiguration unvollständig oder falsch: {raw}",
    ("config", "not_an_object"): "Konfiguration ist kein Objekt.",
    ("config", "too_many_sensors"): "Höchstens {value} Sensoren.",
    ("config", "too_large"): "Der Grundriss ist zu groß (höchstens 100 × 100 m).",
    ("params", "not_an_object"): "Einstellungen sind kein Objekt.",
    ("params", "not_an_object", "target_thresholds"): "Schwelle „Ziel“ je Raum ist kein Objekt.",
    ("sensor", "no_id"): "Sensor ohne ID.",
    ("sensor", "id_chars"): "{where}Die ID darf kein /, + oder # enthalten (sie ist Teil des MQTT-Topics).",
    ("sensor", "id_twice"): "Sensor-ID {raw} doppelt.",
    ("sensor", "not_a_bool"): "{where}{field} ist nicht ja/nein.",
    ("wall", "not_points"): "Wand {name}: Punkte sind keine endlichen Koordinaten.",
    ("wall", "not_a_segment"): "Wand {name}: braucht genau zwei verschiedene Punkte.",
    ("wall", "unknown_kind"): "Wand {name}: unbekannte Art {raw!r}.",
    ("door", "no_position"): "Tür ohne endliche Position.",
    ("zone", "not_an_object"): "Zone ist kein Objekt.",
    ("zone", "no_id"): "Zone ohne ID.",
    ("zone", "id_twice"): "Zonen-ID {raw} doppelt.",
    ("zone", "unknown_kind"): "Zone {name}: unbekannte Art {raw!r}.",
    ("zone", "unknown_shape"): "Zone {name}: unbekannte Form {raw!r}.",
    ("zone", "not_points"): "Zone {name}: Punkte sind keine endlichen Koordinaten.",
    ("zone", "rect_corners"): "Zone {name}: ein Rechteck braucht zwei Ecken.",
    ("zone", "polygon_corners"): "Zone {name}: eine Fläche braucht mindestens drei Eckpunkte.",
    ("zone", "no_centre"): "Zone {name}: ein Kreis braucht einen Mittelpunkt.",
    ("zone", "negative"): "Zone {name}: Radius ist keine Zahl ≥ 0.",
    ("ld_view", "not_an_object"): "Blick des LD2410C ist kein Objekt.",
    ("ld_view", "negative"): "Blick des LD2410C {name}: keine Zahl ≥ 0 ({value}).",
    ("layer", "not_an_object"): "Bildebene ist kein Objekt.",
}
_WHERE = {"threshold": "Raum {name}: ", "sensor": "Sensor {name}: "}


def _num(v) -> str:
    return f"{v:g}".replace(".", ",") if isinstance(v, (int, float)) and not isinstance(v, bool) else repr(v)


def message(problem) -> str:
    """The sentence of the web UI for a problem the core found (presence_core.ConfigProblem)."""
    p = problem
    text = (_SENTENCES.get((p.part, p.reason, p.field)) or _SENTENCES.get((p.part, p.reason))
            or _SENTENCES[None, p.reason])
    words = {"where": _WHERE.get(p.part, "").format(name=p.name), "name": p.name, "field": p.field,
             "label": LABELS.get(p.part, {}).get(p.field, p.field), "raw": p.value, "value": _num(p.value)}
    if p.reason == "out_of_range":
        lo, hi, lo_open, hi_open = p.limits
        words["low"] = f"größer als {_num(lo)}" if lo_open else f"mindestens {_num(lo)}"
        words["high"] = f"kleiner als {_num(hi)}" if hi_open else f"höchstens {_num(hi)}"
    return text.format(**words)


def _plain(v):
    """A configuration for the core's JSON: a number that is no number (NaN, inf; JSON has none) as its name."""
    if isinstance(v, float) and not math.isfinite(v):
        return repr(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def check(d) -> None:
    """Rejects what the model can't run with (the core's check: values outside their limits, coordinates that
    are no finite numbers, ids twice, walls that are no segment, zones of an unknown kind or shape or
    without what their shape needs, ...): ValueError with the sentence for the web UI. Only what is present
    is checked; what is missing takes its default. (A circle without a center passed until 0.18 and failed
    every tick after, also after every restart.)"""
    try:
        presence_core.check_config(json.dumps(_plain(d)))
    except presence_core.ConfigError as e:
        raise ValueError(message(e.args[0])) from e


def limits() -> dict:
    """The limits of the numbers for the web UI (the core's): per group key -> [low, high, low excluded,
    high excluded]."""
    return presence_core.config_limits()


@dataclass
class Config:
    sensors: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    walls: list = field(default_factory=list)  # [{"points": [a, b], "kind": "wall" | "divider"}], one segment each
    doors: list = field(default_factory=list)  # [{"id", "x", "y", "width"}] on a wall
    background: dict = field(default_factory=dict)  # image placement, see web UI
    # the LD2410Cs' measured view of other rooms (MODEL.md 4.3 "Blick des LD2410C"): "own<-other" (room ids) ->
    # the share of what the geometric sight expects; missing: 1. Measured data of the home, like the walls:
    # no setting, not in params ("Alle auf Standard" keeps it)
    ld_view: dict = field(default_factory=dict)
    params: TrackerParams = field(default_factory=TrackerParams)

    def __post_init__(self):
        self.rebuild()

    def rebuild(self):
        """Recompute derived data after an edit. The places of the people model (observed rooms, regions,
        closed rooms) are the core's plan (filter.plan)."""
        self.sensor_by_id = {s.id: s for s in self.sensors}

    def geometry(self) -> presence_core.Geometry:
        """The core's sensor geometry of this configuration (MODEL.md 2, 4.1; for the calibration and the
        simulation): its sensors' frames and fields of view, the walls that block their sight."""
        return presence_core.Geometry(json.dumps(self.to_dict()))

    def zones_of(self, kind: str) -> list:
        return [z for z in self.zones if z.kind == kind]

    def home_zones(self, *kinds) -> list:
        """The zones of these kinds that belong to the home: not the outside rooms (the stairwell,
        MODEL.md 2), which get no count, no state and no entity."""
        return [z for z in self.zones if z.kind in kinds and not (z.kind == "room" and z.entry)]

    # --- persistence ---

    def to_dict(self) -> dict:
        return {
            "sensors": [s.to_dict() for s in self.sensors],
            "zones": [z.to_dict() for z in self.zones],
            "walls": self.walls,
            "doors": self.doors,
            "background": self.background,
            "ld_view": self.ld_view,
            "params": asdict(self.params),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """The configuration as stored or sent (what is missing: the defaults); unchecked (check(d) first for
        an edit in the web UI: a stored configuration loads as it is)."""
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
            ld_view=d.get("ld_view", {}),
            params=pick(TrackerParams, d.get("params", {})),
        )

    @classmethod
    def load(cls, path: pathlib.Path) -> "Config":
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))

    def save(self, path: pathlib.Path):
        atomic_write(path, json.dumps(self.to_dict(), indent=1, ensure_ascii=False).encode())
