"""The Bayes filter of MODEL.md: everybody's whereabouts given the LD2450 tracks and the LD2410C flags (4.3). Deterministic:
nothing is drawn, the same data always give the same result.

Hypotheses (MODEL.md 5.1): which live tracks are one person's and which are ghosts, with exact
weights. A new track (or one found again) branches every hypothesis; hypotheses that come to say the
same about the live tracks are merged (5.6). This is the data association of a PMBM filter
(Garcia-Fernandez et al. 2018).

Given a hypothesis, the people are independent:
  - a person with a live track (measured or held): a Gaussian mixture over standing / walking
    (gauss.py, 5.2)
  - a person known from earlier tracks, without one now: a density over the tiles (hidden.py, 5.3)
  - the people nobody knows of: a Poisson intensity over the tiles (hidden.Undetected, 3.4, 5.5)
The number of people is part of the state: people become known by their tracks, and one who is
almost surely out of the house is given back to the unknown ones.
Objects are shared by the hypotheses with the same history and updated once; what differs between
hypotheses makes new objects (copy on write).
"""

import itertools
import math

import numpy as np

from .filtermodel import FRAME, IDLE, STILL, WALK, Model, Shapes, radar_pd
from .frames import SensorRuntime, detections as frame_detections
from .gauss import Gauss
from .hidden import Hidden, Undetected
from .sensormodel import PD_MAX, SensorModel
from .sensortracks import SensorTracks
from .tiling import Tiling
from .unobserved import Dwell
from .world import CELL, OBSERVED, World

__all__ = ["Model", "Tracker"]

MAX_STEP = 0.2  # s: motion is cut into parts no longer than this
MOUNT_RADIUS = 0.3  # m: targets this close to a sensor come from its mount
LN2 = math.log(2.0)
GIVE_UP = 0.01  # a known person in the house with less probability joins the unknown ones with all
                # of their mass: only P(several of them come back) changes, by <= GIVE_UP^2 / 2
RECYCLE_EVERY = 1.0  # s


def _logsumexp(a) -> float:
    a = np.asarray(a, dtype=float)
    if not a.size:
        return -math.inf
    top = float(a.max())
    if top == -math.inf:
        return top
    return top + math.log(float(np.exp(a - top).sum()))


def _log(x):
    with np.errstate(divide="ignore"):
        return np.log(x)


class Hyp:
    """One hypothesis: its weight; per live track whose it is (a group id, or "g" for a ghost); per
    ghost track the log likelihood of its life per kind of ghost and its source (a Gauss); the
    people with tracks (group id -> Gauss), the known ones without (Hidden, one per person) and the
    unknown ones (Undetected)."""

    __slots__ = ("logw", "kind", "ghost", "phantom", "groups", "hidden", "ppp")

    def __init__(self, logw, kind, ghost, groups, hidden, ppp, phantom=None):
        self.logw = logw
        self.kind = kind
        self.ghost = ghost
        self.phantom = phantom if phantom is not None else {}
        self.groups = groups
        self.hidden = hidden
        self.ppp = ppp

    def child(self, logw) -> "Hyp":
        return Hyp(logw, dict(self.kind), {s: a.copy() for s, a in self.ghost.items()}, dict(self.groups),
                   list(self.hidden), self.ppp, dict(self.phantom))

    def people(self) -> list:
        """The known people (with existence 1)."""
        return list(self.groups.values()) + self.hidden

    def objects(self) -> list:
        """Everything that moves and is weighed: the known people and the unknown ones."""
        return self.people() + [self.ppp]

    def group_segs(self) -> dict:
        """group id -> the live tracks the hypothesis says are that person's."""
        out = {}
        for s, k in self.kind.items():
            if k != "g":
                out.setdefault(k, set()).add(s)
        return out

    def key(self):
        return (frozenset(frozenset(v) for v in self.group_segs().values()),
                frozenset(s for s, k in self.kind.items() if k == "g"), len(self.hidden))


class Tracker:
    def __init__(self, config, start: float | None = None, n: int | None = None, seed: int = 1, people=None,
                 sensor_model: SensorModel | None = None, model: Model | None = None):
        # n and seed are accepted for the tools' sake; nothing here is random
        self.m = model or Model()
        self.shapes = Shapes(self.m)
        self.config = config
        self.p = config.params
        self.sensor_model = sensor_model or SensorModel(config)
        self.dwell = Dwell(self.p, [rid for rid, r in config.regions.items() if r["open"]])
        self.listeners = []
        self.runtime = {}
        self.now = start or 0.0
        self.start = start
        self.loglik = 0.0  # log evidence of everything seen so far
        self._ids = itertools.count(1)
        self._gids = itertools.count(1)
        self.ghost_map = None  # where each sensor starts ghost tracks (ghostmap.py)
        self.learn_ghosts = True  # learn the map online (MODEL.md 4.2); off for the offline EM tool
        self._build()
        self.use_ghost_map(None)
        self.reset_people(people)

    # --------------------------------------------------------------- setup

    def _build(self):
        self.world = World(self.config)
        self.sensors = [s.id for s in self.config.sensors]
        self.sidx = {sid: k for k, sid in enumerate(self.sensors)}
        self.tracks = {sid: SensorTracks(sid, self._ids) for sid in self.sensors}
        self._gcache = {}
        self._area = {}
        self._ghost_total = {}
        rooms = [z for z in self.config.zones_of("room") if not any(z.id in r["rooms"] for r in self.config.regions.values())]
        self.rooms = [z.id for z in rooms]
        w = self.world
        self.room_of = np.full((w.nx, w.ny), -1, dtype=np.int16)
        for k, z in enumerate(rooms):
            self.room_of[w.zone_mask(z) & (w.labels == OBSERVED)] = k
        self.tiles = Tiling(self)
        for si in range(len(self.sensors)):
            self._g(si, np.zeros((1, 2)))

    def reset_people(self, people=None):
        """Start over: nothing known - start_people people, each anywhere (MODEL.md 5.3); or people
        at the given places ("anywhere" or a place name). Nobody unknown: newcomers arrive from
        then on (3.4). A Poisson start instead keeps expecting more people however many were found
        (its counts are independent), and the house then never looks complete."""
        if people is None:
            people = ["anywhere"] * max(int(round(self.p.start_people)), 0)
        anywhere = None
        hidden = []
        for where in people:
            if where == "anywhere":
                anywhere = anywhere or Hidden.anywhere(self.tiles)
                hidden.append(anywhere)
            else:
                hidden.append(Hidden.at_place(self.tiles, self.world.index[where]))
        self.hyps = [Hyp(0.0, {}, {}, {}, hidden, Undetected.none(self.tiles))]
        self._recycled = self.now
        self._flushed = self.now
        self._out_cache = {}
        self._ld_pending = {}  # (sensor, flag) -> time of the LD2410C flag off / on since the last move
        self._version = 0  # counts the moves of everybody and the events (for the outputs' cache)
        self.segs = {}  # seg id -> dict(si, t, zt, var, z, born, lost, ...)
        self._live_by_sensor = {si: [] for si in range(len(self.sensors))}

    def reconfigure(self, config):
        self.config = config
        self.p = config.params
        self.sensor_model.config = config
        self.sensor_model.rebuild()
        self.dwell = Dwell(self.p, [rid for rid, r in config.regions.items() if r["open"]])
        self._build()
        self.use_ghost_map(self.ghost_map)  # it holds only while the sensors are where they were
        self.reset_people()

    def use_ghost_map(self, gm) -> bool:
        """Use a learned ghost map (MODEL.md 4.2) if it was learned with the sensors where they are
        now; else start a new one from the prior (a moved, added or removed sensor changes where
        all of them see ghosts)."""
        from .ghostmap import GhostMap, pose_of
        ok = gm is not None and gm.matches(self.config)
        if ok:
            self.ghost_map = gm
        else:
            self.ghost_map = GhostMap.for_world(self.world, sum(rate for rate, _ in self.m.ghost_types),
                                                self.m.ghost_prior_time)
            self.ghost_map.poses = {s.id: pose_of(s) for s in self.config.sensors}
        self._ghost_total = {}
        return ok

    def _ghost_types(self) -> tuple:
        """The kinds of ghosts ((share or rate, mean life s), ...): learned with the map, else the
        model's."""
        if self.ghost_map.types:
            return self.ghost_map.types
        return self.m.ghost_types

    def _ghost_rate(self, si: int, pos) -> float:
        """Rate of ghost births per m^2 and s at pos for sensor si (without the echoes)."""
        return float(self.ghost_map.rate(self.sensors[si], np.asarray(pos).reshape(1, 2))[0])

    def _ghost_rate_total(self, si: int) -> float:
        """Rate of ghost births anywhere sensor si sees (per s, without the echoes)."""
        if si not in self._ghost_total:
            grid = self._gcache[si]
            i, j = np.nonzero(grid > 0.05)
            w = self.world
            pts = np.stack([w.x0 + (i + 0.5) * CELL, w.y0 + (j + 0.5) * CELL], axis=1)
            tot = float(self.ghost_map.rate(self.sensors[si], pts).sum()) * CELL * CELL
            self._ghost_total[si] = tot
        return self._ghost_total[si]

    def _measuring(self, seg) -> bool:
        """Is the track live and not held (lost) by its sensor?"""
        info = self.segs.get(seg)
        return info is not None and info["lost"] is None

    # ------------------------------------------------------------ geometry

    def _g(self, si: int, pos: np.ndarray) -> np.ndarray:
        """How well sensor si sees the points (..., 2), 0..1 (MODEL.md 4.1, from the geometry)."""
        grid = self._gcache.get(si)
        if grid is None:
            w = self.world
            sid = self.sensors[si]
            grid = np.zeros((w.nx, w.ny))
            for i in range(w.nx):
                for j in range(w.ny):
                    if w.labels[i, j] >= 0:
                        grid[i, j] = self.sensor_model.pd(sid, w.x0 + (i + 0.5) * CELL, w.y0 + (j + 0.5) * CELL) / PD_MAX
            self._gcache[si] = grid
            self._area[si] = float((grid > 0.05).sum()) * CELL * CELL
        shape = pos.shape[:-1]
        flat = pos.reshape(-1, 2)
        w = self.world
        if len(flat) <= 4:  # a person's components: plain Python is faster than numpy for so few
            return np.array([grid[min(max(int((x - w.x0) / CELL), 0), w.nx - 1), min(max(int((y - w.y0) / CELL), 0), w.ny - 1)]
                             for x, y in flat.tolist()]).reshape(shape)
        i, j = w.cell_of(flat)
        return grid[i, j].reshape(shape)

    def _polar(self, si: int, pos: np.ndarray) -> tuple:
        s = self.config.sensors[si]
        dx, dy = pos[..., 0] - s.x, pos[..., 1] - s.y
        return np.hypot(dx, dy), np.arctan2(dy, dx)

    def _mask(self, si: int, pos: np.ndarray, skip=()) -> np.ndarray:
        """Share of the rate at which sensor si would start a new track on somebody at pos (n, 2)
        that is left given its live tracks: next to a target it measures, somebody is merged into it
        (resolution, Svensson 2012); near where it holds a lost target it finds that one again."""
        out = np.ones(len(pos))
        segs = [s for s in self._live_by_sensor.get(si, []) if s not in skip]
        if not segs:
            return out
        m = self.m
        r, th = self._polar(si, pos)
        for seg in segs:
            info = self.segs[seg]
            if info["lost"] is None:
                rz, tz = self._polar(si, info["z"])
                dr = r - rz
                dc = 0.5 * (r + rz) * np.angle(np.exp(1j * (th - tz)))
                out *= 1 - np.exp(-LN2 * ((dr / m.res_range) ** 2 + (dc / m.res_cross) ** 2))
            else:
                dz = pos - info["lost"]["z"]
                out *= 1 - self._near((dz * dz).sum(axis=1))
        return out

    def _base_rates(self, si: int, pos: np.ndarray) -> tuple:
        """Rate (1/s) at which sensor si starts a track on a walker / on a still person at pos,
        without the mask: rho * P_D(r) * geometry (MODEL.md 4.1)."""
        (rs, r50s), (rw, r50w) = self.m.acquire
        r, _ = self._polar(si, pos)
        g = self._g(si, pos)
        return rw * radar_pd(r, r50w) * g, rs * radar_pd(r, r50s) * g

    def _tile_rates(self, si: int, skip=()) -> tuple:
        """(walkers, still people) rates (n,) of sensor si per tile (averaged over the tile)."""
        tl = self.tiles
        if not tl.n:
            return np.zeros(0), np.zeros(0)
        key = ("tiles", si)
        base = self._gcache.get(key)
        if base is None:
            base = self._base_rates(si, tl.points)
            self._gcache[key] = (base, tl.average(base[0]), tl.average(base[1]))
            base = self._gcache[key]
        if not any(s not in skip for s in self._live_by_sensor.get(si, [])):
            return base[1], base[2]
        mask = self._mask(si, tl.points, skip)
        return tl.average(base[0][0] * mask), tl.average(base[0][1] * mask)

    def _gauss_rates(self, si: int, g: Gauss, skip=()) -> np.ndarray:
        """(2,) rate of sensor si per component [STILL, WALK], at its mean (0 where the person has a
        measuring track of this sensor: one person, one track per sensor)."""
        if any(self.segs[s]["si"] == si and self._measuring(s) for s in g.slots if s in self.segs and s not in skip):
            return np.zeros(2)
        rw, rs = self._base_rates(si, g.pos)
        mask = self._mask(si, g.pos, skip)
        return np.array([rs[STILL], rw[WALK]]) * mask[[STILL, WALK]]

    # ------------------------------------------------------------- frames

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        if self.start is None:
            self.start = self.now = self._flushed = self._recycled = t
        sensor = self.config.sensor_by_id.get(sensor_id)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        prev = rt.last_frame
        rt.frame = frame
        rt.frames += 1
        if sensor is None:
            rt.last_frame = t
            return
        dets = frame_detections(self.config, sensor, frame)
        for d in dets:
            if math.hypot(d.pos[0] - sensor.x, d.pos[1] - sensor.y) < MOUNT_RADIUS:
                d.hidden = True
        rt.detections = dets
        self._ld_runtime(sensor, rt, t, frame.get("ld2410") or {})
        ev = self.tracks[sensor_id].update(t, frame, dets) if sensor_id in self.tracks else None
        measured = {id(d) for _, d in ev.born + ev.measured} if ev else set()
        for d in dets:
            d.stale = not d.hidden and id(d) not in measured
        for listener in self.listeners:
            listener("frame", (sensor, t, dets))
        if ev is None or not sensor.enabled or not sensor.placed:
            rt.last_frame = t
            return
        si = self.sidx[sensor_id]
        if t > self.now:
            # the time since this sensor's last frame counts as watched by it (MODEL.md 4.4)
            self.step(t)
        rt.last_frame = t
        gap = t - prev
        delta = gap if gap <= IDLE else 0.0  # longer: lost data, says nothing
        self._evidence(si, t, delta, ev)
        if "ld2410" in frame:
            self._ld_frame(si, rt, t, frame["ld2410"] or {})

    def step(self, t: float, targets=None):
        """Time goes on to t (MODEL.md 3). Everybody is moved and weighed by the tracks not started
        meanwhile together, at most MAX_STEP apart in time (and before every event that concerns
        all of them, _evidence); a measured track moves its owners alone (_measured)."""
        if self.start is None or t <= self.now:
            return
        self.now = t
        if t - self._flushed >= MAX_STEP - 1e-9:
            self._flush(t)
        if t - self._recycled >= RECYCLE_EVERY:
            self._recycled = t
            self._recycle()

    def _flush(self, t: float):
        """Move everybody to time t: the people without a track all from the last flush, each
        Gaussian from its own time; in parts no longer than MAX_STEP."""
        if t <= self._flushed:
            return
        objs = self._objects(phantoms=True)
        t0 = self._flushed
        parts = int(math.ceil((t - t0) / MAX_STEP - 1e-9))
        lat_f = None
        for k in range(parts):
            tk = t0 + (t - t0) * (k + 1) / parts
            live, lat_f = self._watching(tk, (t - t0) / parts)
            for obj, refs in objs:
                if isinstance(obj, Hidden):
                    obj.move((t - t0) / parts)
                    d = obj.weigh(*lat_f) if lat_f is not None else 0.0
                    for h in refs:
                        self.hyps[h].logw += d
        for obj, refs in objs:
            if isinstance(obj, Hidden):
                continue
            self._sync(obj, refs, t, None if lat_f is None else
                       tuple(f ** (MAX_STEP * parts / (t - t0)) for f in lat_f))
        pending, self._ld_pending = self._ld_pending, {}
        for (si, on, d), E in pending.items():
            if E > 0:
                self._ld_weigh(si, "on" if on else "off", E, d)
        self._flushed = t
        self._version += 1

    def _watching(self, t: float, dt: float) -> tuple:
        """The sensors watching at t, and the factors (walkers, still people per level of
        detectability) per tile for no track started by them in dt."""
        live = self._live(t)
        if not live or not self.tiles.n:
            return live, None
        rates = [self._tile_rates(si) for si in live]
        rw, rs = sum(r[0] for r in rates), sum(r[1] for r in rates)
        return live, (np.exp(-rw * dt), np.exp(-np.outer(self.shapes.kappa, rs) * dt))

    def _sync(self, obj: Gauss, refs, t: float, lat_f=None):
        """Move one Gaussian (a person with tracks, or a ghost's source) from its own time to t, and
        weigh it by no track started on the person meanwhile. Its part gone through a door is
        weighed with lat_f (the factors per tile for MAX_STEP from _flush); without (a measurement
        of the person follows, which drops that part) only moved."""
        t0 = self._flushed if obj.t is None else obj.t
        if t <= t0:
            return
        kappa = self.shapes.kappa
        parts = int(math.ceil((t - t0) / MAX_STEP - 1e-9))
        dt = (t - t0) / parts
        for k in range(parts):
            obj.predict(dt, self.m, self.shapes, self.tiles)
            if obj.phantom:
                continue
            live = self._live(t0 + dt * (k + 1))
            if not live:
                continue
            r = sum(self._gauss_rates(si, obj) for si in live)
            da = 0.0
            if obj.away is not None and lat_f is not None:
                share = dt / MAX_STEP  # lat_f holds for MAX_STEP; dt is at most that long
                da = obj.away.weigh(lat_f[0] ** share, lat_f[1] ** share)
            d = obj.reweigh(obj.kappa_weigh(np.exp(-kappa * r[STILL] * dt), math.exp(-r[WALK] * dt)), da)
            for h in refs:
                self.hyps[h].logw += d
        obj.t = t

    def _live(self, t: float) -> list:
        return [si for si, sid in enumerate(self.sensors)
                if sid in self.runtime and t - self.runtime[sid].last_frame <= IDLE
                and self.config.sensors[si].enabled and self.config.sensors[si].placed]

    def _recycle(self):
        """Known people almost surely out of the house join the unknown ones (MODEL.md 5.5):
        otherwise every guest who ever came would be followed forever."""
        cache = {}
        changed = False
        for hy in self.hyps:
            gone = [u for u in hy.hidden if u.out > 1 - GIVE_UP]
            if not gone:
                continue
            key = (id(hy.ppp),) + tuple(sorted(id(u) for u in gone))
            if key not in cache:
                new = hy.ppp.copy()
                for u in gone:
                    new.add(u)
                cache[key] = new
            hy.ppp = cache[key]
            for u in gone:
                hy.hidden.remove(u)
            changed = True
        if changed:
            self._flush(self.now)
            self._merge()
            self._version += 1

    def _objects(self, phantoms=False) -> list:
        """The distinct objects of all hypotheses (people; with phantoms: also the sources of ghost
        tracks), each with the hypotheses holding it (repeated as often as they hold it)."""
        seen = {}
        for h, hy in enumerate(self.hyps):
            for obj in hy.objects() + (list(hy.phantom.values()) if phantoms else []):
                seen.setdefault(id(obj), (obj, []))[1].append(h)
        return list(seen.values())

    # ------------------------------------------------------------ evidence

    def _near(self, d2) -> np.ndarray:
        """Can the sensor find its held target on a person this far (squared distance) from it?"""
        return np.exp(-0.5 * d2 / self.m.find_radius ** 2)

    def _evidence(self, si, t, delta, ev):
        m = self.m
        censored = delta <= 0
        events = censored or ev.born or ev.ended or any(seg in self.segs and self.segs[seg]["lost"] is not None
                                                       for seg, _ in ev.measured)
        if events:
            self._flush(t)  # these weigh, branch or merge everybody: all at t
            self._version += 1
        # 1. no ghost track was born meanwhile (echoes come with walkers in view)
        area = self._area.get(si, 0.0)
        if delta > 0:
            total = self._ghost_rate_total(si)
            walkers = self._walkers(si)
            for h, hy in enumerate(self.hyps):
                hy.logw -= (total + m.ghost_echo * walkers[h] * area) * delta
            if self.learn_ghosts:
                self.ghost_map.add_watch(self.sensors[si], delta)
                if self.ghost_map.fade(t):
                    self._ghost_total = {}
            for listener in self.listeners:
                listener("watch", (self.sensors[si], delta))
        # 2. the sensor's tracks: measured (or found again), lost, still held, gone
        refound = []
        for seg, d in ev.measured:
            if seg in self.segs:
                if self.segs[seg]["lost"] is None:
                    self._measured(seg, d, t)
                else:
                    refound.append((seg, d))
        for seg, kind, d in ev.lost:
            if seg in self.segs:
                self.segs[seg]["lost"] = {"kind": kind, "t": t, "z": d.pos.copy(), "u": 0.0}
        for seg, d in ev.held:
            if seg in self.segs:
                self._held(si, seg, d, t)
        ended = [seg for seg in ev.ended if seg in self.segs]
        for seg in ended:
            self._end(si, seg, t, ev.data_lost)
        if ended:
            self._merge()
        # 3. nothing seen before (start, lost data): a person there now is tracked with the share of
        # the time a track lasts against the time until one is started
        if censored:
            self._censor(si)
        # 4. new tracks, and lost ones found again
        if refound or ev.born:
            skip = {seg for seg, _ in ev.born}
            rates = self._tile_rates(si, skip)
            last = FRAME if censored else min(delta, FRAME)
            for seg, d in refound + ev.born:
                self._branch(si, seg, d, t, last, censored, rates, skip)
        self._normalize()

    def _walkers(self, si) -> np.ndarray:
        """Per hypothesis: the expected number of walkers in view of sensor si (as of the last move
        of everybody or event)."""
        key = ("walkers", si)
        hit = self._out_cache.get(key)
        if hit is not None and hit[0] == self._version and len(hit[1]) == len(self.hyps):
            return hit[1]
        val = self._count_walkers(si)
        self._out_cache[key] = (self._version, val)
        return val

    def _count_walkers(self, si) -> np.ndarray:
        val = np.zeros(len(self.hyps))
        g, _ = self.tiles.seen(si)
        for obj, refs in self._objects():
            if isinstance(obj, Hidden):
                v = float(obj.walking() @ (g > 0.1)) if self.tiles.n else 0.0
            else:
                v = (1 - obj.a) * obj.walking() * float(self._g(si, obj.pos[WALK][None, :])[0] > 0.1)
                if obj.away is not None and self.tiles.n:
                    v += obj.a * float(obj.away.walking() @ (g > 0.1))
            for h in refs:
                val[h] += v
        return val

    def _censor(self, si):
        """First frame of a sensor (or after lost data): who has no track of it now was not tracked
        by it - with the stationary share 1 / (1 + rate * life of a track)."""
        m = self.m
        kappa = self.shapes.kappa
        rw, rs = self._tile_rates(si)
        lw, ls = m.track_life[WALK], m.track_life[STILL]
        for obj, refs in self._objects():
            if isinstance(obj, Hidden):
                d = obj.weigh(1 / (1 + rw * lw), 1 / (1 + np.outer(kappa, rs) * ls))
            else:
                r = self._gauss_rates(si, obj)
                da = obj.away.weigh(1 / (1 + rw * lw), 1 / (1 + np.outer(kappa, rs) * ls)) if obj.away is not None else 0.0
                d = obj.reweigh(obj.kappa_weigh(1 / (1 + kappa * r[STILL] * ls), 1 / (1 + r[WALK] * lw)), da)
            for h in refs:
                self.hyps[h].logw += d

    def _offset_var(self, si, pos) -> float:
        """Variance (per axis, m^2) of where a track sits on a person at this distance: the
        measured spread along and across the line of sight (MODEL.md 4.1)."""
        p = self.p
        s = self.config.sensors[si]
        dist = math.hypot(pos[0] - s.x, pos[1] - s.y)
        sr = p.range_sigma_base + p.range_sigma_slope * dist
        st = p.lateral_sigma_base + p.lateral_sigma_slope * dist
        return 0.5 * (sr * sr + st * st)

    def _measured(self, seg, d, t):
        """A measurement of a track that was not lost: it sits at z = x + c + o + w on its source
        (a person, or a ghost's source), exact Kalman per component; a ghost lives on."""
        m = self.m
        info = self.segs[seg]
        for hy in self.hyps:
            if hy.kind.get(seg) == "g":
                self._ghost_life(hy, seg, info, t)
        info["gt"] = t
        if t - info["zt"] < m.pos_every:
            return
        for obj, refs in self._objects(phantoms=True):
            if isinstance(obj, Gauss) and seg in obj.slots:
                self._sync(obj, refs, t)
                dl = obj.update(seg, d.pos, m.white)
                for h in refs:
                    self.hyps[h].logw += dl
        info["t"] = info["zt"] = t
        info["z"] = d.pos.copy()

    # ghosts: per hypothesis, a mixture over the kinds of ghosts (how long they live)

    def _ghost_life(self, hy, seg, info, t):
        acc = hy.ghost[seg]
        before = _logsumexp(acc)
        dt = t - info.get("gt", info["born"])
        for k, (_, life) in enumerate(self._ghost_types()):
            acc[k] += -dt / life
        hy.logw += _logsumexp(acc) - before

    # lost tracks

    def _no_find(self, kind: str, u: float) -> float:
        """P(the sensor has not found its lost target again within u s | the person stayed where
        it was held): measured, a log-normal time to finding it again, and a small share never."""
        if u <= 0:
            return 1.0
        median, spread, never = self.m.refind[kind]
        later = 0.5 * math.erfc((math.log(u) - math.log(median)) / (spread * math.sqrt(2)))
        return never + (1 - never) * later

    def _kk_tiles(self, si, z) -> np.ndarray:
        """(n,): how well the sensor could find its target held at z on somebody in each tile."""
        tl = self.tiles
        dz = tl.points - z
        return tl.average(self._g(si, tl.points) * self._near((dz * dz).sum(axis=1)))

    def _kk_gauss(self, si, c: Gauss, z) -> np.ndarray:
        return self._g(si, c.pos) * c.near(z, self.m.find_radius)

    def _held(self, si, seg, d, t):
        """A lost track, still held: not found again on anybody near where it is held, nor on the
        ghost it may be, nor on a reflection there (MODEL.md 4.1)."""
        info = self.segs[seg]
        lost = info["lost"]
        u0, u1 = lost["u"], t - lost["t"]
        ratio = self._no_find(lost["kind"], u1) / max(self._no_find(lost["kind"], u0), 1e-300)
        lost["u"] = u1
        lr = math.log(max(ratio, 1e-300))
        if lr < 0:
            kl = self._kk_tiles(si, lost["z"]) if self.tiles.n else None
            for obj, refs in self._objects():
                if isinstance(obj, Hidden):
                    f = np.exp(kl * lr) if kl is not None else np.zeros(0)
                    dl = obj.weigh(f, f)
                else:
                    da = obj.away.weigh(np.exp(kl * lr), np.exp(kl * lr)) if obj.away is not None and kl is not None else 0.0
                    dl = obj.reweigh(self._kk_gauss(si, obj, lost["z"]) * lr, da)
                for h in refs:
                    self.hyps[h].logw += dl
            for hy in self.hyps:
                hy.logw += (self.m.find_clutter + (hy.kind.get(seg) == "g")) * lr
        lost["z"] = d.pos.copy()

    def _end(self, si, seg, t, data_lost):
        """The sensor dropped the track. For a person this follows from not finding it again
        (counted while it was held); a ghost died: its hazard. A person left without tracks goes
        to the people without one. The ghost map learns where ghosts begin and how long they live,
        with P(ghost) as judged now, but without what the map itself said at the birth (MODEL.md 4.2)."""
        w = self.hyp_weights()
        p_ghost = float(sum(wi for wi, hy in zip(w, self.hyps) if hy.kind.get(seg) == "g"))
        info = self.segs.pop(seg)
        if 0 < p_ghost < 1:  # the map entered the odds once, as a factor at the birth: taken out
            p_ghost = 1 / (1 + math.exp(math.log1p(-p_ghost) - math.log(p_ghost) + info["map_odds"]))
        life = (info["lost"]["t"] if info["lost"] else info.get("gt", info["born"])) - info["born"]
        if self.learn_ghosts and p_ghost > 0:
            self.ghost_map.add_birth(self.sensors[si], info["z0"], p_ghost)
            if not data_lost:
                self.ghost_map.add_life(life, p_ghost, self.m.ghost_types)
            self._ghost_total.pop(si, None)
        for listener in self.listeners:
            listener("track_end", (self.sensors[si], info["z0"], p_ghost, life, data_lost))
        self._live_by_sensor[si].remove(seg)
        cache = {}
        for hy in self.hyps:
            kind = hy.kind[seg]
            acc = hy.ghost.pop(seg, None)
            hy.phantom.pop(seg, None)
            if kind == "g":
                del hy.kind[seg]
                if not data_lost:
                    before = _logsumexp(acc)
                    u = t - (info["lost"]["t"] if info["lost"] else info.get("gt", info["born"]))
                    for k, (_, life) in enumerate(self._ghost_types()):
                        acc[k] += math.log(max(-math.expm1(-max(u, FRAME) / life), 1e-300))
                    hy.logw += _logsumexp(acc) - before
                continue
            self._release(hy, kind, seg, cache)
            del hy.kind[seg]

    def _release(self, hy, gid, seg, cache):
        """In hypothesis hy, the person of group gid no longer owns seg (it ended, or it is somebody
        else's in this child): its offset is marginalized out; without any live track left they go
        to the tiles (MODEL.md 5.4)."""
        obj = hy.groups[gid]
        rest = [s for s, k in hy.kind.items() if k == gid and s != seg]
        key = (id(obj), seg, bool(rest))
        if key not in cache:
            new = obj.copy()
            if seg in new.slots:
                new.drop_track(seg)
            cache[key] = new if rest else new.to_tiles(self.tiles)
        new = cache[key]
        if rest:
            hy.groups[gid] = new
        else:
            del hy.groups[gid]
            hy.hidden.append(new)

    def _branch(self, si, seg, d, t, last, censored, rates, skip):
        """A new track, or a lost one found again: branch every hypothesis (MODEL.md 5.1). A new
        track is a ghost's, or a person's who has tracks of other sensors, or a person's without
        any (from their tiles). A track found again is its owner's again, or the sensor found it
        on somebody else near the spot, or on a reflection there - the LD2450 lets a held track
        glide to the next target. Every alternative is a hypothesis with its exact weight."""
        m = self.m
        tl = self.tiles
        refind = seg in self.segs
        info = self.segs.get(seg)
        var = info["var"] if refind else self._offset_var(si, d.pos)
        z = d.pos
        ghost_rates = np.array([rate for rate, _ in self._ghost_types()])
        ghost_life = np.array([life for _, life in self._ghost_types()])
        kappa = self.shapes.kappa
        lw, ls = m.track_life[WALK], m.track_life[STILL]
        if refind:
            lost = info["lost"]
            u0, u1 = lost["u"], t - lost["t"]
            lr_nf = math.log(max(self._no_find(lost["kind"], u1) / max(self._no_find(lost["kind"], u0), 1e-300), 1e-300))
            kl = self._kk_tiles(si, lost["z"]) if tl.n else np.zeros(0)
            f_lat = (kl, kl)
            g_life = np.array([-(t - info.get("gt", info["born"])) / life_ for life_ in ghost_life])

            def logf(g):
                return _log(self._kk_gauss(si, g, lost["z"]))
        else:
            rw, rs = rates
            # a birth now instead of none in the last frame (the "none" is in the weights already)
            rsk = np.outer(kappa, rs)  # still people: per level of detectability
            f_lat = (rw * lw if censored else np.expm1(rw * last), rsk * ls if censored else np.expm1(rsk * last))
            walkers = self._walkers(si)

            def logf(g):
                r = self._gauss_rates(si, g, skip | {seg})
                if censored:
                    return g.kappa_weigh(kappa * r[STILL] * ls, r[WALK] * lw)
                return g.kappa_weigh(np.expm1(kappa * r[STILL] * last), math.expm1(r[WALK] * last))
        if not refind:
            # the ghost map learns from P(ghost) judged without itself at z (MODEL.md 4.2)
            map_rate = self._ghost_rate(si, z)
            ghost_ms, ghost_ls = [], []  # this track's ghost children: their log weights, and the same
                                         # with the prior's rate at z in place of the map's
        cache = {}
        release_cache = {}

        def derived(key, make):
            if key not in cache:
                cache[key] = make()
            return cache[key]

        def source():
            return derived(("source",), lambda: Gauss.source(seg, z, var, m, self.shapes))

        def measured_by_si(gid, hy):
            return any(self.segs[s]["si"] == si and self._measuring(s) for s, k in hy.kind.items()
                       if k == gid and s != seg)

        def attach(obj):
            """seg starts / is found on this person: (new object, log factor)."""
            if isinstance(obj, Gauss):  # somebody with tracks
                def make():
                    new = obj.copy()
                    if seg not in new.slots:
                        new.add_track(seg, var, m.const_share)
                    return new, new.update(seg, z, m.white, logf(new))
                return derived(("gauss", id(obj)), make)
            if not tl.n:
                return None, -math.inf
            return derived(("tiles", id(obj)), lambda: Gauss.from_tiles(obj, tl, f_lat[0], f_lat[1], seg, z, var, m))

        children = []
        for h, hy in enumerate(self.hyps):
            cur = hy.kind.get(seg) if refind else None

            def released(ch, cur=cur):
                """The track leaves its old owner in this child."""
                if cur == "g":
                    ch.ghost.pop(seg, None)
                    ch.phantom.pop(seg, None)
                elif cur is not None:
                    self._release(ch, cur, seg, release_cache)
                return ch

            if refind:
                # found in this frame on one of the finders: everybody near the spot as far as the
                # sensor sees them there, the ghost (if it is one), a reflection
                E = m.find_clutter + (cur == "g")
                for obj in hy.objects():
                    if isinstance(obj, Gauss):
                        E += (1 - obj.a) * float(obj.weights() @ self._kk_gauss(si, obj, lost["z"]))
                        if obj.away is not None and tl.n:
                            E += obj.a * obj.away.integrate(kl, kl)
                    elif tl.n:
                        E += obj.integrate(kl, kl)
                pf = math.log(max(-math.expm1(E * lr_nf), 1e-300)) - math.log(E)
                if cur == "g":
                    acc = hy.ghost[seg]
                    stay = acc + g_life
                    ph = hy.phantom[seg]

                    def found_source(ph=ph):
                        new = ph.copy()
                        return new, new.update(seg, z, m.white)
                    new, L = derived(("gstay", id(ph)), found_source)
                    ch = hy.child(hy.logw + pf + _logsumexp(stay) - _logsumexp(acc) + L)
                    ch.ghost[seg] = stay
                    ch.phantom[seg] = new
                    children.append(ch)
                else:
                    new, L = attach(hy.groups[cur])
                    if L > -math.inf:
                        ch = hy.child(hy.logw + pf + L)
                        ch.groups[cur] = new
                        children.append(ch)
                    ch = released(hy.child(hy.logw + pf + math.log(m.find_clutter / (2 * math.pi * m.find_radius ** 2))))
                    ch.kind[seg] = "g"
                    ch.ghost[seg] = _log(ghost_rates / ghost_rates.sum())
                    ch.phantom[seg] = source()
                    children.append(ch)
                base = pf
            else:
                lam = ghost_rates * (map_rate / ghost_rates.sum())
                lam[0] += m.ghost_echo * walkers[h]
                lam_prior = ghost_rates * (self.ghost_map.prior_rate / ghost_rates.sum())
                lam_prior[0] += m.ghost_echo * walkers[h]
                if censored:
                    lam = lam * ghost_life / FRAME  # ghosts there now: born at any time before
                    lam_prior = lam_prior * ghost_life / FRAME
                ch = hy.child(hy.logw + math.log(lam.sum() * last))
                ghost_ms.append(ch.logw)
                ghost_ls.append(ch.logw + math.log(lam_prior.sum() / lam.sum()))
                ch.kind[seg] = "g"
                ch.ghost[seg] = _log(lam / lam.sum())
                ch.phantom[seg] = source()
                children.append(ch)
                base = 0.0
            # somebody with tracks (of other sensors, or held ones)
            for gid, obj in hy.groups.items():
                if gid == cur or measured_by_si(gid, hy):
                    continue
                new, L = attach(obj)
                if L == -math.inf:
                    continue
                ch = released(hy.child(hy.logw + base + L))
                ch.groups[gid] = new
                ch.kind[seg] = gid
                children.append(ch)
            # somebody without a track: from their tiles
            counts = {}
            for u in hy.hidden:
                counts.setdefault(id(u), [u, 0])[1] += 1
            for u, mult in counts.values():
                new, L = attach(u)
                if L == -math.inf:
                    continue
                ch = released(hy.child(hy.logw + base + L + math.log(mult)))
                ch.hidden.remove(u)
                gid = next(self._gids)
                ch.groups[gid] = new
                ch.kind[seg] = gid
                children.append(ch)
            # somebody nobody knew of (the unknown ones stay as they are: a birth is one point of
            # the Poisson process, B_GarciaFernandez2018 eq. 18-24)
            new, L = attach(hy.ppp)
            if L > -math.inf:
                ch = released(hy.child(hy.logw + base + L))
                gid = next(self._gids)
                ch.groups[gid] = new
                ch.kind[seg] = gid
                children.append(ch)
        if not children:
            return
        # the factor by which the map at z moved the odds of "ghost" (for what the map learns, _end)
        map_odds = _logsumexp(ghost_ms) - _logsumexp(ghost_ls) if not refind and ghost_ms else 0.0
        top = max(c.logw for c in children)
        self.hyps = [c for c in children if c.logw > top + math.log(m.hyp_floor)] if top > -math.inf else children
        self._version += 1
        if refind:
            info["lost"] = None
            info["t"] = info["zt"] = info["gt"] = t
            info["z"] = d.pos.copy()
        else:
            self.segs[seg] = {"si": si, "t": t, "zt": t, "var": var, "z": d.pos.copy(), "z0": d.pos.copy(), "born": t,
                              "lost": None, "map_odds": map_odds}
            self._live_by_sensor[si].append(seg)
        self._merge()
        self._prune()

    # ------------------------------------------------------- bookkeeping

    @staticmethod
    def _mix_hidden(parts) -> Hidden:
        """sum_i w_i * density_i for [(w_i, Hidden)]."""
        merged = {}
        for w, u in parts:
            merged[id(u)] = (merged.get(id(u), (0.0, u))[0] + w, u)
        vals = list(merged.values())
        return vals[0][1] if len(vals) == 1 else Hidden.mixture(vals)

    @staticmethod
    def _pair(base: list, other: list) -> list:
        """other reordered so that other[j] is the person most like base[j]: people without a track
        are exchangeable, so the pairing with the least summed L1 distance (MODEL.md 5.6)."""
        if len(base) <= 1 or len(base) > 6:  # more than six never happens in a household
            return list(other)
        cost = np.array([[a.distance(b) for b in other] for a in base])
        best = min(itertools.permutations(range(len(other))), key=lambda p: sum(cost[i, j] for i, j in enumerate(p)))
        return [other[j] for j in best]

    def _merge(self):
        """Hypotheses that now say the same about every live track are one: their mixture (MODEL.md
        5.6). People held by the same object in all of them stay as they are; the others are mixed
        per person (exact for one, the multi-Bernoulli approximation for more)."""
        self._version += 1  # the hypotheses change: cached per-hypothesis values are void
        by = {}
        for hy in self.hyps:
            by.setdefault(hy.key(), []).append(hy)
        if len(by) == len(self.hyps):
            return
        out = []
        for hs in by.values():
            if len(hs) == 1:
                out.append(hs[0])
                continue
            lw = np.array([h.logw for h in hs])
            tot = _logsumexp(lw)
            wn = np.exp(lw - tot) if tot > -math.inf else np.full(len(hs), 1.0 / len(hs))
            base = hs[0]
            new = base.child(tot)
            for gid, segs in base.group_segs().items():
                objs = []
                for h in hs:
                    g2 = next(k for k, v in h.group_segs().items() if v == segs)
                    objs.append(h.groups[g2])
                if all(o is objs[0] for o in objs):
                    continue
                merged = {}
                for w, o in zip(wn, objs):
                    merged[id(o)] = (merged.get(id(o), (0.0, o))[0] + w, o)
                new.groups[gid] = Gauss.mixture(list(merged.values()))
            for seg_ in base.phantom:
                merged = {}
                for w, h in zip(wn, hs):
                    o = h.phantom[seg_]
                    merged[id(o)] = (merged.get(id(o), (0.0, o))[0] + w, o)
                if len(merged) > 1:
                    new.phantom[seg_] = Gauss.mixture(list(merged.values()))
            # people without a track: the objects all hold stay, the others are paired and mixed
            common = list(base.hidden)
            for h in hs[1:]:
                rest = list(h.hidden)
                keep = []
                for u in common:
                    if any(u is r for r in rest):
                        keep.append(u)
                        rest.remove(next(r for r in rest if r is u))
                common = keep
            rests = []
            for h in hs:
                rest = list(h.hidden)
                for u in common:
                    rest.remove(next(r for r in rest if r is u))
                rests.append(rest)
            rests = [rests[0]] + [self._pair(rests[0], r) for r in rests[1:]]
            mixed = [self._mix_hidden([(w, rest[j]) for w, rest in zip(wn, rests)]) for j in range(len(rests[0]))]
            new.hidden = common + mixed
            new.ppp = self._mix_hidden([(w, h.ppp) for w, h in zip(wn, hs)])
            out.append(new)
        self.hyps = out

    def _prune(self):
        """Keep the strongest hypotheses (Vo et al. 2017: cutting by weight minimizes the L1 error)."""
        self._version += 1
        self.hyps.sort(key=lambda h: -h.logw)
        top = self.hyps[0].logw
        if top == -math.inf:  # nothing explains the data (should not happen): keep them, see _normalize
            self.hyps = self.hyps[:self.m.max_hyps]
            return
        self.hyps = [h for h in self.hyps[:self.m.max_hyps] if h.logw > top + math.log(self.m.hyp_floor)]

    def _normalize(self):
        total = _logsumexp([h.logw for h in self.hyps])
        if total == -math.inf:
            # nothing explains the data (should not happen): keep the hypotheses, equal weights
            for h in self.hyps:
                h.logw = -math.log(len(self.hyps))
            return
        for h in self.hyps:
            h.logw -= total
        self.loglik += total

    def hyp_weights(self) -> np.ndarray:
        lw = np.array([h.logw for h in self.hyps])
        w = np.exp(lw - lw.max())
        return w / w.sum()

    def check(self):
        """The bookkeeping's invariants (for tests)."""
        keys = set()
        for hy in self.hyps:
            k = hy.key()
            assert k not in keys, "two hypotheses alike"
            keys.add(k)
            assert set(hy.kind) == set(self.segs), "hypothesis and live tracks differ"
            gs = hy.group_segs()
            assert set(gs) == set(hy.groups), "a person with tracks and no object, or the reverse"
            for gid, obj in hy.groups.items():
                assert isinstance(obj, Gauss), "a person with tracks not as a Gauss"
                assert gs[gid] <= obj.segs, "a track of a person without its offset"
            for s, k in hy.kind.items():
                if k == "g":
                    assert s in hy.phantom and s in hy.ghost, "a ghost without its source"
            assert isinstance(hy.ppp, Undetected), "no unknown people"
            assert all(type(u) is Hidden for u in hy.hidden), "a known person as an intensity"

    # -------------------------------------------------------------- LD2410C

    def _ld_geometry(self, si: int, pos) -> tuple:
        """(degrees off the LD2410C's axis, slant distance, in sight) of the points (n, 2)."""
        s = self.config.sensors[si]
        pos = np.asarray(pos, dtype=float).reshape(-1, 2)
        dx, dy = pos[:, 0] - s.x, pos[:, 1] - s.y
        ahead = dx * s._cos + dy * s._sin
        side = dx * s._sin - dy * s._cos
        angle = np.degrees(np.abs(np.arctan2(side, np.maximum(ahead, 1e-9))))
        slant = np.sqrt(dx * dx + dy * dy + (s.height - self.p.target_height) ** 2)
        return angle, slant, (ahead > 0) & (self._g(si, pos) > 0)

    def _ld_values(self, si: int, kind: str, d) -> tuple:
        """What an LD2410C factor depends on, per person position (MODEL.md 4.3): for "off" and
        "rise", how surely it sees a person there [still, walking] (its cone and reach) - for "rise"
        times the density of the reported distance d; for "on" and "fall", whether a person there
        can hold it on (its wider beam, at about the reported distance). Returns (per tile still,
        per tile walking, function of positions (n, 2) -> (still, walking))."""
        m = self.m

        def cone(angle, edges):
            full, none = edges
            return np.clip((none - angle) / (none - full), 0.0, 1.0)

        def values(pos):
            angle, slant, sight = self._ld_geometry(si, pos)
            if kind in ("off", "rise"):
                v = cone(angle, m.ld_cone) * sight
                out = [v * radar_pd(slant, m.ld_reach[STILL]), v * radar_pd(slant, m.ld_reach[WALK])]
                if kind == "rise" and d is not None:
                    dens = np.exp(-0.5 * ((slant - d) / m.ld_spread) ** 2) / (math.sqrt(2 * math.pi) * m.ld_spread)
                    out = [o * dens for o in out]
                return out
            v = cone(angle, m.ld_beam) * sight
            if d is not None:
                v = v * np.exp(-0.5 * ((slant - d) / m.ld_spread) ** 2)
            return [v, v]

        key = ("ld", si, kind, d)
        if key not in self._gcache:
            tl = self.tiles
            vs, vw = values(tl.points)
            if len(self._gcache) > 4096:
                self._gcache = {k: v for k, v in self._gcache.items() if not (isinstance(k, tuple) and k[0] == "ld")}
            self._gcache[key] = (tl.average(vs), tl.average(vw))
        vs, vw = self._gcache[key]
        return vs, vw, values

    def _ld_frame(self, si, rt, t, ld):
        """The LD2410C flag of this frame (MODEL.md 4.3): the time since the last frame counts as
        off or on (weighed with the next move of everybody); a change is an event now. With the
        distance it reports (slant, m; the still one when it says still, else the moving one)."""
        on = bool(ld.get("moving") or ld.get("still"))
        dist = (ld.get("still_distance") if ld.get("still") else ld.get("moving_distance")) or 0
        d = round(dist / 250) * 0.25 if on and dist > 0 else None  # the 0.75 m gates, finer than needed
        before, since, d_before = rt.ld_on, rt.ld_t, rt.ld_d
        rt.ld_on, rt.ld_t, rt.ld_d = on, t, d
        if before is None or t - since > IDLE:
            return  # nothing known before (start, lost data)
        key = (si, before, d_before if before else None)
        self._ld_pending[key] = self._ld_pending.get(key, 0.0) + (t - since)
        if on and not before:
            self._ld_weigh(si, "rise", d=d)
        elif before and not on:
            self._ld_weigh(si, "fall", d=d_before)
        else:
            return
        self._version += 1

    def _ld_share(self, obj, vals) -> float:
        """E[the value] for a person (or the expected sum over the unknown ones)."""
        vs, vw, fn = vals
        if isinstance(obj, Hidden):
            return float(obj.walk @ vw + obj.still.sum(axis=(0, 1)) @ vs)
        w = obj.weights()
        v = fn(obj.pos)
        out = (1 - obj.a) * (w[STILL] * v[0][STILL] + w[WALK] * v[1][WALK])
        if obj.away is not None:
            out += obj.a * float(obj.away.walk @ vw + obj.away.still.sum(axis=(0, 1)) @ vs)
        return float(out)

    def _ld_apply(self, obj, vals, f) -> float:
        """Multiply a person by f(value), per tile or per component; where the LD2410C can't see
        them (behind doors, out of the house) by f(0)."""
        vs, vw, fn = vals
        f0 = float(f(0.0))
        if isinstance(obj, Hidden):
            return obj.weigh(f(vw) / f0, f(vs) / f0) + (math.log(f0) if not isinstance(obj, Undetected) else 0.0)
        v = fn(obj.pos)
        dlog = np.log(np.maximum([f(v[0][STILL]), f(v[1][WALK])], 1e-300))
        da = obj.away.weigh(f(vw) / f0, f(vs) / f0) + math.log(f0) if obj.away is not None else 0.0
        return obj.reweigh(dlog, da)

    def _ld_weigh(self, si, kind, E=0.0, d=None):
        """The LD2410C flag (MODEL.md 4.3). Off for E s: nobody turned it on (a rate per person, a
        product over the people, like the LD2450's tracks). Turning on (at the reported distance d),
        on for E s, turning off: whether anybody is there - not a product over the people: each
        hypothesis gets its exact factor (the people independent given it), each person the factor
        given the others, mixed over the hypotheses holding them (the marginals of JIPDA; what the
        others explain says nothing about this person, 0.6.13)."""
        m = self.m
        a0 = m.ld_rate
        k, c = 1.0 / m.ld_blip, 1.0 / m.ld_hold
        vals = self._ld_values(si, kind, d)
        objs = self._objects()
        if kind == "off":
            for obj, refs in objs:
                dl = self._ld_apply(obj, vals, lambda v: np.exp(-a0 * v * E))
                for h in refs:
                    self.hyps[h].logw += dl - m.ld_blips * E
            return
        # blips report any distance up to the farthest gate
        b = m.ld_blips / m.ld_max if (kind == "rise" and d is not None) else m.ld_blips
        S = {id(o): self._ld_share(o, vals) for o, _ in objs}
        none = np.zeros(len(self.hyps))  # P(nobody holds it)
        rate = np.zeros(len(self.hyps))  # expected rate (density) of turning it on
        for h, hy in enumerate(self.hyps):
            people = [S[id(o)] for o in hy.people()]
            lam = S[id(hy.ppp)]
            none[h] = float(np.prod([1 - x for x in people])) * math.exp(-lam)
            rate[h] = b + a0 * (sum(people) + lam)
            if kind == "rise":
                hy.logw += math.log(rate[h])
            elif kind == "fall":
                hy.logw += math.log(c + (k - c) * none[h])
            else:
                hy.logw += -c * E - (k - c) * E * none[h]
        hw = self.hyp_weights()
        for obj, refs in objs:
            if isinstance(obj, Undetected):
                continue
            w = hw[refs]
            if w.sum() <= 0:
                continue
            if kind == "rise":
                other = float(w @ (rate[refs] - a0 * S[id(obj)]) / w.sum())
                self._ld_apply(obj, vals, lambda v: other + a0 * v)
                continue
            # P(none of the others holds it), per hypothesis without this person
            rest = []
            for h in refs:
                hy = self.hyps[h]
                others = list(hy.people())
                others.remove(obj)
                rest.append(float(np.prod([1 - S[id(o)] for o in others])) * math.exp(-S[id(hy.ppp)]))
            P = float(w @ np.array(rest) / w.sum())
            if kind == "fall":
                self._ld_apply(obj, vals, lambda v: c + (k - c) * (1 - v) * P)
            else:
                self._ld_apply(obj, vals, lambda v: np.exp(-(k - c) * E * (1 - v) * P))
        self._normalize()

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

    # -------------------------------------------------------------- outputs

    def _in_view(self, obj) -> np.ndarray:
        """Mass per tile in view (n,)."""
        if isinstance(obj, Hidden):
            return obj.in_view()
        out = (1 - obj.a) * obj.tile_mass(self.tiles)
        return out + obj.a * obj.away.in_view() if obj.away is not None else out

    def _room_probs(self, obj) -> np.ndarray:
        """P(the person is in each scored room), and in view at all (last entry)."""
        R = len(self.rooms)
        mass = self._in_view(obj)
        ok = self.tiles.room >= 0
        out = np.bincount(self.tiles.room[ok], weights=mass[ok], minlength=R) if R else np.zeros(0)
        return np.concatenate([out, [mass.sum()]])

    def _place_probs(self, obj) -> np.ndarray:
        if isinstance(obj, Hidden):
            return obj.places()
        out = np.zeros(len(self.world.places))
        out[OBSERVED] = 1.0 - obj.a
        return out + obj.a * obj.away.places() if obj.away is not None else out

    @staticmethod
    def _poisson_binomial(ps, means) -> np.ndarray:
        """The number of people, per column: one Bernoulli per known person (ps (P, C)), plus a
        Poisson number of unknown ones (means (C,)), cut where the rest is below 1e-9. (C, size),
        a column's entries beyond its own cut 0."""
        C = len(means)
        dist = np.ones((C, 1))
        for q in ps:
            nxt = np.zeros((C, dist.shape[1] + 1))
            nxt[:, :-1] = dist * (1 - q)[:, None]
            nxt[:, 1:] += dist * q[:, None]
            dist = nxt
        some = np.flatnonzero(means > 0)
        if len(some):
            mean = means[some][:, None]
            cut = (mean + 10 * np.sqrt(mean) + 6).astype(int)
            k = np.arange(int(cut.max()))
            pois = np.exp(k * np.log(mean) - mean - np.array([math.lgamma(i + 1) for i in k]))
            pois[k >= cut] = 0.0
            out = np.zeros((C, dist.shape[1] + len(k) - 1))
            out[means <= 0, :dist.shape[1]] = dist[means <= 0]
            for i in range(dist.shape[1]):
                out[some, i:i + len(k)] += dist[some, i:i + 1] * pois
            dist = out
        return dist

    def _counts(self, per_object) -> np.ndarray:
        """(columns, K): count distributions per column of per_object(obj) - for a known person the
        probabilities, for the unknown ones the expected numbers - mixed over the hypotheses, all
        columns at once."""
        hw = self.hyp_weights()
        objs = [obj for obj, _ in self._objects()]
        vals = np.array([np.atleast_1d(per_object(o)) for o in objs])
        row = {id(o): i for i, o in enumerate(objs)}
        dists = [(w, self._poisson_binomial(vals[[row[id(o)] for o in hy.people()]], vals[row[id(hy.ppp)]]))
                 for w, hy in zip(hw, self.hyps)]
        out = np.zeros((vals.shape[1], max(d.shape[1] for _, d in dists)))
        for w, d in dists:
            out[:, :d.shape[1]] += w * d
        return out

    def _cached(self, name, make):
        """Outputs are computed once per move of everybody (MAX_STEP) or event (_flush)."""
        key = (name, self._version)
        if self._out_cache.get(name, (None,))[0] != key:
            self._out_cache[name] = (key, make())
        return self._out_cache[name][1]

    def count_distribution(self) -> dict:
        """room id -> [P(0 people), P(1), ...] over the observed rooms, and "_observed"."""
        return self._cached("counts", self._count_distribution)

    def _count_distribution(self) -> dict:
        c = self._counts(self._room_probs)
        out = {rid: c[k].tolist() for k, rid in enumerate(self.rooms)}
        out["_observed"] = c[-1].tolist()
        return out

    def place_distribution(self) -> dict:
        """place name -> [P(0 people), P(1), ...], and "_house" (anywhere but outside)."""
        return self._cached("places", self._place_distribution)

    def _place_distribution(self) -> dict:
        def with_house(o):
            p = self._place_probs(o)
            return np.append(p, p[:-1].sum())
        c = self._counts(with_house)
        out = {name: c[k].tolist() for k, name in enumerate(self.world.places)}
        out["_house"] = c[-1].tolist()
        return out

    def present_count(self) -> int:
        """The most probable number of people in view."""
        return int(np.argmax(self.count_distribution()["_observed"]))

    def persons(self) -> list:
        """The known people of the most probable hypothesis, for the display: those with a track
        first."""
        hy = self.hyps[int(np.argmax([h.logw for h in self.hyps]))]
        return [self._display(k + 1, obj) for k, obj in enumerate(hy.people())]

    def _display(self, pid, obj) -> dict:
        """Where to draw a person: with a measuring track the mean of the mixture (it moves as
        smoothly as the estimate does); without, the densest tile of the most probable place."""
        pr = self._place_probs(obj)
        best = int(np.argmax(pr))
        out = {"id": pid, "places": {self.world.places[i]: round(float(v), 3) for i, v in enumerate(pr) if v >= 0.005},
               "lost": isinstance(obj, Hidden), "obj": obj}
        if best != OBSERVED:
            out.update({"x": None, "y": None, "vx": 0.0, "vy": 0.0, "sigma": 0.0, "walk": 0.0})
            return out
        if isinstance(obj, Hidden):
            tl = self.tiles
            mass = obj.in_view()
            c = int(np.argmax(mass / tl.area))
            x, y = tl.centers[c]
            spread = mass @ (((tl.centers - [x, y]) ** 2).sum(axis=1) + tl.var.sum(axis=1))
            out.update({"x": round(float(x), 3), "y": round(float(y), 3), "vx": 0.0, "vy": 0.0,
                        "sigma": round(math.sqrt(float(spread) / max(mass.sum(), 1e-12)), 3),
                        "walk": round(float(obj.walking().sum() / max(mass.sum(), 1e-12)), 3)})
            return out
        w = obj.weights()
        pos = w @ obj.pos
        vel = w @ obj.mean[:, :, 1]
        spread = w @ (((obj.pos - pos) ** 2).sum(axis=1) + obj.pos_var().sum(axis=1))
        out.update({"x": round(float(pos[0]), 3), "y": round(float(pos[1]), 3), "vx": round(float(vel[0]), 3),
                    "vy": round(float(vel[1]), 3), "sigma": round(math.sqrt(float(spread)), 3),
                    "walk": round(float(w[WALK]), 3)})
        return out

    def _cloud(self, pid, obj) -> dict:
        """Where a person may be, for the map: with a track their Gaussians [[weight, x, y, sd x,
        sd y]] as they are; without (and their part gone through a door), [[tile, mass per m^2]]
        of the tiles holding at least 0.2 % (anywhere in a tile alike: all the model knows about
        where an unseen person is; the tiles' squares: /api/tiles)."""
        tl = self.tiles
        out = {"id": pid, "gauss": [], "tiles": []}
        if isinstance(obj, Hidden):
            mass = obj.in_view()
        else:
            sd = np.sqrt(obj.pos_var())
            out["gauss"] = [[round(float((1 - obj.a) * w), 3), round(float(obj.pos[k][0]), 3), round(float(obj.pos[k][1]), 3),
                             round(float(sd[k][0]), 3), round(float(sd[k][1]), 3)]
                            for k, w in enumerate(obj.weights()) if (1 - obj.a) * w >= 0.01]
            mass = obj.a * obj.away.in_view() if obj.away is not None else np.zeros(tl.n)
        k = np.flatnonzero(mass > 0.002)
        out["tiles"] = [[int(i), round(float(mass[i] / tl.area[i]), 4)] for i in k]
        return out

    def zone_states(self) -> dict:
        """Per zone: the most probable number of people (observed rooms: from all hypotheses),
        moving / still and "about to be entered" from the most probable hypothesis' people.
        Occupied, where the probability is known: P(somebody there) above the threshold that
        minimizes the expected cost of the light (MODEL.md 6)."""
        from .zones import ZoneState
        p = self.p
        c = p.light_cost / (p.light_cost + 1.0)
        states = {}
        region_of = {room: rid for rid, r in self.config.regions.items() for room in r["rooms"]}
        counts = self.count_distribution()
        places = self.place_distribution()
        for z in self.config.zones_of("room"):
            st = ZoneState()
            rid = region_of.get(z.id)
            if rid is None:
                if z.id in counts:
                    st.count = int(np.argmax(counts[z.id]))
                    st.probability = 1 - float(counts[z.id][0])
                    st.decided = st.probability > c
            else:
                dist = places.get(rid, [1.0])
                st.probability = 1 - float(dist[0])
                if len(self.config.regions[rid]["rooms"]) == 1:
                    st.count = int(np.argmax(dist))
                    st.decided = st.probability > c
            states[z.id] = st
        total = ZoneState()
        zones = [z for z in self.config.zones if z.kind in ("room", "area")]
        for z in zones:
            states.setdefault(z.id, ZoneState())
        total.count = int(np.argmax(places["_house"]))
        for d in self.persons():
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

    def snapshot(self) -> dict:
        """For the web UI: the people of the most probable hypothesis at their most probable place,
        their densities as heat maps, the sensors' raw data, the places without a sensor."""
        t = self.now
        tracks, clouds = [], []
        for d in self.persons():
            obj = d.pop("obj")
            room = None if d["x"] is None else next(
                (z.id for z in self.config.zones_of("room") if z.contains(d["x"], d["y"])), None)
            tracks.append({**d, "room": room})
            clouds.append(self._cloud(d["id"], obj))
        sensors = {}
        for sid, rt in self.runtime.items():
            sensors[sid] = {
                "online": t - rt.last_frame < 15,
                "detections": [{"x": round(float(d.pos[0]), 3), "y": round(float(d.pos[1]), 3),
                                "lx": round(d.local[0], 3), "ly": round(d.local[1], 3),
                                "speed": round(d.speed, 2), "hidden": d.hidden or d.stale}
                               for d in rt.detections] if t - rt.last_frame < 1.0 else [],
                "ld2410": {"present": rt.ld_present, "distance": round(rt.ld_distance, 2),
                           "move_gates": rt.move_gates, "still_gates": rt.still_gates},
            }
        places = self.place_distribution()
        regions = {}
        for rid, r in self.config.regions.items():
            dist = places.get(rid, [1.0])
            regions[rid] = {"name": r["name"], "rooms": r["rooms"], "open": r["open"],
                            "count": int(np.argmax(dist)),
                            "probabilities": [round(1 - dist[0], 3)] if 1 - dist[0] >= 0.005 else [],
                            "dwell": self.dwell.stats(rid)}
        return {"t": t, "tracks": tracks, "clouds": clouds, "tiling": id(self.tiles), "sensors": sensors, "regions": regions}

    # ------------------------------------------------------------- learned

    def learned(self) -> dict:
        return {"ghost_map": self.ghost_map.to_dict()}

    def load_learned(self, data: dict):
        if data.get("ghost_map"):  # learned offline (tools/ghostmap.py), used if the sensors are where they were
            from .ghostmap import GhostMap
            self.use_ghost_map(GhostMap.from_dict(data["ghost_map"]))
