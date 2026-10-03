"""Multi-sensor, multi-person tracker.

One track per person in the house frame, filtered with an IMM Kalman filter (walk / still).
The core rule: people do not appear or vanish out of nowhere.
  - A new track is confirmed quickly in an entry zone (stairs, front door, balcony) and only
    after a longer run of consistent detections anywhere else.
  - A confirmed track that stops getting detections is kept at its last place ("lost": a person
    sitting still that the LD2450 dropped). It only ends when it was lost in an entry zone or
    outside all sensors, when the LD2410C of every sensor covering that place has reported
    "nobody here" for a while, or after a long time without any support.
  - A detection close to a lost track picks that track up again instead of starting a new one.
The LD2410C (distance only, with regular ghost detections) never creates tracks. Its own hold
time is set to 0 in the firmware; the hold happens here: gaps up to ld2410_hold are bridged.
A ghost (interference from the LD2450) lasts about a second, so only presence that lasts
ld2410_min_presence, at the distance of a lost track, supports the track. Without such support
a lost track ends after a while.
"""

import math
from dataclasses import dataclass, field

import numpy as np

from .assignment import assign
from .imm import IMM, STILL, WALK, IMMState
from .model import Config, SensorConfig

TENTATIVE, CONFIRMED = "tentative", "confirmed"
_BIG = 1e9


@dataclass
class Detection:
    sensor: str
    slot: int
    pos: np.ndarray  # house frame
    R: np.ndarray
    radial: np.ndarray  # unit vector sensor -> target on the floor
    speed: float  # radial speed on the floor, m/s, negative = approaching
    local: tuple  # raw LD2450 x, y in meters (sensor frame), for calibration
    ignored: bool = False  # inside an interference zone
    outside: bool = False  # outside all rooms: a reflection behind a wall, not used for tracking


@dataclass
class Track:
    id: int
    state: IMMState
    t: float
    born: float
    born_at_entry: bool
    status: str = TENTATIVE
    first_hit: float = 0.0
    last_hit: float = 0.0
    last_support: float = 0.0
    hits: int = 0
    expected: int = 0  # frames of sensors that should have seen it (while tentative)
    last_hit_by: dict = field(default_factory=dict)
    near_since: dict = field(default_factory=dict)  # other track id -> time they came close
    co_detected: dict = field(default_factory=dict)  # other track id -> last time both in one frame
    zones: set = field(default_factory=set)

    def position(self) -> np.ndarray:
        return self.state.mu @ self.state.x[:, :2]

    def velocity(self) -> np.ndarray:
        return self.state.mu @ self.state.x[:, 2:]

    def lost(self, now: float, lost_after: float) -> bool:
        return now - self.last_hit > lost_after

    @property
    def walk_prob(self) -> float:
        return float(self.state.mu[WALK])


@dataclass
class SensorRuntime:
    last_frame: float = -math.inf
    detections: list = field(default_factory=list)
    frame: dict | None = None
    ld_present: bool = False
    ld_distance: float = 0.0  # m, on the floor
    ld_present_since: float | None = None
    ld_last_present: float = -math.inf
    ld_ever_present: bool = False
    move_gates: list | None = None  # LD2410C energy per 0.75 m gate (engineering mode)
    still_gates: list | None = None
    frames: int = 0


class SensorClock:
    """Frame time from the sensor's uptime, anchored to the receive time.

    The receive time jitters with WiFi; the uptime does not. The offset follows the fastest
    delivery seen (smallest receive - uptime) and creeps up by 1 ms per second so that it
    follows clock drift and a broker that got slower. Resets on a reboot or a jump.
    """

    def __init__(self):
        self.offset = None
        self.last_up = None

    def __call__(self, recv: float, uptime_ms: int | None) -> float:
        if uptime_ms is None:
            return recv
        up = uptime_ms / 1000
        candidate = recv - up
        if self.offset is None or up < self.last_up or abs(candidate - self.offset) > 2.0:
            self.offset = candidate
        else:
            self.offset = min(candidate, self.offset + 0.001 * (up - self.last_up))
        self.last_up = up
        return up + self.offset


class Tracker:
    def __init__(self, config: Config, start: float | None = None):
        self.config = config
        self.imm = IMM(config.params)
        self.tracks: list[Track] = []
        self.next_id = 1
        self.runtime: dict[str, SensorRuntime] = {}
        self.start = start
        self.now = start or 0.0
        self.unknown_sensors: dict[str, float] = {}
        self.listeners = []  # called with (event, data) for calibration and debugging

    # ------------------------------------------------------------------ input

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        if self.start is None:
            self.start = t
        self.now = max(self.now, t)
        sensor = self.config.sensor_by_id.get(sensor_id)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        rt.last_frame = t
        rt.frame = frame
        rt.frames += 1
        if sensor is None:
            self.unknown_sensors[sensor_id] = t
            return
        detections = self._detections(sensor, frame)
        rt.detections = detections
        if not sensor.enabled or not sensor.placed:
            return
        self._ld2410(sensor, rt, t, frame.get("ld2410") or {})
        self._predict_all(t)
        self._count_expected(sensor)
        self._associate(sensor, [d for d in detections if not d.outside], t)
        for listener in self.listeners:
            listener("frame", (sensor, t, detections))

    def _detections(self, s: SensorConfig, frame: dict) -> list:
        p = self.config.params
        out = []
        for target in frame.get("targets", []):
            lx, ly = target["x"] / 1000, target["y"] / 1000
            if ly <= 0:
                continue
            wx, wy, ground, slant = s.to_world(lx, ly, p.target_height)
            dx, dy = wx - s.x, wy - s.y
            u = np.array([dx, dy]) / max(ground, 1e-3)
            # measurement noise: range along u, angle across it, worse at wide angles
            az = math.degrees(math.atan2(lx, ly))
            sigma_t = max(ground * math.radians(p.sigma_angle) * (1 + 0.5 * (az / 60) ** 2), 0.05)
            rot = np.array([[u[0], -u[1]], [u[1], u[0]]])
            R = rot @ np.diag([p.sigma_range**2, sigma_t**2]) @ rot.T
            speed = target.get("speed", 0) / 1000 * (slant / ground if ground > 0 else 1)
            pos = np.array([wx, wy])
            ignored = any(z.contains(wx, wy) for z in self.config.zones_of("ignore"))
            rooms = self.config.zones_of("room")
            outside = bool(rooms) and not any(z.contains(wx, wy, p.outside_margin) for z in rooms)
            out.append(Detection(s.id, target.get("slot", 0), pos, R, u, speed, (lx, ly), ignored, outside))
        return out

    # ------------------------------------------------------------- filtering

    def _predict_all(self, t: float):
        p = self.config.params
        max_sigma = p.max_gate_radius / math.sqrt(p.gate)
        for tr in self.tracks:
            dt = t - tr.t
            if dt <= 0:
                continue
            coast = tr.status == CONFIRMED and t - tr.last_hit > p.coast_time
            self.imm.predict(tr.state, dt, force_still=coast, max_sigma=max_sigma)
            tr.t = t

    def _count_expected(self, sensor: SensorConfig):
        walls = self.config.wall_segments
        for tr in self.tracks:
            if tr.status == TENTATIVE:
                x, y = tr.position()
                if sensor.sees(x, y, walls):
                    tr.expected += 1

    def _associate(self, sensor: SensorConfig, detections: list, t: float):
        p = self.config.params
        if not detections:
            return
        tracks = self.tracks
        n, m = len(detections), len(tracks)
        cost = [[_BIG] * (m + n) for _ in range(n)]
        for i, d in enumerate(detections):
            for j, tr in enumerate(tracks):
                d2 = IMM.gate_distance(tr.state, d.pos, d.R)
                if d2 < p.gate:
                    penalty = 1.0 if tr.status == TENTATIVE else 0.0
                    cost[i][j] = d2 + penalty
            cost[i][m + i] = p.gate  # start a new track (or drop the detection)
        result = assign(cost)

        updated = []
        unassigned = []
        for i, j in enumerate(result):
            d = detections[i]
            if j < m and cost[i][j] < _BIG:
                self._update(tracks[j], d, t)
                updated.append(tracks[j])
            else:
                unassigned.append(d)

        for a in updated:
            for b in updated:
                if a is not b:
                    a.co_detected[b.id] = t

        for d in unassigned:
            if d.ignored:
                continue
            # LD2450 sometimes splits one body into two targets
            if any(np.linalg.norm(tr.position() - d.pos) < p.split_radius for tr in updated):
                continue
            # someone getting up where a lost track sits: same person
            lost = [tr for tr in tracks
                    if tr.status == CONFIRMED and tr not in updated and tr.lost(t, p.lost_after)]
            near = min(lost, key=lambda tr: np.linalg.norm(tr.position() - d.pos), default=None)
            if near is not None and np.linalg.norm(near.position() - d.pos) < 1.5 * p.max_gate_radius:
                near.state = IMMState.from_position(d.pos, d.R, walk_prob=0.5)
                self._update(near, d, t, measure=False)
                updated.append(near)
                continue
            self._birth(d, t)

    def _update(self, tr: Track, d: Detection, t: float, measure: bool = True):
        p = self.config.params
        if measure:
            z = np.array([d.pos[0], d.pos[1], d.speed])
            R = np.zeros((3, 3))
            R[:2, :2] = d.R
            R[2, 2] = p.sigma_speed**2
            self.imm.update(tr.state, z, R, radial=d.radial)
        if tr.hits == 0:
            tr.first_hit = t
        tr.hits += 1
        tr.last_hit = t
        tr.last_support = t
        tr.last_hit_by[d.sensor] = t

    def _birth(self, d: Detection, t: float):
        p = self.config.params
        at_entry = (any(z.contains(*d.pos) for z in self.config.entry_zones)
                    or (self.start is not None and t - self.start < p.warmup))
        walk = 0.7 if abs(d.speed) > 0.15 else 0.4
        tr = Track(self.next_id, IMMState.from_position(d.pos, d.R, walk), t, t, at_entry)
        self.next_id += 1
        self._update(tr, d, t, measure=False)
        tr.expected = 1
        self.tracks.append(tr)

    # --------------------------------------------------------------- LD2410C

    def _ld2410(self, s: SensorConfig, rt: SensorRuntime, t: float, ld: dict):
        p = self.config.params
        rt.move_gates = ld.get("move_gates")
        rt.still_gates = ld.get("still_gates")
        if not (ld.get("moving") or ld.get("still")):
            rt.ld_present = t - rt.ld_last_present <= p.ld2410_hold
            return
        if rt.ld_present_since is None or t - rt.ld_last_present > p.ld2410_hold:
            rt.ld_present_since = t  # a new episode
        rt.ld_last_present = t
        rt.ld_present = True
        rt.ld_ever_present = True
        dist_mm = ld.get("still_distance") or ld.get("moving_distance") or 0
        slant = dist_mm / 1000
        dh = s.height - p.target_height
        rt.ld_distance = math.sqrt(max(slant * slant - dh * dh, 0.0))
        if t - rt.ld_present_since < p.ld2410_min_presence:
            return
        for tr in self.tracks:
            if tr.status != CONFIRMED or not tr.lost(t, p.lost_after):
                continue
            x, y = tr.position()
            if not s.sees(x, y, self.config.wall_segments, margin=0.3):
                continue
            if abs(math.hypot(x - s.x, y - s.y) - rt.ld_distance) < p.ld2410_support_window:
                tr.last_support = t

    # ----------------------------------------------------------- housekeeping

    def step(self, t: float):
        """Confirm, merge and end tracks. Call regularly (a few times per second)."""
        p = self.config.params
        self.now = max(self.now, t)
        t = self.now
        keep = []
        for tr in self.tracks:
            if tr.status == TENTATIVE:
                if t - tr.last_hit > p.tentative_timeout:
                    self._emit("dropped", tr)
                    continue
                need = p.confirm_time_entry if tr.born_at_entry else p.confirm_time
                ratio = tr.hits / max(tr.expected, 1)
                if t - tr.first_hit >= need and ratio >= p.confirm_ratio:
                    tr.status = CONFIRMED
                    self._emit("confirmed", tr)
            elif tr.lost(t, p.lost_after):
                reason = self._end_reason(tr, t)
                if reason:
                    self._emit("ended", (tr, reason))
                    continue
            keep.append(tr)
        self.tracks = keep
        self._merge(t)

    def _end_reason(self, tr: Track, t: float) -> str | None:
        p = self.config.params
        x, y = tr.position()
        since = t - tr.last_hit
        if since > p.exit_timeout:
            if any(z.contains(x, y) for z in self.config.entry_zones):
                return "left via entry"
            if not self.config.visible_sensors(x, y, margin=-0.2):
                return "left coverage"
        if t - tr.last_support > p.max_lost_time:
            return "no support"
        covering = [s for s in self.config.visible_sensors(x, y)
                    if self.runtime.get(s.id) and self.runtime[s.id].ld_ever_present]
        if covering and t - tr.last_support > p.absence_time:
            return "no LD2410C support"
        return None

    def _merge(self, t: float):
        p = self.config.params
        active = [tr for tr in self.tracks if tr.status == CONFIRMED and not tr.lost(t, p.lost_after)]
        remove = set()
        for i, a in enumerate(active):
            for b in active[i + 1:]:
                if a.id in remove or b.id in remove:
                    continue
                close = np.linalg.norm(a.position() - b.position()) < p.merge_distance
                if not close:
                    a.near_since.pop(b.id, None)
                    continue
                since = a.near_since.setdefault(b.id, t)
                if t - since < p.merge_time:
                    continue
                if t - a.co_detected.get(b.id, -math.inf) < p.merge_time:
                    continue  # one sensor saw both at once: two people
                younger = b if b.born > a.born else a
                remove.add(younger.id)
                self._emit("merged", younger)
        if remove:
            self.tracks = [tr for tr in self.tracks if tr.id not in remove]

    def _emit(self, event: str, data):
        for listener in self.listeners:
            listener(event, data)

    # ---------------------------------------------------------------- output

    def confirmed(self) -> list:
        return [tr for tr in self.tracks if tr.status == CONFIRMED]

    def snapshot(self) -> dict:
        p = self.config.params
        t = self.now
        tracks = []
        for tr in self.tracks:
            x, P = tr.state.mean()
            tracks.append({
                "id": tr.id,
                "x": round(float(x[0]), 3), "y": round(float(x[1]), 3),
                "vx": round(float(x[2]), 3), "vy": round(float(x[3]), 3),
                "sigma": round(math.sqrt(max(P[0, 0], P[1, 1])), 3),
                "status": tr.status,
                "lost": tr.lost(t, p.lost_after),
                "walk": round(tr.walk_prob, 3),
                "age": round(t - tr.born, 1),
            })
        sensors = {}
        for sid, rt in self.runtime.items():
            sensors[sid] = {
                "online": t - rt.last_frame < 15,
                "detections": [{"x": round(float(d.pos[0]), 3), "y": round(float(d.pos[1]), 3),
                                "lx": round(d.local[0], 3), "ly": round(d.local[1], 3),
                                "speed": round(d.speed, 2), "ignored": d.ignored, "outside": d.outside}
                               for d in rt.detections] if t - rt.last_frame < 1.0 else [],
                "ld2410": {"present": rt.ld_present, "distance": round(rt.ld_distance, 2),
                           "move_gates": rt.move_gates, "still_gates": rt.still_gates},
            }
        return {"t": t, "tracks": tracks, "sensors": sensors}
