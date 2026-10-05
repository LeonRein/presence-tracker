"""The people in the house, each as a particle cloud (MODEL.md): motion between frames, the
measurement model per frame, the outputs.

Per sensor frame, every person's particles are weighted by how well the person there explains
the frame (MODEL.md 4.1 and 3.3):

  hit by detection j:  (1 - m) * g_j / lambda_j     g_j: density of the measurement (position,
                                                     radial speed); lambda_j: ghost density there
  no hit:              m = U(x, tau + dt) / U(x, tau)
                       U(x, tau) = P(someone who stays at x is not detected for tau seconds)
                             = (1 - c) (1 - P_D(x))^(tau / frame) + c S(tau)
                       (independent misses of single frames, plus long dropouts: rate r per
                       second of being seen, durations S; c = min(r frame / P_D, 1))

Which detection belongs to whom is summed over all assignments (each person at most one
detection, each detection at most one person, the rest are ghosts), as in JPDA.
"""

import itertools
import math

import numpy as np

from .cloud import STILL, WALK, Cloud, Motion
from .sensormodel import GAP_FLOOR, GAP_PRIOR, GAP_TAUS, GATE, PD_MAX, SensorModel, cell_of
from .tracker import SensorClock, SensorRuntime, Tracker  # noqa: F401 (SensorClock re-exported)
from .unobserved import Dwell
from .world import OBSERVED, World

SPEED_SPREAD = 4.0  # m/s: ghosts' radial speeds spread over about +-2 m/s
FRAME = 0.089  # s, LD2450 frame period (measured)
DROP_RATE = {STILL: 1 / 70, WALK: 1 / 300}  # 1/s: dropouts begin (measured on the recordings)
WALK_GAP = 0.7  # s, mean dropout of a walker in view (measured 0.3-1.2 s)
PD_CELL = 0.1  # m, cache of the detection probability per sensor
# ghosts per m^2 and frame, until learned: measured 0.4 short ghosts per hour in the empty rooms at
# night (5 h), none next to people sitting; while somebody walks about 4 per minute and sensor
# (each ~1 s, simulation calibrated to the recordings) within a few meters of the walker
CLUTTER_PRIOR = 1e-4
CLUTTER_FLOOR = 1e-5
ECHO_DENSITY = 3e-3  # per m^2 and frame per walker (a reflection can show up anywhere in view)
ECHO_RADIUS = 8.0  # m
GHOST_LIFE = 1.5  # s, mean (measured: night ghosts 1.5 s, echoes about 1 s)
GHOST_SHOWS = 0.85  # P(a ghost that is still there shows up in a frame)
GHOST_SPREAD = 0.15  # m: a ghost stays at its spot, wobbling about this much
GHOST_SPEED = 0.2  # m/s: how much a ghost's radial speed varies around the one it showed up with
GHOST_MIN = 0.02  # ghosts less probable than this are forgotten
GHOST_BORN = 0.5  # a ghost is kept from a detection that is more probably a new ghost than not
MAX_GHOSTS = 3  # per sensor
HELD_FRAMES = 5  # the same nonzero speed this often in a row: the sensor holds a lost target
FROZEN_RADIUS = 1.0  # m around a frozen target where the sensor's silence says nothing
FROZEN_MAX = 35.0  # s: the longest a person's target stays frozen (measured)
MOUNT_RADIUS = 0.3  # m around a sensor: targets there come from its mount, not from people
BODY = 0.3  # m: two people's centers are never closer
LD_LEARNED = (100, 300)  # frames with a person in a gate / empty before the gate is used as evidence
LD_CORR = 3.0  # s: LD2410C gate energies with a person stay correlated about this long (measured 2.5-4 s)


def _ghost_survival(age: float) -> float:
    """Share of ghosts still there after `age` s (measured: most about 1-1.5 s)."""
    return math.exp(-age / GHOST_LIFE)


def _survival(mode: int, tau: np.ndarray) -> np.ndarray:
    """Share of dropouts lasting longer than tau. Standing / sitting: the measured table (long,
    heavy tail: the LD2450 loses still people for minutes). Walking: a walker in view is found
    again within about a second (measured 0.3-1.2 s); the table's long walking gaps were people
    who had left the view."""
    if mode == WALK:
        return np.exp(-np.maximum(tau, 0.0) / WALK_GAP)
    table = np.array(GAP_PRIOR["still"])
    taus = np.array(GAP_TAUS)
    t = np.maximum(tau, taus[0])
    out = np.exp(np.interp(np.log(t), np.log(taus), np.log(table)))
    tail = t > taus[-1]
    out[tail] = table[-1] * taus[-1] / t[tail]
    return out


class Person:
    def __init__(self, pid: int, cloud: Cloud):
        self.pid = pid
        self.cloud = cloud


class Crowd:
    def __init__(self, config, start: float | None = None, n: int = 800, seed: int = 1, people=None,
                 sensor_model: SensorModel | None = None):
        """people: place names where the people are at the start, or "anywhere" (default: the
        configured number of residents, anywhere - until the arrivals of MODEL.md 3.2 are built)."""
        self.config = config
        self.p = config.params
        self.world = World(config)
        self.sensor_model = sensor_model or SensorModel(config)
        self.dwell = Dwell(self.p, [rid for rid, r in config.regions.items() if r["open"]])
        self.motion = Motion()
        self.rng = np.random.default_rng(seed)
        self.n = n
        self.sensors = [s.id for s in config.sensors]
        self.sidx = {sid: k for k, sid in enumerate(self.sensors)}
        self.helper = Tracker(config)  # measurement preprocessing (detections, frozen targets)
        self.runtime = {}
        self.now = start or 0.0
        self.start = start
        self.last_step = None
        self.listeners = []
        self._pd_cache = {}
        self._held = {}  # sensor -> slot -> (raw speed, frames in a row)
        self._ld_used = {}  # sensor -> time the LD2410C energies last weighed the clouds
        self._ghosts = {}  # sensor -> [{"pos", "alive", "born", "last"}]
        self.reset_people(people)

    def reset_people(self, people=None):
        """Start over: nothing known about where anybody is. The residents, each anywhere - also
        out of the house. (Somebody unknown coming in is not built yet: see MODEL.md 3.2.)"""
        if people is None:
            people = ["anywhere"] * max(int(self.p.residents), 1)
        self.people = [Person(k + 1, Cloud.anywhere(self.n, len(self.sensors), self.rng, self.world, self.now) if place == "anywhere"
                              else Cloud.at_place(self.n, len(self.sensors), self.rng, self.world.index[place], self.now, self.world))
                       for k, place in enumerate(people)]

    def reconfigure(self, config):
        """The floor plan or a sensor changed: the geometry is rebuilt, where people are starts over."""
        self.config = config
        self.p = config.params
        self.world = World(config)
        self.sensor_model.config = config
        self.sensor_model.rebuild()
        self.dwell.p = config.params
        self.sensors = [s.id for s in config.sensors]
        self.sidx = {sid: k for k, sid in enumerate(self.sensors)}
        self.helper = Tracker(config)
        self._pd_cache.clear()
        self.reset_people()

    # ---------------------------------------------------------------- fields

    def _pd(self, sid: str, pos: np.ndarray) -> np.ndarray:
        """Detection probability per frame of sensor sid at the points (n, 2), from a raster of
        the world's cells (rebuilt once a minute: learning is slow)."""
        grid, built = self._pd_cache.get(sid, (None, -math.inf))
        if grid is None or self.now - built > 60:
            w = self.world
            grid = np.zeros((w.nx, w.ny))
            for i in range(w.nx):
                for j in range(w.ny):
                    if w.labels[i, j] >= 0:
                        grid[i, j] = min(self.sensor_model.pd(sid, w.x0 + (i + 0.5) * 0.1, w.y0 + (j + 0.5) * 0.1), PD_MAX)
            self._pd_cache[sid] = (grid, self.now)
        i, j = self.world.cell_of(pos)
        return grid[i, j]

    def _miss(self, sid: str, cloud: Cloud, idx: np.ndarray, t: float, gap: float) -> np.ndarray:
        """m per particle (observed ones, idx): P(no detection now | unseen since last_hit)."""
        pos = cloud.pos[idx]
        pd = self._pd(sid, pos)
        tau = np.maximum(t - gap - cloud.last_hit[idx, self.sidx[sid]], 0.0)
        out = np.empty(len(idx))
        for mode in (STILL, WALK):
            k = cloud.mode[idx] == mode
            if not k.any():
                continue
            rate = DROP_RATE[mode]
            c = np.minimum(rate * FRAME / np.maximum(pd[k], 1e-3), 1.0)
            q = 1 - pd[k]

            def unseen(tt):
                return (1 - c) * q ** (tt / FRAME) + c * _survival(mode, tt)
            out[k] = unseen(tau[k] + gap) / np.maximum(unseen(tau[k]), 1e-300)
        return np.clip(out, 0.0, 1.0)

    # ------------------------------------------------------------ the frames

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        if self.start is None:
            # the clocks of the start state count from the first frame, not from 1970: otherwise
            # everybody is "unseen for decades" and may stay unseen anywhere for free
            self.start = t
            for person in self.people:
                c = person.cloud
                c.since[:] = t
                c.entered[:] = t
                c.last_hit[:] = t
        self.now = max(self.now, t)
        sensor = self.config.sensor_by_id.get(sensor_id)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        gap = min(max(t - rt.last_frame, 1e-3), 1.0)
        rt.last_frame = t
        rt.frame = frame
        rt.frames += 1
        if sensor is None:
            return
        detections = self.helper._detections(sensor, frame)
        self.helper._mark_stale(rt, frame, detections)
        # a target the LD2450 lost it keeps reporting for about a second, with the same speed
        # frame after frame (measured: at 70 % of the targets' ends, elsewhere in 2.5 %): a
        # prediction of its own, no measurement
        held = self._held.setdefault(sensor_id, {})
        speeds = {tg.get("slot", 0): tg.get("speed", 0) for tg in frame.get("targets", [])}
        for slot in list(held):
            if slot not in speeds:
                del held[slot]
        for slot, v in speeds.items():
            last = held.get(slot)
            held[slot] = (v, last[1] + 1 if last and last[0] == v and v != 0 else 1)
        for d in detections:
            if held.get(d.slot, (0, 0))[1] >= HELD_FRAMES:
                d.stale = True
        rt.detections = detections
        self._ld_runtime(sensor, rt, t, frame.get("ld2410") or {})
        energies = [max(a, b) for a, b in zip(rt.move_gates, rt.still_gates)] if rt.move_gates and rt.still_gates else None
        for listener in self.listeners:
            listener("frame", (sensor, t, detections))
        if not sensor.enabled or not sensor.placed:
            return
        # right at the sensor: a reflection of its mount - a person there would be far outside its
        # vertical field of view (it hangs at about 1.5 m and looks ahead)
        for d in detections:
            if math.hypot(d.pos[0] - sensor.x, d.pos[1] - sensor.y) < MOUNT_RADIUS:
                d.hidden = True
        dets = [d for d in detections if not d.hidden and not d.stale and not d.ignored]
        # outside the plan's rooms (in a wall, beyond the outer wall): not a person. Seen through an
        # open door into a place without a sensor: somebody there, like anywhere else
        if dets:
            inside = self.world.place_of(np.array([d.pos for d in dets])) >= 0
            dets = [d for d, ok in zip(dets, inside) if ok]
        self.step(t, np.array([d.pos for d in dets]).reshape(-1, 2))
        # frozen targets: a person sitting still makes the LD2450 repeat itself for up to FROZEN_MAX
        # (measured); longer it is something of its own (a reflection at the mount), not a person
        frames = {slot: n for slot, (_, n) in rt.repeats.items()}
        frozen = np.array([d.pos for d in detections if d.stale and not d.hidden
                           and frames.get(d.slot, 0) * FRAME <= FROZEN_MAX]).reshape(-1, 2)
        self._update(sensor, t, dets, gap, full=len(detections) >= 3, frozen=frozen)
        if energies:
            self._ld_learn(sensor, t, energies, dets)
            if t - self._ld_used.get(sensor.id, -math.inf) >= LD_CORR:
                self._ld_used[sensor.id] = t
                self._ld_weigh(sensor, t, energies)

    def _update(self, s, t, dets, gap, full, frozen=None):
        p = self.p
        m_det = len(dets)
        # ghost density (MODEL.md 3.4): measured almost none in an empty room, echoes near walkers
        walkers = self._walking_near(np.array([d.pos for d in dets]).reshape(-1, 2))
        lam = np.array([(self.sensor_model.clutter_density(s.id, d.pos[0], d.pos[1], CLUTTER_PRIOR, CLUTTER_FLOOR)
                         + ECHO_DENSITY * walkers[j]) / SPEED_SPREAD for j, d in enumerate(dets)])
        per = []  # per person: (observed particle indices, m, g (n_obs, m_det) / lambda, M, A (m_det,))
        tau_before = []  # per person and observed particle: unseen by this sensor before this frame
        for person in self.people:
            c = person.cloud
            w = c.weights()
            idx = np.flatnonzero(c.place != self.world.outside)
            tau_before.append(t - c.last_hit[idx, self.sidx[s.id]])
            m = self._miss(s.id, c, idx, t, gap) if len(idx) else np.zeros(0)
            if frozen is not None and len(frozen) and len(idx):
                # the sensor repeats a target bit-identically: it is stuck there and says nothing new
                # about that spot (mostly the person is still there, measured)
                d = c.pos[idx][:, None, :] - frozen[None, :, :]
                stuck = (np.hypot(d[..., 0], d[..., 1]) < FROZEN_RADIUS).any(axis=1)
                m[stuck] = 1.0
                # and no news is not "unseen": the clock of not being seen starts when it ends
                c.last_hit[idx[stuck], self.sidx[s.id]] = t
            r = np.zeros((len(idx), m_det))
            for j, d in enumerate(dets):
                dz = c.pos[idx] - d.pos
                Rinv = np.linalg.inv(d.R)
                q = np.einsum("ni,ij,nj->n", dz, Rinv, dz)
                gpos = np.exp(-0.5 * q) / (2 * math.pi * math.sqrt(np.linalg.det(d.R)))
                vr = c.vel[idx] @ d.radial
                gspd = np.exp(-0.5 * ((d.speed - vr) / p.sigma_speed) ** 2) / (math.sqrt(2 * math.pi) * p.sigma_speed)
                r[:, j] = gpos * gspd / lam[j]
            miss_full = np.ones_like(m) if full else m
            M = float(w[idx] @ miss_full) + float(w.sum() - w[idx].sum())  # out of the house: never detected
            A = (w[idx] * (1 - m)) @ r if m_det else np.zeros(0)
            per.append((idx, m, miss_full, r, M, A))

        # ghosts this sensor has been showing (MODEL.md 3.4): one that lasts shows up again where it
        # was, without speed, and never where a body is
        ghosts = self._ghosts_now(s.id, t)
        explain = [(pp[5], pp[4]) for pp in per]  # (A per detection, M) of everybody who may explain one
        for g in ghosts:
            A = np.zeros(m_det)
            for j, d in enumerate(dets):
                C = d.R + np.eye(2) * GHOST_SPREAD**2
                dz = d.pos - g["pos"]
                dens = math.exp(-0.5 * float(dz @ np.linalg.solve(C, dz))) / (2 * math.pi * math.sqrt(np.linalg.det(C)))
                # a ghost keeps the radial speed it showed up with (still ones about 0; a target the
                # LD2450 holds keeps its last speed exactly)
                spd = math.exp(-0.5 * ((d.speed - g["speed"]) / GHOST_SPEED) ** 2) / (math.sqrt(2 * math.pi) * GHOST_SPEED)
                A[j] = g["alive"] * GHOST_SHOWS * dens * spd / lam[j]
            free = 1 - self._body_near(g["pos"])
            explain.append((A * free, 1 - g["alive"] * GHOST_SHOWS * free))
        n_p = len(self.people)
        n_e = len(explain)
        beta_all = np.zeros((n_e, m_det + 1))
        total = 0.0
        for a in itertools.product(*[range(-1, m_det)] * n_e):
            used = [j for j in a if j >= 0]
            if len(used) != len(set(used)):
                continue
            pr = 1.0
            for k, j in enumerate(a):
                pr *= explain[k][0][j] if j >= 0 else explain[k][1]
            total += pr
            for k, j in enumerate(a):
                beta_all[k, j + 1 if j >= 0 else 0] += pr
        if total <= 0:
            return
        beta_all /= total
        beta = beta_all[:n_p]
        new = (1 - beta_all[:, 1:].sum(axis=0)) * np.array([1 - self._body_near(d.pos) for d in dets]) if m_det else np.zeros(0)
        self._ghosts_update(s.id, t, dets, ghosts, beta_all[n_p:], new)

        gaps_ended = []
        for k, person in enumerate(self.people):
            c = person.cloud
            idx, m, miss_full, r, M, A = per[k]
            factor = np.full(c.n, beta[k, 0] / max(M, 1e-300))  # out of the house: only "not detected"
            ended = (np.zeros(0, dtype=int), np.zeros(0))
            if len(idx):
                hit = np.zeros(len(idx))
                for j in range(m_det):
                    if A[j] > 0:
                        hit += beta[k, j + 1] * (1 - m) * r[:, j] / A[j]
                f_obs = beta[k, 0] * miss_full / max(M, 1e-300) + hit
                factor[idx] = f_obs
                # which particles were hit: drawn by their share of "hit" in the factor
                got = self.rng.random(len(idx)) < hit / np.maximum(f_obs, 1e-300)
                c.last_hit[idx[got], self.sidx[s.id]] = t
                long_gap = got & (tau_before[k] > GAP_FLOOR) & (c.mode[idx] == STILL)
                ended = (idx[long_gap], tau_before[k][long_gap])
            c.logw += np.log(np.maximum(factor, 1e-300))
            c.normalize()
            gaps_ended.append(ended)
        self._learn_frame(s, t, dets, beta, gap, tau_before, gaps_ended)
        for k, person in enumerate(self.people):
            gi, taus = gaps_ended[k]
            w = person.cloud.weights()
            if len(gi) and float(w[gi].sum()) > 0.5:
                # found again where they sat: the LD2410C gate was occupied all the time
                pos = w[gi] @ person.cloud.pos[gi] / w[gi].sum()
                g = int(self._gates(s, pos[None, :], self.p.ld2410_fov)[0])
                if g >= 0:
                    self.sensor_model.learn_ld2410_gap(s.id, g, t - float(np.median(taus)), t)
        for person in self.people:
            person.cloud.resample()

    def step(self, t: float, targets=None):
        if self.last_step is None:
            self.last_step = t
            return
        dt = t - self.last_step
        if dt < 0.05:
            return
        self.last_step = t
        for person in self.people:
            person.cloud.predict(t, dt, self.world, self.dwell, self.motion, targets)
        self._bodies()
        for person in self.people:
            person.cloud.normalize()

    def _bodies(self):
        """Two bodies don't stand in one place (MODEL.md 3.1): a particle of one person is only
        as possible as the others are not within BODY of it."""
        if len(self.people) < 2:
            return
        w0 = self.world
        nx, ny = int(w0.nx * 0.1 / BODY) + 2, int(w0.ny * 0.1 / BODY) + 2
        cells, dens = [], []
        for person in self.people:
            c = person.cloud
            obs = np.flatnonzero(c.place != self.world.outside)
            i = np.clip(((c.pos[obs, 0] - w0.x0) / BODY).astype(int), 0, nx - 1)
            j = np.clip(((c.pos[obs, 1] - w0.y0) / BODY).astype(int), 0, ny - 1)
            grid = np.zeros((nx + 2, ny + 2))
            np.add.at(grid, (i + 1, j + 1), c.weights()[obs])
            # the probability within about BODY: the own cell and a quarter of the eight around it
            near = grid.copy()
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di or dj:
                        near[1:-1, 1:-1] += 0.25 * grid[1 + di:nx + 1 + di, 1 + dj:ny + 1 + dj]
            cells.append((obs, i + 1, j + 1))
            dens.append(near)
        for k, person in enumerate(self.people):
            obs, i, j = cells[k]
            if not len(obs):
                continue
            free = np.ones(len(obs))
            for kk, near in enumerate(dens):
                if kk != k:
                    free *= np.clip(1 - near[i, j], 1e-6, 1)
            person.cloud.logw[obs] += np.log(free)

    def _walking_near(self, pos: np.ndarray) -> np.ndarray:
        """Per point: expected number of people walking within ECHO_RADIUS (their echoes)."""
        out = np.zeros(len(pos))
        for person in self.people:
            c = person.cloud
            k = np.flatnonzero((c.place != self.world.outside) & (c.mode == WALK))
            if not len(k) or not len(pos):
                continue
            w = c.weights()[k]
            d = np.hypot(c.pos[k][:, None, 0] - pos[None, :, 0], c.pos[k][:, None, 1] - pos[None, :, 1])
            out += (w[:, None] * (d < ECHO_RADIUS)).sum(axis=0)
        return out

    def _body_near(self, pos) -> float:
        """P(at least one person stands within BODY of pos)."""
        free = 1.0
        for person in self.people:
            c = person.cloud
            obs = np.flatnonzero(c.place != self.world.outside)
            if len(obs):
                near = np.hypot(*(c.pos[obs] - pos).T) < BODY
                free *= 1 - float(c.weights()[obs][near].sum())
        return 1 - free

    def _ghosts_now(self, sid: str, t: float) -> list:
        """This sensor's ghosts, each still there by the lifetime distribution (MODEL.md 3.4)."""
        out = []
        for g in self._ghosts.get(sid, []):
            a0, a1 = g["last"] - g["born"], t - g["born"]
            g["alive"] *= _ghost_survival(a1) / max(_ghost_survival(a0), 1e-12)
            g["last"] = t
            if g["alive"] >= GHOST_MIN:
                out.append(g)
        self._ghosts[sid] = out
        return out

    def _ghosts_update(self, sid: str, t: float, dets, ghosts, beta_g, new):
        """After a frame: each ghost still there as likely as the frame says, moved to where it
        probably showed up (it drifts with its own targets); where a detection is more probably a
        new ghost than anything else, one is kept."""
        for k, g in enumerate(ghosts):
            took = beta_g[k, 1:]
            stays = g["alive"] * (1 - GHOST_SHOWS) / max(1 - g["alive"] * GHOST_SHOWS, 1e-12)
            g["alive"] = float(took.sum() + beta_g[k, 0] * stays)
            for j, d in enumerate(dets):
                g["pos"] = g["pos"] + took[j] * (d.pos - g["pos"])
        for j, d in enumerate(dets):
            if new[j] < GHOST_BORN:
                continue
            near = [g for g in ghosts if float(np.linalg.norm(g["pos"] - d.pos)) < 2 * GHOST_SPREAD]
            if near:
                near[0]["alive"] = max(near[0]["alive"], float(new[j]))
                continue
            ghosts.append({"pos": d.pos.copy(), "speed": d.speed, "alive": float(new[j]), "born": t, "last": t})
        ghosts.sort(key=lambda g: -g["alive"])
        self._ghosts[sid] = [g for g in ghosts[:MAX_GHOSTS] if g["alive"] >= GHOST_MIN]

    def _gates(self, s, pos: np.ndarray, fov: float) -> np.ndarray:
        """LD2410C gate (0.75 m slant distance step) of the points (n, 2) for the radar in sensor
        s's case; -1 outside the cone of `fov` degrees."""
        dx, dy = pos[:, 0] - s.x, pos[:, 1] - s.y
        ly = dx * s._cos + dy * s._sin
        gx = dx * s._sin - dy * s._cos
        lx = -gx if s.mirror else gx
        slant = np.sqrt(lx * lx + ly * ly + (s.height - self.p.target_height) ** 2)
        g = np.floor(slant / GATE).astype(int)
        inside = (ly > 0) & (np.degrees(np.abs(np.arctan2(lx, np.maximum(ly, 1e-9)))) <= fov / 2)
        return np.where(inside, g, -1)

    def _ld_weigh(self, s, t, energies):
        """MODEL.md 4.2: the energy in a particle's gate with a person there vs. with nobody
        (learned, a mixture over the neighbouring gates: the exact slant distance is uncertain).
        Energy where another person probably is explains itself: with q that probability, the
        factor is q + (1 - q) ratio. Particles out of the trusted cone, behind walls or behind a
        door: 1. One observation per LD_CORR (the device smooths the energies)."""
        p, sm = self.p, self.sensor_model
        n_g = len(energies)
        if s.id not in sm.ld_occ:
            return
        ratio = {}
        for moving in (False, True):
            # only what was learned: a gate counts once it has seen enough with a person in it and
            # empty (after a reset - a moved sensor - an assumed distribution made people up)
            occ = (sm.ld_move if moving else sm.ld_occ)[s.id].sum(axis=1)
            emp = sm.ld_emp[s.id].sum(axis=1)
            single = np.array([sm.ld2410_ratio(s.id, k, energies[k], moving=moving)
                               if occ[k] >= LD_LEARNED[0] and emp[k] >= LD_LEARNED[1] else 1.0 for k in range(n_g)])
            padded = np.concatenate([[single[0]], single, [single[-1]]])
            ratio[moving] = 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]
        # each person's probability of being in each gate of the wider beam (energy reaches it)
        occ = []
        for person in self.people:
            c = person.cloud
            obs = np.flatnonzero(c.place != self.world.outside)
            g = self._gates(s, c.pos[obs], p.ld2410_beam)
            q = np.zeros(n_g + 4)
            ok = (g >= 0) & (g < n_g)
            np.add.at(q, g[ok] + 2, c.weights()[obs][ok])
            near = np.array([q[k:k + 5].sum() for k in range(n_g)])  # within two gates
            occ.append(np.minimum(near, 1.0))
        for k, person in enumerate(self.people):
            c = person.cloud
            obs = np.flatnonzero(c.place != self.world.outside)
            if not len(obs):
                continue
            g = self._gates(s, c.pos[obs], p.ld2410_fov)
            seen = (g >= 0) & (g < n_g) & (self._pd(s.id, c.pos[obs]) > 0.05)
            if not seen.any():
                continue
            others = np.zeros(n_g)
            for kk, q in enumerate(occ):
                if kk != k:
                    others = 1 - (1 - others) * (1 - q)
            idx = obs[seen]
            gg = g[seen]
            walking = c.mode[idx] == WALK
            r = np.where(walking, ratio[True][gg], ratio[False][gg])
            factor = others[gg] + (1 - others[gg]) * r
            c.logw[idx] += np.log(np.maximum(factor, 1e-6))
            c.normalize()

    def _ld_learn(self, s, t, energies, dets):
        """The energy distributions per gate: with a sure person sitting / walking in it, and
        with nobody within two gates (people anywhere probable, and this sensor's own targets,
        block their gates). Independent evidence: the people are sure from the LD2450."""
        p = self.p
        still, moving, blocked = set(), set(), set()
        for person in self.people:
            mean, mass, spread = self._sure(person)
            c = person.cloud
            obs = np.flatnonzero(c.place != self.world.outside)
            if len(obs):
                w = c.weights()[obs]
                g = self._gates(s, c.pos[obs], p.ld2410_beam)
                for gate in np.unique(g[(g >= 0) & (w > 0.01)]):
                    blocked.update(range(gate - 2, gate + 3))
            if mean is None or mass < 0.9 or spread > 0.4:
                continue
            w = c.weights()
            recent = float(w[obs] @ (t - c.last_hit[obs].max(axis=1) < 1.0)) / mass
            if recent < 0.8:
                continue
            g = int(self._gates(s, mean[None, :], p.ld2410_fov)[0])
            if g >= 0:
                walk = float(w[obs] @ (c.mode[obs] == WALK)) / mass
                (moving if walk > 0.5 else still).add(g)
        for d in dets:
            g = int(self._gates(s, d.pos[None, :], p.ld2410_beam)[0])
            if g >= 0:
                blocked.update(range(g - 2, g + 3))
        self.sensor_model.learn_ld2410(s.id, t, energies, still, moving, blocked)

    def _ld_runtime(self, s, rt, t, ld):
        """LD2410C state for the display (presence with the app's own hold time)."""
        p = self.p
        rt.move_gates = ld.get("move_gates")
        rt.still_gates = ld.get("still_gates")
        present = bool(ld.get("moving") or ld.get("still"))
        if present:
            rt.ld_last_present = t
            slant = (ld.get("still_distance") or ld.get("moving_distance") or 0) / 1000
            dh = s.height - p.target_height
            rt.ld_distance = math.sqrt(max(slant * slant - dh * dh, 0.0))
        rt.ld_present = present or t - rt.ld_last_present <= p.ld2410_hold

    # -------------------------------------------------------------- learning

    def _sure(self, person):
        """(mean position, observed mass, spread) of a person in the observed area."""
        c = person.cloud
        w = c.weights()
        obs = c.place == OBSERVED
        mass = float(w[obs].sum())
        if mass <= 0:
            return None, 0.0, math.inf
        mean = w[obs] @ c.pos[obs] / mass
        spread = math.sqrt(float(w[obs] @ ((c.pos[obs] - mean) ** 2).sum(axis=1)) / mass)
        return mean, mass, spread

    def _learn_frame(self, s, t, dets, beta, gap, tau_before, gaps_ended):
        """MODEL.md 7, per frame: where the sensors see ghosts."""
        sm = self.sensor_model
        sm._decay(t)
        sid = s.id
        if sid not in sm.prior:
            return
        sure = [self._sure(person) for person in self.people]

        # ghosts: where another online sensor sees well and reports nothing, nobody near
        for other in self.sensors:
            if other == sid or other not in sm.prior or not self._online(other):
                continue
            ort = self.runtime.get(other)
            if ort is None or t - ort.last_frame > 0.3:
                continue
            mask = (sm.prior[other] >= 0.8) & (sm.prior[sid] > 0.05)
            if not mask.any():
                continue
            mask = mask.copy()
            k = int(1.0 / 0.25)
            blocked = [mn for mn, mass, sp in sure if mn is not None and mass > 0.3] + \
                [d.pos for d in ort.detections if not d.hidden]
            for x, y in blocked:
                i, j = cell_of(x, y)
                mask[max(i - k, 0):i + k + 1, max(j - k, 0):j + k + 1] = False
            sm.exposure[sid] += mask
            for d in dets:
                i, j = cell_of(*d.pos)
                if mask[i, j]:
                    sm.clutter[sid][i, j] += 1
            sm.changed = True
            break

    def _online(self, sid: str) -> bool:
        rt = self.runtime.get(sid)
        return rt is not None and self.now - rt.last_frame < 15

    def learned(self) -> dict:
        return {"sensor_model": self.sensor_model.to_dict(),
                "dwell": self.dwell.dwell}

    def load_learned(self, data: dict):
        self.sensor_model.load_dict(data.get("sensor_model", {}))
        self.dwell.dwell = {k: list(v) for k, v in data.get("dwell", {}).items()}
        self._pd_cache.clear()

    # --------------------------------------------------------------- outputs

    def place_probabilities(self) -> dict:
        """pid -> {place name: probability}."""
        out = {}
        for person in self.people:
            pr = person.cloud.place_probabilities(len(self.world.places))
            out[person.pid] = {name: float(pr[i]) for i, name in enumerate(self.world.places) if pr[i] > 0}
        return out

    def zone_probabilities(self) -> dict:
        """zone id -> [P(person k is there)] for room zones (observed ones by particle position)."""
        region_of = {room: rid for rid, r in self.config.regions.items() for room in r["rooms"]}
        out = {}
        for z in self.config.zones_of("room"):
            probs = []
            for person in self.people:
                c = person.cloud
                w = c.weights()
                rid = region_of.get(z.id)
                if rid is not None:
                    probs.append(float(w[c.place == self.world.index[rid]].sum()))
                else:
                    obs = np.flatnonzero(c.place == OBSERVED)
                    i, j = self.world.cell_of(c.pos[obs])
                    inside = self.world.zone_mask(z)[i, j]
                    probs.append(float(w[obs][inside].sum()))
            out[z.id] = probs
        return out

    def zone_states(self) -> dict:
        """Per zone: the most probable number of people (people independent), P(somebody is
        there); moving / still and "about to be entered" from where each person is drawn - only
        for people in view: behind a door nobody knows whether they move."""
        from .zones import ZoneState
        p = self.p
        states = {}
        region_of = {room: rid for rid, r in self.config.regions.items() for room in r["rooms"]}
        for zid, probs in self.zone_probabilities().items():
            dist = np.array([1.0])
            for q in probs:  # count distribution
                dist = np.convolve(dist, [1 - q, q])
            st = ZoneState()
            rid = region_of.get(zid)
            # a room without a sensor that shares its region with other rooms: the model only knows
            # "somewhere in the region" - the probability of that, but no count of its own
            st.count = int(np.argmax(dist)) if rid is None or len(self.config.regions[rid]["rooms"]) == 1 else 0
            st.probability = 1 - float(dist[0]) if rid is not None else None
            states[zid] = st
        total = ZoneState()
        zones = [z for z in self.config.zones if z.kind in ("room", "area")]
        for z in zones:
            states.setdefault(z.id, ZoneState())
        # in the house: the most probable number
        home = np.array([1.0])
        for person in self.people:
            p_in = 1 - float(person.cloud.place_probabilities(len(self.world.places))[self.world.outside])
            home = np.convolve(home, [1 - p_in, p_in])
        total.count = int(np.argmax(home))
        for person in self.people:
            d = self._display(person)
            if d["x"] is None:
                continue
            x, y, vx, vy = d["x"], d["y"], d["vx"], d["vy"]
            moving = not d["lost"] and d["walk"] > 0.5 and math.hypot(vx, vy) > 0.15
            total.moving += moving
            total.still += not moving
            for z in zones:
                st = states[z.id]
                if z.kind == "area" and z.contains(x, y):
                    st.count += 1
                if z.contains(x, y):
                    st.moving += moving
                    st.still += not moving
                elif moving and math.hypot(vx, vy) >= p.approach_min_speed:
                    steps = max(1, int(p.lead_time / 0.1))
                    for k in range(1, steps + 1):
                        tau = p.lead_time * k / steps
                        if z.contains(x + vx * tau, y + vy * tau):
                            st.approaching = True
                            st.eta = tau if st.eta is None else min(st.eta, tau)
                            break
        for st in (*states.values(), total):
            st.moving = min(st.moving, st.count)
            st.still = min(st.still, st.count - st.moving)
        states["_total"] = total
        return states

    def present(self) -> list:
        """People most probably in the observed area."""
        return [person for person in self.people
                if person.cloud.place_probabilities(len(self.world.places))[OBSERVED] > 0.5]

    def _display(self, person) -> dict:
        """Where to draw a person: the most probable place; in the observed area the densest
        0.2 m cell (the mean of a split cloud could lie between two possibilities)."""
        c = person.cloud
        w = c.weights()
        pr = c.place_probabilities(len(self.world.places))
        best = int(np.argmax(pr))
        out = {"id": person.pid, "places": {self.world.places[i]: round(float(v), 3) for i, v in enumerate(pr) if v >= 0.005}}
        obs = np.flatnonzero(c.place == OBSERVED)
        last = float(w @ c.last_hit.max(axis=1)) if len(w) else -math.inf
        out["lost"] = self.now - last > 1.5
        if best != OBSERVED or not len(obs):
            out.update({"x": None, "y": None, "vx": 0.0, "vy": 0.0, "sigma": 0.0, "walk": 0.0})
            return out
        grid, x0, y0, cell = c.heat(self.world)
        i, j = np.unravel_index(int(np.argmax(grid)), grid.shape)
        centre = np.array([x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell])
        near = obs[np.hypot(*(c.pos[obs] - centre).T) < 0.5]
        near = near if len(near) else obs
        wn = w[near] / w[near].sum()
        pos = wn @ c.pos[near]
        vel = wn @ c.vel[near]
        sigma = math.sqrt(float(wn @ ((c.pos[near] - pos) ** 2).sum(axis=1)))
        out.update({"x": round(float(pos[0]), 3), "y": round(float(pos[1]), 3), "vx": round(float(vel[0]), 3),
                    "vy": round(float(vel[1]), 3), "sigma": round(sigma, 3),
                    "walk": round(float(wn @ (c.mode[near] == WALK)), 3)})
        return out

    def snapshot(self) -> dict:
        """For the web UI: people (as "tracks", drawn at their most probable place), their clouds
        as heat maps (MODEL.md 6), the sensors' raw data, the places without a sensor."""
        t = self.now
        tracks, clouds = [], []
        for person in self.people:
            d = self._display(person)
            tracks.append({**d, "status": "confirmed", "age": round(t - (self.start or t), 1),
                           "where": d["places"] if d["x"] is None or d["lost"] else None,
                           "existence": None, "real": None})
            grid, x0, y0, cell = person.cloud.heat(self.world)
            ii, jj = np.nonzero(grid > 0.002)
            clouds.append({"id": person.pid, "cell": cell,
                           "cells": [[round(x0 + i * cell, 2), round(y0 + j * cell, 2), round(float(grid[i, j]), 3)]
                                     for i, j in zip(ii, jj)]})
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
            k = self.world.index.get(rid)
            probs = [float(person.cloud.place_probabilities(len(self.world.places))[k]) for person in self.people] if k is not None else []
            regions[rid] = {"name": r["name"], "open": r["open"],
                            "count": sum(pr >= 0.5 for pr in probs),
                            "probabilities": [round(pr, 3) for pr in probs if pr >= 0.005],
                            "dwell": self.dwell.stats(rid)}
        return {"t": t, "tracks": tracks, "clouds": clouds, "sensors": sensors, "regions": regions}
