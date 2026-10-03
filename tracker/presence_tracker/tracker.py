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
the learned energy distributions per gate, at the distance of a lost track, support the track. Without support
a lost track ends after a while.
"""

import math
from dataclasses import dataclass, field

import numpy as np

from .assignment import assign
from .imm import IMM, STILL, WALK, IMMState
from .model import Config, SensorConfig
from .sensormodel import GATE, PD_MAX, SensorModel
from .unobserved import Dwell
from .whereabouts import ABSENT, DEAD, GONE, HERE, NEAR, Whereabouts

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
    hidden: bool = False  # behind a wall or outside all rooms: a reflection, not used for tracking
    stale: bool = False  # frozen: the LD2450 repeats the exact same coordinates, nobody is there


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
    hits: int = 0
    existence: float = 0.0  # probability that this new track is a real person (while tentative)
    where: Whereabouts | None = None  # where the person is while unseen (once confirmed)
    last_hit_by: dict = field(default_factory=dict)
    near_since: dict = field(default_factory=dict)  # other track id -> time they came close
    co_detected: dict = field(default_factory=dict)  # other track id -> last time both in one frame
    zones: set = field(default_factory=set)
    last_walking: float = -math.inf  # last detection while moving
    last_velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))  # at the last detection
    has_origin: bool = False  # came in through a door/entry, back from a room, or was there at the start
    portal_region: str | None = None  # born at a door: the region behind it
    last_evidence: dict = field(default_factory=dict)  # sensor -> last time its frame was evidence while lost
    rejoin_pos: np.ndarray | None = None  # where a lost track was first measured again

    def position(self) -> np.ndarray:
        return self.state.mu @ self.state.x[:, :2]

    def velocity(self) -> np.ndarray:
        return self.state.mu @ self.state.x[:, 2:]

    def lost(self, now: float, lost_after: float) -> bool:
        return now - self.last_hit > lost_after

    def present(self) -> bool:
        """Most probably inside the observed area (not behind a door, not gone)."""
        return self.status == CONFIRMED and (self.where is None or self.where.best()[0] in (HERE, NEAR))

    def at_home(self) -> bool:
        return self.status == CONFIRMED and (self.where is None or self.where.best()[0] not in ABSENT)

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
    repeats: dict = field(default_factory=dict)  # slot -> ((x, y) in mm, frames in a row)


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
        self.dwell = Dwell(config.params)  # how long people stay in rooms without a sensor
        self.sensor_model = SensorModel(config)  # what each sensor sees and how reliably
        self.last_step: float | None = None

    # ------------------------------------------------------------------ input

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        if self.start is None:
            self.start = t
        self.now = max(self.now, t)
        sensor = self.config.sensor_by_id.get(sensor_id)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        frame_gap = t - rt.last_frame
        rt.last_frame = t
        rt.frame = frame
        rt.frames += 1
        if sensor is None:
            self.unknown_sensors[sensor_id] = t
            return
        detections = self._detections(sensor, frame)
        self._mark_stale(rt, frame, detections)
        rt.detections = detections
        if not sensor.enabled or not sensor.placed:
            return
        weight = min(max(frame_gap, 0.0), self.config.params.evidence_time) / self.config.params.evidence_time
        self._predict_all(t)
        updates = self._associate(sensor, [d for d in detections if not d.hidden and not d.stale], t, weight)
        self._existence(sensor, t, frame_gap, updates)
        self._lost_evidence(sensor, t, weight, {id(tr) for _, tr, _ in updates})
        self._ld2410(sensor, rt, t, frame.get("ld2410") or {}, weight)
        self.sensor_model.learn(self, sensor, t, detections, [(d, tr) for d, tr, _ in updates])
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
            # measurement error: along the line of sight and across it, both growing with the
            # distance (across faster: an angle error), worse toward the edge of the view. Not the
            # tiny frame-to-frame jitter (1-5 cm), but the real offset of 15-30 cm at 3-5 m
            # (which part of the body reflects, angle bias, calibration).
            az = math.degrees(math.atan2(lx, ly))
            sigma_r = p.range_sigma_base + p.range_sigma_slope * ground
            sigma_t = (p.lateral_sigma_base + p.lateral_sigma_slope * ground) * (1 + 0.5 * (az / 60) ** 2)
            rot = np.array([[u[0], -u[1]], [u[1], u[0]]])
            R = rot @ np.diag([sigma_r**2, sigma_t**2]) @ rot.T
            speed = target.get("speed", 0) / 1000 * (slant / ground if ground > 0 else 1)
            pos = np.array([wx, wy])
            ignored = any(z.contains(wx, wy) for z in self.config.zones_of("ignore"))
            out.append(Detection(s.id, target.get("slot", 0), pos, R, u, speed, (lx, ly), ignored,
                                 self._hidden(s, pos, u)))
        return out

    def _mark_stale(self, rt: SensorRuntime, frame: dict, detections: list):
        """The LD2450 sometimes keeps reporting a target with bit-identical coordinates for up to
        ~35 s after the person left. A real person, even sitting still, changes the millimeter
        values in nearly every frame (99 % of identical runs are at most 2 frames long)."""
        p = self.config.params
        repeats = {}
        by_slot = {d.slot: d for d in detections}
        for target in frame.get("targets", []):
            slot, xy = target.get("slot", 0), (target["x"], target["y"])
            last = rt.repeats.get(slot)
            n = last[1] + 1 if last and last[0] == xy else 1
            repeats[slot] = (xy, n)
            if n >= p.stale_frames and slot in by_slot:
                by_slot[slot].stale = True
        rt.repeats = repeats

    def _hidden(self, s: SensorConfig, pos: np.ndarray, u: np.ndarray) -> bool:
        return self.config.hidden(s, pos, u, self.config.params.wall_margin)

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

    def _pd(self, sensor: SensorConfig, pos: np.ndarray) -> float:
        """Detection probability at pos, with the same tolerance at walls as the reflection filter:
        someone sitting against a wall may be measured a few centimeters behind it."""
        pd = self.sensor_model.pd_effective(sensor.id, *pos)
        if pd <= 0:
            u = pos - np.array([sensor.x, sensor.y])
            back = pos - u / max(float(np.linalg.norm(u)), 1e-6) * self.config.params.wall_margin
            pd = self.sensor_model.pd_effective(sensor.id, *back)
        return pd

    def _existence(self, sensor: SensorConfig, t: float, frame_gap: float, updates: list):
        """Bayes update of the existence probability of new tracks with this sensor's frame.

        A measurement assigned to the track: likelihood ratio (P_D g + (1 - P_D) lambda) / lambda,
        with P_D the sensor's detection probability there, g the measurement density under the
        track, lambda the ghost density there (higher next to a walking person: echoes).
        No measurement although the sensor looks there: 1 - P_D. One frame counts only as a
        fraction frame_gap / evidence_time of an independent observation: the LD2450 smooths,
        a ghost that lasts a second must not count as eleven pieces of evidence."""
        p = self.config.params
        weight = min(max(frame_gap, 0.0), p.evidence_time) / p.evidence_time
        if weight <= 0:
            return
        assigned = {id(tr): (d, g) for d, tr, g in updates}
        walkers = [tr for tr in self.tracks if tr.status == CONFIRMED and not tr.lost(t, p.lost_after)
                   and tr.walk_prob > 0.5 and float(np.linalg.norm(tr.velocity())) > 0.3]
        for tr in self.tracks:
            if tr.status != TENTATIVE:
                continue
            pd = self._pd(sensor, tr.position())
            if id(tr) in assigned:
                d, g = assigned[id(tr)]
                lam = self.sensor_model.clutter_density(sensor.id, *d.pos, p.clutter_density, p.clutter_floor)
                if any(np.linalg.norm(w.position() - d.pos) < p.echo_radius for w in walkers):
                    lam *= p.echo_factor
                ratio = (pd * g + (1 - pd) * lam) / lam
            elif pd > 0.02:
                ratio = 1 - pd
            else:
                continue
            logit = math.log(tr.existence / (1 - tr.existence)) + weight * math.log(max(ratio, 1e-9))
            tr.existence = min(max(1 / (1 + math.exp(-logit)), 1e-4), 1 - 1e-4)

    def _associate(self, sensor: SensorConfig, detections: list, t: float, weight: float) -> list:
        """Returns [(detection, track, likelihood)] for the tracks updated with a measurement.

        A detection attaches to a track only at its Kalman position (here); the cost is the
        Mahalanobis distance minus 2 ln of the mass of "here". Someone showing up elsewhere, at a
        door or nearby, becomes a new track that takes the lost one over once confirmed."""
        p = self.config.params
        if not detections:
            return []
        tracks = self.tracks
        n, m = len(detections), len(tracks)
        cost = [[_BIG] * (m + n) for _ in range(n)]
        for i, d in enumerate(detections):
            for j, tr in enumerate(tracks):
                best = _BIG
                w_here = tr.where.here() if tr.where is not None else 1.0
                z = np.array([d.pos[0], d.pos[1], d.speed])
                R = np.zeros((3, 3))
                R[:2, :2] = d.R
                R[2, 2] = p.sigma_speed**2
                c, d2 = IMM.association_cost(tr.state, z, R, d.radial)
                if d2 < p.gate:
                    # a small "here" mass is a penalty, not a veto: being measured right at the
                    # place is strong evidence by itself (the LD2450 drops sitting people for long)
                    best = c + (1.0 if tr.status == TENTATIVE else 0.0) + min(-2 * math.log(max(w_here, 1e-6)), p.mass_penalty_cap)
                if best < _BIG:
                    cost[i][j] = best
            cost[i][m + i] = p.gate  # start a new track (or drop the detection)
        result = assign(cost)

        updated = []
        pairs = []
        unassigned = []
        for i, j in enumerate(result):
            d = detections[i]
            if j < m and cost[i][j] < _BIG:
                tr = tracks[j]
                g = IMM.likelihood(tr.state, d.pos, d.R)  # before the update
                if tr.where is not None and tr.lost(t, p.lost_after):
                    tr.rejoin_pos = d.pos.copy()
                    mode = "walking" if tr.last_hit - tr.last_walking <= 1.0 else "still"
                    self.sensor_model.learn_gap(mode, t - tr.last_hit)
                    if mode == "still" and float(np.linalg.norm(tr.position() - d.pos)) < 0.6:
                        # they sat there all along: the LD2410C gate was occupied, whatever it showed
                        for o in self.config.sensors:
                            if o.enabled and o.placed and o.sees(d.pos[0], d.pos[1], self.config.wall_segments):
                                lx, ly = o.to_local(d.pos[0], d.pos[1])
                                if ly > 0 and abs(math.degrees(math.atan2(lx, ly))) <= p.ld2410_fov / 2:
                                    gate = int(math.hypot(lx, ly, o.height - p.target_height) / GATE)
                                    self.sensor_model.learn_ld2410_gap(o.id, gate, tr.last_hit, t)
                self._update(tr, d, t)
                if tr.where is not None:
                    pd = self._pd(sensor, d.pos)
                    lam = self._clutter(sensor, d.pos)
                    tr.where.update(HERE, (pd * g + (1 - pd) * lam) / lam, weight)
                    tr.last_evidence.clear()
                    self._arrived(tr, t, d.pos)
                updated.append(tr)
                pairs.append((d, tr, g))
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
            self._birth(d, t)
        return pairs

    def _arrived(self, tr: Track, t: float, pos: np.ndarray):
        first = tr.rejoin_pos if tr.rejoin_pos is not None else pos

        def at_door(region):
            return any(q.region == region and q.contains(first[0], first[1], 0.3) for q in self.config.portals)
        for rid, duration in tr.where.arrive(t, self.dwell, at_door):
            if at_door(rid):
                self._someone_came_out(rid, tr)
            self._emit("returned", (tr, rid, duration))
        if tr.where.here() >= 0.5:
            tr.rejoin_pos = None
        if tr.where.here() > 0.95:
            tr.where.reset_here()

    def _reach(self, tr: Track, t: float) -> float:
        p = self.config.params
        return p.reach_min + p.unseen_speed * max(t - tr.last_hit, 0.0)

    def _portals_within_reach(self, tr: Track, t: float) -> list:
        """[(region, prior weight)] of the doors the person could have walked to unseen: closer
        doors and the one they were walking toward are more likely; a door whose threshold an
        online sensor watches well, where nobody was seen passing, is unlikely (a pass can
        still be missed: door_pass_missed)."""
        p = self.config.params
        reach = self._reach(tr, t)
        pos = tr.position()
        v = tr.last_velocity
        out = {}
        for portal in self.config.portals:
            center = np.array(portal.geometry.center)
            dist = max(float(np.linalg.norm(center - pos)) - 0.5, 0.0)
            if dist > reach:
                continue
            wt = math.exp(-dist / p.door_scale)
            if float(np.linalg.norm(v)) > 0.3 and float(np.dot(center - pos, v)) > 0:
                wt *= p.toward_factor
            watched = max((self.sensor_model.pd_effective(s.id, *portal.watch) / PD_MAX
                           for s in self.config.sensors if s.enabled and s.placed and self._online(s.id)), default=0.0)
            wt *= 1 - watched * (1 - p.door_pass_missed)
            out[portal.region] = out.get(portal.region, 0.0) + wt
        return list(out.items())

    def _clutter(self, sensor: SensorConfig, pos: np.ndarray) -> float:
        p = self.config.params
        return self.sensor_model.clutter_density(sensor.id, pos[0], pos[1], p.clutter_density, p.clutter_floor)

    def _update(self, tr: Track, d: Detection, t: float, measure: bool = True):
        p = self.config.params
        if measure:
            # (Counting the frames of one sensor as partly dependent - the LD2450 smooths
            # internally - made tracks lag behind walkers and spawn duplicates: tried, measured
            # worse on the recordings, left out.)
            z = np.array([d.pos[0], d.pos[1], d.speed])
            R = np.zeros((3, 3))
            R[:2, :2] = d.R
            R[2, 2] = p.sigma_speed**2
            self.imm.update(tr.state, z, R, radial=d.radial)
        if tr.hits == 0:
            tr.first_hit = t
        tr.hits += 1
        tr.last_hit = t
        vx, vy = tr.velocity()
        tr.last_velocity = np.array([vx, vy])
        if tr.walk_prob > 0.5 and math.hypot(vx, vy) > 0.2:
            tr.last_walking = t
        tr.last_hit_by[d.sensor] = t

    def _birth(self, d: Detection, t: float):
        p = self.config.params
        warmup = self.start is not None and t - self.start < p.warmup
        # at a door to a room without a sensor only if nobody we know of is in there (otherwise
        # the association has claimed the detection for them)
        portal = next((q for q in self.config.portals if q.contains(d.pos[0], d.pos[1])), None)
        at_entry = warmup or (portal is not None and not portal.closed) \
            or any(z.contains(d.pos[0], d.pos[1]) for z in self.config.zones_of("entry"))
        walk = 0.7 if abs(d.speed) > 0.15 else 0.4
        tr = Track(self.next_id, IMMState.from_position(d.pos, d.R, walk), t, t, at_entry)
        tr.has_origin = at_entry
        tr.existence = p.birth_entry if at_entry else p.birth_room
        if portal is not None:
            tr.portal_region = portal.region
            # someone we know of is probably behind that door: likely them coming back
            behind = max((x.where.region(portal.region) for x in self.tracks if x.where is not None), default=0.0)
            tr.existence = max(tr.existence, min(behind, p.birth_return))
        self.next_id += 1
        self._update(tr, d, t, measure=False)
        self.tracks.append(tr)

    # --------------------------------------------------- evidence while unseen

    def _lost_evidence(self, sensor: SensorConfig, t: float, weight: float, updated: set):
        """This sensor's frame had no measurement for a lost track. Against "still here": the
        LD2450 drops people it saw, for a long time too - how often a dropout lasts another dt
        is the learned re-detection survival, applied where the sensor sees the spot. Against
        "moved unseen nearby": a walker in the part of the disc the sensor sees would be seen."""
        p = self.config.params
        model = self.sensor_model
        for tr in self.tracks:
            if tr.where is None or id(tr) in updated or not tr.lost(t, p.lost_after):
                continue
            pos = tr.position()
            if tr.where.here() >= 0.01:
                see = self._pd(sensor, pos) / PD_MAX
                if see > 0.02:
                    # the dropout survival is one fact about the person, not one per sensor: split
                    # it among the online sensors that see the spot
                    total = sum(self._pd(o, pos) / PD_MAX for o in self.config.sensors
                                if o.enabled and o.placed and self._online(o.id))
                    share = see / max(total, see)
                    mode = "walking" if tr.last_hit - tr.last_walking <= 1.0 else "still"
                    tau = t - tr.last_hit
                    since = max(t - tr.last_evidence.get(sensor.id, t), 0.0)
                    s_now = model.redetection_survival(mode, tau)
                    s_before = model.redetection_survival(mode, max(tau - since, 0.0))
                    tr.last_evidence[sensor.id] = t
                    if s_before > 0 and since > 0:
                        tr.where.update(HERE, 1 - (1 - s_now / s_before), share)
            if tr.where.w[NEAR] >= 0.01:
                r = min(self._reach(tr, t), p.near_max) * 0.6
                pts = [pos] + [pos + r * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
                pd = sum(self._pd(sensor, q) for q in pts) / len(pts)
                if pd > 0.02:
                    tr.where.update(NEAR, 1 - pd, weight)

    def _ld2410(self, s: SensorConfig, rt: SensorRuntime, t: float, ld: dict, weight: float):
        """LD2410C: energy per 0.75 m gate. For a lost track at gate g, the energy there is
        evidence for or against "still here" by the learned energy distributions (with a person
        in the gate vs. nobody near it), unless someone else explains that gate. Also learns."""
        p = self.config.params
        rt.move_gates = ld.get("move_gates")
        rt.still_gates = ld.get("still_gates")
        present = bool(ld.get("moving") or ld.get("still"))
        if present:
            if rt.ld_present_since is None or t - rt.ld_last_present > p.ld2410_hold:
                rt.ld_present_since = t  # a new episode
            rt.ld_last_present = t
            rt.ld_ever_present = True
            dist_mm = ld.get("still_distance") or ld.get("moving_distance") or 0
            slant = dist_mm / 1000
            dh = s.height - p.target_height
            rt.ld_distance = math.sqrt(max(slant * slant - dh * dh, 0.0))
        rt.ld_present = present or t - rt.ld_last_present <= p.ld2410_hold
        if not rt.still_gates or not rt.move_gates:
            return
        energies = [max(a, b) for a, b in zip(rt.move_gates, rt.still_gates)]

        def gate_of(x, y):
            lx, ly = s.to_local(x, y)
            if ly <= 0 or abs(math.degrees(math.atan2(lx, ly))) > p.ld2410_fov / 2:
                return None
            slant = math.hypot(lx, ly, s.height - p.target_height)
            return int(slant / GATE)

        # who explains which gate: seen people and this sensor's own detections
        explained = set()  # the energy spills into the neighbouring gates, two each way
        for tr in self.tracks:
            if tr.status == CONFIRMED and not tr.lost(t, p.lost_after):
                g = gate_of(*tr.position())
                if g is not None:
                    explained.update(range(g - 2, g + 3))
        for d in rt.detections:
            if not d.hidden and not d.stale:
                g = gate_of(*d.pos)
                if g is not None:
                    explained.update(range(g - 2, g + 3))
        # evidence for lost tracks
        for tr in self.tracks:
            if tr.where is None or not tr.lost(t, p.lost_after) or tr.where.here() < 0.01:
                continue
            x, y = tr.position()
            g = gate_of(x, y)
            if g is None or g >= len(energies) or not s.sees(x, y, self.config.wall_segments, margin=0.3):
                continue
            if g in explained:
                continue
            # the exact gate is uncertain (slant range, where on the body the echo comes from)
            ratio = max(self.sensor_model.ld2410_ratio(s.id, k, energies[k], moving=tr.walk_prob > 0.5)
                        for k in (g - 1, g, g + 1) if 0 <= k < len(energies))
            tr.where.update(HERE, ratio, weight)
        # learning: sure people's gates are occupied (sitting and moving are different
        # populations); gates nobody is near are empty
        still, moving = set(), set()
        for tr in self.tracks:
            if tr.status != CONFIRMED or tr.lost(t, p.lost_after):
                continue
            sure = any(sid != s.id and t - ts < 1.0 for sid, ts in tr.last_hit_by.items()) or (tr.has_origin and t - tr.born > 30)
            if sure:
                g = gate_of(*tr.position())
                if g is not None:
                    (moving if tr.walk_prob > 0.5 and float(np.linalg.norm(tr.velocity())) > 0.2 else still).add(g)
        blocked = set(explained)
        for tr in self.tracks:  # lost people might be anywhere near their last place
            if tr.where is not None and tr.lost(t, p.lost_after) and tr.where.here() + tr.where.w[NEAR] > 0.05:
                g = gate_of(*tr.position())
                if g is not None:
                    blocked.update(range(g - 2, g + 3))
        self.sensor_model.learn_ld2410(s.id, t, energies, still, moving, blocked)

    # ----------------------------------------------------------- housekeeping

    def step(self, t: float):
        """Confirm new tracks, move the mass of unseen people, end tracks, merge duplicates.
        Call regularly (a few times per second)."""
        p = self.config.params
        self.now = max(self.now, t)
        t = self.now
        dt = t - self.last_step if self.last_step is not None else 0.0
        self.last_step = t
        # new tracks first, so a lost track they turn out to be is not ended in the same step
        drop = set()
        for tr in self.tracks:
            if tr.status != TENTATIVE:
                continue
            if t - tr.last_hit > p.tentative_timeout or tr.existence < p.drop_prob:
                self._emit("dropped", tr)
                drop.add(tr.id)
                continue
            if tr.existence >= p.confirm_prob:
                lost = self._takeover_candidate(tr, t)
                if lost is not None:
                    # nobody appears out of nowhere: it's the lost person, who moved unseen
                    lost.state = tr.state
                    lost.t, lost.last_hit, lost.last_walking = tr.t, tr.last_hit, tr.last_walking
                    lost.last_hit_by.update(tr.last_hit_by)
                    lost.has_origin = True
                    if tr.portal_region and lost.where.region(tr.portal_region) >= p.takeover_mass:
                        region = tr.portal_region
                        self._someone_came_out(region, lost)
                        duration = lost.where.returned(region, t, self.dwell)
                        self._emit("returned", (lost, region, duration))
                    else:
                        lost.where.reset_here()
                        self._emit("taken over", (lost, tr))
                    drop.add(tr.id)
                    continue
                tr.status = CONFIRMED
                tr.where = Whereabouts(p, existence=tr.existence)
                if tr.portal_region:
                    # a new person out of that door: whoever we thought was in there may have
                    # been this one (an unknown newcomer counts like one more candidate)
                    self._someone_came_out(tr.portal_region, None, newcomer=1.0)
                self._emit("confirmed", tr)
        keep = []
        watched = self._doors_watched(t) if dt > 0 else {}
        for tr in self.tracks:
            if tr.id in drop:
                continue
            if tr.status == CONFIRMED and tr.lost(t, p.lost_after) and dt > 0:
                walking = tr.last_hit - tr.last_walking <= 3.0 and t - tr.last_hit <= 10.0
                tr.where.leak(dt, walking, self._portals_within_reach(tr, t), tr.last_hit)
                tr.where.end_visits(t, dt, self.dwell, watched)
                if tr.where.absent() >= p.gone_prob or t - tr.last_hit > p.max_lost_time:
                    self._emit("ended", (tr, tr.where.best()[0]))
                    continue
            keep.append(tr)
        self.tracks = keep
        self._merge(t)

    def _someone_came_out(self, region: str, who, newcomer: float = 0.0):
        """One person came out of `region` and is tracked as `who` (or as a new track). Every other
        track thought to be in there shares the chance that it was them; that share of them now
        lives on in `who`, i.e. is gone for them."""
        total = newcomer + sum(o.where.region(region) for o in self.tracks if o.where is not None and o is not who)
        for o in self.tracks:
            if o is not who and o.where is not None and o.where.region(region) > 0:
                o.where.dissolve(region, o.where.region(region) / max(total, 1e-9))

    def _doors_watched(self, t: float) -> dict:
        """Region -> some sensor that is online sees (the room side of) its door."""
        out = {}
        for portal in self.config.portals:
            out[portal.region] = out.get(portal.region, False) or any(
                self._online(s.id) and s.sees(*portal.watch, self.config.wall_segments)
                for s in self.config.sensors if s.enabled and s.placed)
        return out

    def _online(self, sensor_id: str) -> bool:
        """Frames arrive (at least the 5 s heartbeat while nothing is detected)."""
        rt = self.runtime.get(sensor_id)
        return rt is not None and self.now - rt.last_frame < 15

    def _takeover_candidate(self, tr: Track, t: float):
        """The nearest lost track that could have walked to tr's position since it was last seen
        and most probably is still inside the observed area."""
        p = self.config.params
        pos = tr.position()
        if tr.portal_region:
            behind = [o for o in self.tracks if o.where is not None and o.where.region(tr.portal_region) >= p.takeover_mass]
            if behind:
                return max(behind, key=lambda o: o.where.region(tr.portal_region))
        best, best_d = None, None
        for other in self.tracks:
            if other.where is None or not other.lost(t, p.lost_after):
                continue
            if other.where.here() + other.where.w[NEAR] < p.takeover_mass:
                continue
            d = float(np.linalg.norm(other.position() - pos))
            if d <= p.reach_min + p.unseen_speed * (tr.first_hit - other.last_hit) and (best is None or d < best_d):
                best, best_d = other, d
        return best

    def _merge(self, t: float):
        """Two tracks for one person: one of them came out of nowhere mid-room (e.g. the walker's
        track stuck on an echo for a moment and a new one picked up the walker). Such a track may
        be merged into another. A track with an origin (came in through a door or entry, came
        back from a counted room, was there at the start) is a person and never fuses with
        anyone; if the radar can't separate two people up close, one becomes a lost track."""
        p = self.config.params
        active = [tr for tr in self.tracks if tr.status == CONFIRMED and not tr.lost(t, p.lost_after)]
        remove = set()
        for i, a in enumerate(active):
            for b in active[i + 1:]:
                if a.id in remove or b.id in remove:
                    continue
                younger = b if b.born > a.born else a
                if younger.has_origin:
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

    def present(self) -> list:
        """Confirmed tracks most probably inside the observed area."""
        return [tr for tr in self.tracks if tr.present()]

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
                "existence": round(tr.existence, 3) if tr.status == TENTATIVE else None,
                "where": tr.where.to_dict() if tr.where is not None else None,
            })
        sensors = {}
        for sid, rt in self.runtime.items():
            sensors[sid] = {
                "online": t - rt.last_frame < 15,
                "detections": [{"x": round(float(d.pos[0]), 3), "y": round(float(d.pos[1]), 3),
                                "lx": round(d.local[0], 3), "ly": round(d.local[1], 3),
                                "speed": round(d.speed, 2), "ignored": d.ignored, "hidden": d.hidden or d.stale}
                               for d in rt.detections] if t - rt.last_frame < 1.0 else [],
                "ld2410": {"present": rt.ld_present, "distance": round(rt.ld_distance, 2),
                           "move_gates": rt.move_gates, "still_gates": rt.still_gates},
            }
        regions = {}
        for rid, r in self.config.regions.items():
            probs = [tr.where.region(rid) for tr in self.tracks if tr.where is not None and tr.where.region(rid) >= 0.005]
            regions[rid] = {"name": r["name"], "open": r["open"],
                            "count": sum(tr.where is not None and tr.where.best()[0] == rid for tr in self.tracks),
                            "probabilities": [round(pr, 3) for pr in probs],
                            "dwell": self.dwell.stats(rid)}
        return {"t": t, "tracks": tracks, "sensors": sensors, "regions": regions}
