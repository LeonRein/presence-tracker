"""The Bayes filter of MODEL.md: everybody's whereabouts given the LD2450 tracks and the LD2410C energies (4.3). Deterministic:
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

import binascii
import itertools
import math
import zlib

import numpy as np

from .filtermodel import FRAME, IDLE, P_FA, S50, STILL, WALK, Model, Shapes
from .frames import SensorRuntime, detections as frame_detections
from . import kernels, ld2410
from . import gauss
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
GIVE_UP = 0.01  # a known person who exists with less probability (wherever they are, out of the house
                # too: there the existence fades as anywhere) joins the unknown ones with r x their density:
                # only P(several of them come back) changes, by <= GIVE_UP^2 / 2 (0.05: 14 % less
                # computing time, light wrongly off 1.67 instead of 1.17)
RECYCLE_EVERY = 1.0  # s
GAP_EXACT = 120.0  # s: the end of a gap in the data is moved as always, what lies before in leaps
LEAP = 15.0  # s   (_predict_gap; 7.10. 08:06, 8 people, 3 h: rooms within 0.022 of moving all as always)
MAX_LEAPS = 2000  # longer gaps in longer leaps (8 h of 8 people: 1.7 s CPU; a week in leaps of 5 min,
                  # 8 h in leaps of 4 min put the rooms up to 0.12 off)


_LGAMMA = np.zeros(0)


def _lgamma_table(n: int) -> np.ndarray:
    """lgamma(i + 1) for i < n (at least)."""
    global _LGAMMA
    if len(_LGAMMA) < n:
        _LGAMMA = np.array([math.lgamma(i + 1) for i in range(max(n, 64))])
    return _LGAMMA


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
        """The known people: with tracks (they exist), without (Bernoullis, Hidden.r)."""
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
        """What the hypothesis says about the live tracks. Hypotheses that differ only in the known
        people without a track (how many, where) are merged (MODEL.md 5.6): a person missing in
        some of them becomes a Bernoulli with r < 1."""
        return (frozenset(frozenset(v) for v in self.group_segs().values()),
                frozenset(s for s, k in self.kind.items() if k == "g"))


class Tracker:
    def __init__(self, config, start: float | None = None, n: int | None = None, seed: int = 1, people=None,
                 sensor_model: SensorModel | None = None, model: Model | None = None):
        # n and seed are accepted for the tools' sake; nothing here is random
        self.m = model or Model()
        self.shapes = Shapes(self.m)
        self.config = config
        self.p = config.params
        self.sensor_model = sensor_model or SensorModel(config)
        self.dwell = Dwell(self.p)
        self.listeners = []
        self.runtime = {}
        self.now = start or 0.0
        self.start = start
        self.loglik = 0.0  # log evidence of everything seen so far
        self._ids = itertools.count(1)
        self._gids = itertools.count(1)
        self.ghost_map = None  # where each sensor starts ghost tracks (ghostmap.py)
        # what each LD2410C sees without anybody (ld2410.py, learned online, MODEL.md 4.3)
        self.ld_background = ld2410.Background(ld2410.prior(self.m), self.m.ld_prior_time, self.m.ld_forget,
                                               self.m.ld_echo_rate, self.m.ld_echo_prior_time)
        self.ld_background.use(config)
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
        rooms = self.config.observed_rooms()
        self.rooms = [z.id for z in rooms]
        w = self.world
        self.room_of = np.full((w.nx, w.ny), -1, dtype=np.int16)
        for k, z in enumerate(rooms):
            self.room_of[w.zone_mask(z) & (w.labels == OBSERVED)] = k
        self.tiles = Tiling(self)
        for si in range(len(self.sensors)):
            self._g(si, np.zeros((1, 2)))

    def reset_people(self, people=None):
        """Start over: nothing known about the people (MODEL.md 5.3, unknown_start); or people at
        the given places ("anywhere" or a place name), nobody unknown (for tests and tools)."""
        if people is None:
            hidden, ppp = [], self.unknown_start()
        else:
            anywhere = None
            hidden = []
            for where in people:
                if where == "anywhere":
                    anywhere = anywhere or Hidden.anywhere(self.tiles)
                    hidden.append(anywhere)
                else:
                    hidden.append(Hidden.at_place(self.tiles, self.world.index[where]))
            ppp = Undetected.none(self.tiles)
        self.hyps = [Hyp(0.0, {}, {}, {}, hidden, ppp)]
        self._clear()
        self.started_from = None  # the saved state the people were restored from (restore_people)

    def unknown_start(self) -> Undetected:
        """Nothing known (MODEL.md 5.3): no known people; unknown ones (5.5) as a Poisson intensity
        with start_unknown people expected, anywhere (in view standing, their stays seen at a
        random moment; behind doors; out). No fixed number: the data make known people of them,
        through the tracks there at the start (4.1) and the ways in. Without any in view, whoever
        sits in view at the start can only be a ghost (the desk at 22:19 and 08:06); with all of
        them behind doors more come out of the doors than anybody does (6./7.10.: light wrongly
        on 0.57 instead of 0.14 of 41)."""
        return Undetected.anywhere(self.tiles, people=self.m.start_unknown)

    def _clear(self):
        """No live tracks, no data of the sensors gathered yet."""
        self._recycled = self.now
        self._flushed = self.now
        self._gap_from = None  # time of a restored state: the gap to the first frame is still to predict
        self._out_cache = {}
        self._ld_stats = {}  # sensor index -> the LD2410C's frames since they were last weighed (ld2410.Stats)
        self._ld_memory = {}  # sensor index -> what the people put into its cells lately (the still ones lag)
        self._ld_echo = {}  # sensor index -> its echo sources (ld2410.Echoes)
        self._version = 0  # counts the moves of everybody and the events (for the outputs' cache)
        self.segs = {}  # seg id -> dict(si, t, zt, var, z, born, lost, ...)
        self._live_by_sensor = {si: [] for si in range(len(self.sensors))}

    def people_state(self) -> dict:
        """What is known about the people, to start from after a restart (MODEL.md 5.3): per
        hypothesis its weight, its known people and the unknown ones, as densities over the tiles.
        Live tracks do not survive a restart (the sensors' next frames are a start, 4.1): the
        people with tracks go to the tiles as when their last track ends (5.4), ghost tracks end,
        and the hypotheses that then say the same are merged (5.6) - one per number of known
        people. The time is that of the last move of everybody. Changes nothing here."""
        cache = {}
        hyps = []
        for hy in self.hyps:
            people = list(hy.hidden)
            for g in hy.groups.values():
                if id(g) not in cache:
                    cache[id(g)] = g.to_tiles(self.tiles)
                people.append(cache[id(g)])
            hyps.append(Hyp(hy.logw, {}, {}, {}, people, hy.ppp))
        hyps = self._merged(hyps)
        total = _logsumexp([hy.logw for hy in hyps])
        index = {}
        for hy in hyps:
            for o in hy.hidden + [hy.ppp]:
                index.setdefault(id(o), (len(index), o))
        return {"t": self._flushed, "tiles": self.tiles.fingerprint(),
                "densities": [o.to_dict() for _, o in sorted(index.values(), key=lambda v: v[0])],
                "hyps": [{"logw": hy.logw - total, "people": [index[id(o)][0] for o in hy.hidden],
                          "unknown": index[id(hy.ppp)][0]} for hy in hyps]}

    def restore_people(self, state: dict | None) -> bool:
        """Start from a saved state (people_state): the posterior then is the prior now (the PMBM
        recursion goes on from it, B_GarciaFernandez2018), moved on over the time between (a gap in
        the data says nothing, MODEL.md 4.4) - at the first frame if the model has not started yet.
        False, and nothing changed, if there is none or it does not fit the tiles (another floor
        plan)."""
        if not state or state.get("tiles") != self.tiles.fingerprint():
            return False
        try:
            unknown = {int(h["unknown"]) for h in state["hyps"]}
            objs = [(Undetected if k in unknown else Hidden).from_dict(self.tiles, d)
                    for k, d in enumerate(state["densities"])]
            hyps = [Hyp(float(h["logw"]), {}, {}, {}, [objs[int(i)] for i in h["people"]], objs[int(h["unknown"])])
                    for h in state["hyps"]]
            t = float(state["t"])
        except (KeyError, ValueError, TypeError, IndexError, zlib.error, binascii.Error):
            return False
        if (not hyps or not math.isfinite(t) or not all(math.isfinite(hy.logw) for hy in hyps)
                or any(type(hy.ppp) is not Undetected or any(type(u) is not Hidden for u in hy.hidden) for hy in hyps)):
            return False
        total = _logsumexp([hy.logw for hy in hyps])
        for hy in hyps:
            hy.logw -= total
        self.hyps = hyps
        self._clear()
        self.started_from = state
        if self.start is None:
            self._gap_from = t
        else:
            self._predict_gap(self.now - t)
        return True

    def _predict_gap(self, gap: float):
        """Everybody moves on over a gap in the data (MODEL.md 4.4, 5.3): its last GAP_EXACT s as
        always (Hidden.move), what lies before in steps of LEAP s (Hidden.leap), at most MAX_LEAPS."""
        if gap <= 0:
            return
        lead = max(gap - GAP_EXACT, 0.0)
        leaps = min(int(math.ceil(lead / LEAP - 1e-9)), MAX_LEAPS)
        parts = int(math.ceil((gap - lead) / MAX_STEP - 1e-9))
        for obj, _ in self._objects():
            for _ in range(leaps):
                obj.leap(lead / leaps)
            for _ in range(parts):
                obj.move((gap - lead) / parts)
            self._fade(obj, gap)
        self._version += 1

    def _fade(self, obj, dt: float):
        """A known person without a track exists on with exp(-dt / record_life), wherever they are
        (Musicki & Evans 2005: the existence of a track as a Markov chain, p11 < 1); measurements
        that support them lift r again (MODEL.md 5.5). None: always."""
        life = self.m.record_life
        if life and type(obj) is Hidden and obj.r > 0:
            obj.r *= math.exp(-dt / life)

    def reconfigure(self, config):
        """A new configuration. If only sensors changed (turned, moved, recalibrated, switched on
        or off) and the world the people live in stays the same, what is known about the people
        stays: a recalibration tells something about a sensor, nothing about them. The changed
        sensors start over as after lost data (MODEL.md 4.1, 5.3): their tracks end without
        information, their next frame is a start. Otherwise (walls, rooms, doors, parameters, the
        set of sensors, the observed area) the model starts over as after a restart: from what it
        knew (people_state) if the tiles stay the same, else from nothing known. Returns whether it
        started over."""
        changed = self._changed_sensors(self.config, config)
        state = self.people_state() if changed is None else None
        self.config = config
        self.p = config.params
        self.sensor_model.config = config
        self.sensor_model.rebuild()
        if changed is not None:
            self._recalibrated(changed)
        else:
            self.dwell = Dwell(self.p)
            self._build()
        self.use_ghost_map(self.ghost_map)  # it holds only while the sensors are where they were
        self.ld_background.use(config)
        if changed is None:
            if not self.restore_people(state):
                self.reset_people()
            for rt in self.runtime.values():
                rt.last_frame = -math.inf  # the tracks start anew: the next frame is a start (_evidence)
        return changed is None

    def _changed_sensors(self, old, new):
        """The ids of the sensors that differ between two configurations if nothing else differs
        (the same sensors, floor plan and parameters, the same observed area), else None."""
        a, b = old.to_dict(), new.to_dict()
        sa, sb = a.pop("sensors"), b.pop("sensors")
        a.pop("background")
        b.pop("background")
        if a != b or [s["id"] for s in sa] != [s["id"] for s in sb] or old.regions != new.regions:
            return None
        if not np.array_equal(World(new).labels, self.world.labels):
            return None
        return [x["id"] for x, y in zip(sa, sb) if x != y]

    def _recalibrated(self, changed):
        """Sensors changed, the people's world not (reconfigure): the people stay as they are; the
        changed sensors' tracks end as at lost data, their next frame counts as a start."""
        if any(self._live_by_sensor[self.sidx[sid]] for sid in changed):
            self._flush(self.now)  # the ends weigh and release everybody: all at now
        for sid in changed:
            si = self.sidx[sid]
            for seg in list(self._live_by_sensor[si]):
                self._end(si, seg, self.now, True)
            self.tracks[sid] = SensorTracks(sid, self._ids)
            if sid in self.runtime:
                self.runtime[sid].last_frame = -math.inf  # the next frame is a start (_evidence)
            for d in (self._ld_stats, self._ld_memory, self._ld_echo):
                d.pop(si, None)
        if changed and self.hyps:
            self._merge()
            self._prune()
            self._normalize()
        self.world.config = self.config
        self._gcache, self._area, self._ghost_total, self._out_cache = {}, {}, {}, {}
        self.tiles.g, self.tiles.dist = {}, {}
        for si in range(len(self.sensors)):
            self._g(si, np.zeros((1, 2)))
        self._version += 1

    def use_ghost_map(self, gm) -> bool:
        """Use a learned ghost map (MODEL.md 4.2) if it was learned with the sensors where they are
        now; else start a new one from the prior (a moved, added or removed sensor changes where
        all of them see ghosts)."""
        from .ghostmap import GhostMap, pose_of
        new = GhostMap.for_world(self.world, sum(rate for rate, _ in self.m.ghost_types), self.m.ghost_prior_ghosts)
        # ... and with the prior it has now (a map learned with another one starts over too)
        ok = (gm is not None and gm.matches(self.config) and math.isclose(gm.prior_rate, new.prior_rate)
              and math.isclose(gm.prior_time, new.prior_time))
        if ok:
            self.ghost_map = gm
        else:
            self.ghost_map = new
            self.ghost_map.poses = {s.id: pose_of(s) for s in self.config.sensors}
        self._ghost_total = {}
        return ok

    def _ghost_types(self) -> tuple:
        """The kinds of ghosts ((rate, mean life s), ...): the model's, not learned online (MODEL.md
        4.2: how long ghosts live is what tells them from people; learned from the filter's own
        judgement it followed the people it took for ghosts)."""
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

    def _grid(self, si: int) -> np.ndarray:
        """How well sensor si sees each 0.1 m cell of the world, 0..1 (MODEL.md 4.1)."""
        grid = self._gcache.get(si)
        if grid is None:
            w = self.world
            sid = self.sensors[si]
            grid = np.zeros((w.nx, w.ny))
            for i in range(w.nx):
                for j in range(w.ny):
                    if w.labels[i, j] >= 0:
                        grid[i, j] = self.sensor_model.pd(sid, w.x0 + (i + 0.5) * CELL, w.y0 + (j + 0.5) * CELL) / PD_MAX
            # the LD2410C sees where the LD2450 in the same housing could see at all (its own beam
            # aside, ld2410.expected): through no wall
            los = (grid > 0).astype(float)
            if self.m.sight_spread:
                grid, los = self._spread_sight(si, grid), self._spread_sight(si, los)
            self._gcache[si] = grid
            self._gcache[("los", si)] = los
            self._area[si] = float((grid > 0.05).sum()) * CELL * CELL
        return grid

    def _spread_sight(self, si: int, grid: np.ndarray) -> np.ndarray:
        """The sight of a person, not of a point (MODEL.md 4.1): the sensor measures the person
        where its track sits on them, z = x + c + o + w, so it sees them as far as it sees that spot:
        the sight of points, averaged over the offset's distribution around x (Gaussian, per axis
        the variance of _offset_var at this distance), over what lies on the person's side of the
        walls (a body does not reach through a wall; through a door gap it does). Without it a
        wall's shadow is a sharp edge: a person standing in it is invisible though the track that
        is theirs sits in sight (6.10. 20:55, the hallway seen from the study), and strips of a
        room the sight just misses hold people no sensor can see (the kitchen along its wall,
        x 2.2-2.4)."""
        w = self.world
        s = self.config.sensors[si]
        cx = w.x0 + (np.arange(w.nx) + 0.5) * CELL
        cy = w.y0 + (np.arange(w.ny) + 0.5) * CELL
        dist = np.hypot(cx[:, None] - s.x, cy[None, :] - s.y)
        p = self.p
        var = 0.5 * ((p.range_sigma_base + p.range_sigma_slope * dist) ** 2
                     + (p.lateral_sigma_base + p.lateral_sigma_slope * dist) ** 2)
        inside = w.labels == OBSERVED
        reach_r = 2.5 * math.sqrt(float(var[inside].max())) if inside.any() else 0.0
        offs, mask = w.reach(min(reach_r, 1.2))
        num = np.zeros_like(grid)
        den = np.zeros_like(grid)
        pad = int(np.abs(offs).max()) if len(offs) else 0
        gp = np.pad(grid, pad)
        for k, (di, dj) in enumerate(offs):
            wk = np.exp(-0.5 * (di * di + dj * dj) * CELL * CELL / var) * mask[k]
            num += wk * gp[pad + di:pad + di + w.nx, pad + dj:pad + dj + w.ny]
            den += wk
        # in the places without a sensor nobody is in view (MODEL.md 2): as the points' sight
        return np.where((w.labels == OBSERVED) & (den > 0), num / np.maximum(den, 1e-300), grid)

    def _g(self, si: int, pos: np.ndarray) -> np.ndarray:
        """How well sensor si sees the points (..., 2), 0..1 (MODEL.md 4.1, from the geometry)."""
        grid = self._grid(si)
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

    def _los(self, si: int, pos: np.ndarray) -> np.ndarray:
        """How much of a person at the points (n, 2) is in the line of sight of sensor si (0..1,
        the LD2410C's sight; _grid)."""
        self._grid(si)
        i, j = self.world.cell_of(pos.reshape(-1, 2))
        return self._gcache[("los", si)][i, j]

    def _rates(self, si: int, pos: np.ndarray, skip=(), masked=True, base=None) -> np.ndarray:
        """(2, n) rates (1/s) [walkers, still people] at which sensor si starts a track on somebody
        at pos (n, 2): rho * P_D(r) * geometry (MODEL.md 4.1); masked: times the share left given
        its live tracks but skip - next to a target it measures, somebody is merged into it
        (resolution, Svensson 2012); near where it holds a lost target it finds that one again
        (kernels.sensor_rates). base: the rates without the mask at pos if known."""
        s, w, m = self.config.sensors[si], self.world, self.m
        (rs, r50s), (rw, r50w) = m.acquire
        live = [(0.0, *self.segs[seg]["z"]) if self.segs[seg]["lost"] is None else (1.0, *self.segs[seg]["lost"]["z"])
                for seg in (self._live_by_sensor.get(si, []) if masked else ()) if seg not in skip]
        return kernels.sensor_rates(np.ascontiguousarray(pos, dtype=float).reshape(-1, 2),
                                    np.zeros((2, 0)) if base is None else base, float(s.x), float(s.y), self._grid(si),
                                    float(w.x0), float(w.y0), CELL, np.array([rw, r50w, rs, r50s], dtype=float), P_FA,
                                    S50, np.array(live, dtype=float).reshape(-1, 3),
                                    np.array([m.res_range, m.res_cross], dtype=float), float(m.find_radius))

    def _tile_rates(self, si: int, skip=()) -> tuple:
        """(walkers, still people) rates (n,) of sensor si per tile (averaged over the tile)."""
        tl = self.tiles
        if not tl.n:
            return np.zeros(0), np.zeros(0)
        key = ("tiles", si)
        base = self._gcache.get(key)
        if base is None:
            at = self._rates(si, tl.points, masked=False)
            base = self._gcache[key] = (at, tl.average(at))
        if not any(s not in skip for s in self._live_by_sensor.get(si, [])):
            return base[1][0], base[1][1]
        r = tl.average(self._rates(si, tl.points, skip, base=base[0]))
        return r[0], r[1]

    def _gauss_rates(self, si: int, g: Gauss, skip=(), pts=None) -> np.ndarray:
        """(2, P) rate of sensor si per component [STILL, WALK] at its sigma points (gauss.UNIT, or
        pts (2, P, 2)); 0 where the person has a measuring track of this sensor: one person, one
        track per sensor."""
        if any(self.segs[s]["si"] == si and self._measuring(s) for s in g.slots if s in self.segs and s not in skip):
            return np.zeros((2, len(gauss.UNIT_W)))
        if pts is None:
            pts = g.sigma(self.world)
        n = pts.shape[1]
        r = self._rates(si, pts, skip)
        return np.stack([r[1, :n], r[0, n:]])

    @staticmethod
    def _expect(f) -> np.ndarray:
        """Expectation over the sigma points (last axis) of values at them."""
        return f @ gauss.UNIT_W

    # ------------------------------------------------------------- frames

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        if self.start is None:
            self.start = self.now = self._flushed = self._recycled = t
            if self._gap_from is not None:  # restored: from the saved state's time to now
                self._predict_gap(t - self._gap_from)
                self._gap_from = None
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
                    self._fade(obj, (t - t0) / parts)
                    d = obj.weigh(*lat_f) if lat_f is not None else 0.0
                    for h in refs:
                        self.hyps[h].logw += d
        for obj, refs in objs:
            if isinstance(obj, Hidden):
                continue
            self._sync(obj, refs, t, None if lat_f is None else
                       tuple(f ** (MAX_STEP * parts / (t - t0)) for f in lat_f))
        for si in sorted(self._ld_stats):
            if self._ld_stats[si].time >= self.m.ld_every:
                self._ld_weigh(si, self._ld_stats.pop(si))
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
            pts = obj.sigma(self.world)
            r = sum(self._gauss_rates(si, obj, pts=pts) for si in live)
            da = 0.0
            if obj.away is not None and lat_f is not None:
                share = dt / MAX_STEP  # lat_f holds for MAX_STEP; dt is at most that long
                da = obj.away.weigh(lat_f[0] ** share, lat_f[1] ** share)
            # E[exp(-rate(x) dt)] over each component, per level of detectability
            d = obj.reweigh(obj.kappa_weigh(self._expect(np.exp(-np.outer(kappa, r[STILL]) * dt)),
                                            float(self._expect(np.exp(-r[WALK] * dt)))), da)
            for h in refs:
                self.hyps[h].logw += d
        obj.t = t

    def _live(self, t: float) -> list:
        return [si for si, sid in enumerate(self.sensors)
                if sid in self.runtime and t - self.runtime[sid].last_frame <= IDLE
                and self.config.sensors[si].enabled and self.config.sensors[si].placed]

    def _recycle(self):
        """Known people who almost surely do not exist join the unknown ones (MODEL.md 5.5): otherwise
        every guest who ever came would be followed forever. Who left the house fades there like
        anywhere (_fade) and is given back only then: given back at once with all of r, they were
        unknown people out of the house who come back at the rate of coming home and are forgotten
        only after a day, while the existence of everybody else without support fades in minutes
        (7.10. 09:01: r 0.99 given back, the unknown ones out of the house 0.27 -> 1.25; at 09:48 a
        ghost in the hallway became a person, MODEL.md 10). The unknown ones are one
        Poisson process for all hypotheses, as in the PMBM (B_GarciaFernandez2018 eq. 7-10): what the
        hypotheses give back is added to it weighted by their probability (otherwise every hypothesis
        would carry its own copy of the density, a third of the computing time at a start with
        nothing known, MODEL.md 10)."""
        gone = [[u for u in hy.hidden if u.r < GIVE_UP] for hy in self.hyps]
        if not any(gone):
            return
        w = self.hyp_weights()
        parts = {}
        for wi, hy, g in zip(w, self.hyps, gone):
            parts.setdefault(id(hy.ppp), [0.0, hy.ppp, []])[0] += wi
            parts[id(hy.ppp)][2].append((wi, g))
        new = None
        for wp, ppp, given in parts.values():
            part = ppp.copy()
            part.scale(wp)
            for wi, g in given:
                for u in g:
                    part.add_scaled(u, wi)
            if new is None:
                new = part
            else:
                new.add_scaled(part, 1.0)
        for hy, g in zip(self.hyps, gone):
            hy.ppp = new
            for u in g:
                hy.hidden.remove(u)
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
                v = (1 - obj.a) * obj.walking() * float(self._expect(self._g(si, obj.sigma(self.world)[WALK]) > 0.1))
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
                d = obj.reweigh(obj.kappa_weigh(self._expect(1 / (1 + np.outer(kappa, r[STILL]) * ls)),
                                                float(self._expect(1 / (1 + r[WALK] * lw)))), da)
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
        """(2,) per component: E[sight * kernel] - the kernel exactly, the sight at the sigma points
        of the position weighted by it."""
        k, pts = c.near_sigma(z, self.m.find_radius, self.world)
        return k * self._expect(self._g(si, pts))

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
        to the people without one. The ghost map learns where ghosts begin, with P(ghost) as
        judged now, but without what the map itself said at the birth (MODEL.md 4.2)."""
        w = self.hyp_weights()
        p_ghost = float(sum(wi for wi, hy in zip(w, self.hyps) if hy.kind.get(seg) == "g"))
        info = self.segs.pop(seg)
        if 0 < p_ghost < 1:  # the map entered the odds once, as a factor at the birth: taken out
            p_ghost = 1 / (1 + math.exp(math.log1p(-p_ghost) - math.log(p_ghost) + info["map_odds"]))
        life = (info["lost"]["t"] if info["lost"] else info.get("gt", info["born"])) - info["born"]
        if self.learn_ghosts and p_ghost > 0:
            self.ghost_map.add_birth(self.sensors[si], info["z0"], p_ghost)
            self._ghost_total.pop(si, None)
        for listener in self.listeners:
            listener("track_end", (self.sensors[si], info["z0"], p_ghost, life, data_lost))
        self._live_by_sensor[si].remove(seg)
        ghost_end = self._ghost_end(info, t)
        cache = {}
        for hy in self.hyps:
            kind = hy.kind[seg]
            acc = hy.ghost.pop(seg, None)
            hy.phantom.pop(seg, None)
            if kind == "g":
                del hy.kind[seg]
                if not data_lost and ghost_end is not None:
                    before = _logsumexp(acc)
                    acc += ghost_end
                    hy.logw += _logsumexp(acc) - before
                continue
            self._release(hy, kind, seg, cache)
            del hy.kind[seg]

    def _ghost_end(self, info, t) -> np.ndarray | None:
        """Per kind of ghost, the log factor of its track ending now beyond what the person's
        alternative gets (MODEL.md 4.2). The track ends when the sensor gives up a target it has
        not found again (sensortracks.py); for a person that is "not found again for u s" (_held,
        S(u)). A ghost's source is either still there and not found again, S_d(u) S(u), or it
        died at some tau < u before being found again, int f_d(tau) S(tau) dtau (it can't be found
        when it is gone): together S(u) (S_d(u) + int f_d S / S(u)) - the S(u) of _held times this.
        Ended without being held first: the same for both, 0. (Until 0.10.0: log(1 - S_d(u)), as if
        the source had to die and also not be found: a ghost's track ending made it ~25 times less
        likely than a person, MODEL.md 10.)"""
        lost = info["lost"]
        types = self._ghost_types()
        if not self.m.ghost_end_exact or (lost is None and self.m.ghost_end_unheld):
            u = t - (lost["t"] if lost else info.get("gt", info["born"]))
            return np.array([math.log(max(-math.expm1(-max(u, FRAME) / life), 1e-300)) for _, life in types])
        if lost is None:
            return None
        u = t - lost["t"]
        if u <= 0:
            return None
        s_u = max(self._no_find(lost["kind"], u), 1e-300)
        tau = u * np.concatenate([[0.0], np.geomspace(1e-4, 1.0, 48)])  # S drops within 0.1-0.3 s
        s_tau = np.array([self._no_find(lost["kind"], x) for x in tau])
        out = []
        for _, life in types:
            f = np.exp(-tau / life) / life * s_tau
            died = float(((f[1:] + f[:-1]) * np.diff(tau)).sum() / 2)
            out.append(math.log(math.exp(-u / life) + died / s_u))
        return np.array(out)

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
                    return g.kappa_weigh(self._expect(np.outer(kappa, r[STILL]) * ls), float(self._expect(r[WALK] * lw)))
                return g.kappa_weigh(self._expect(np.expm1(np.outer(kappa, r[STILL]) * last)),
                                     float(self._expect(np.expm1(r[WALK] * last))))
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
            """seg starts / is found on this person: (new object, log factor). The rate of the start
            (or the chance of finding it there) where the person is given z: over the posterior,
            int p(x) f(x) N(z | x) dx = N(z) E[f(x) | z]."""
            if isinstance(obj, Gauss):  # somebody with tracks
                def make():
                    new = obj.copy()
                    if seg not in new.slots:
                        new.add_track(seg, var, m.const_share)
                    L = new.update(seg, z, m.white)
                    return new, L + new.reweigh(logf(new)) if L > -math.inf else L
                return derived(("gauss", id(obj)), make)
            if not tl.n:
                return None, -math.inf
            return derived(("tiles", id(obj)), lambda: Gauss.from_tiles(obj, tl, f_lat[0], f_lat[1], seg, z, var, m))

        children, cats = [], []  # cats: what each child says the track is (for listeners)

        def add(ch, cat):
            children.append(ch)
            cats.append(cat)

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
                    add(ch, "refind_ghost")
                else:
                    new, L = attach(hy.groups[cur])
                    if L > -math.inf:
                        ch = hy.child(hy.logw + pf + L)
                        ch.groups[cur] = new
                        add(ch, "owner")
                    ch = released(hy.child(hy.logw + pf + math.log(m.find_clutter / (2 * math.pi * m.find_radius ** 2))))
                    ch.kind[seg] = "g"
                    ch.ghost[seg] = _log(ghost_rates / ghost_rates.sum())
                    ch.phantom[seg] = source()
                    add(ch, "clutter")
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
                add(ch, "ghost")
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
                add(ch, "tracked")
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
                add(ch, "known")
            # somebody nobody knew of (the unknown ones stay as they are: a birth is one point of
            # the Poisson process, B_GarciaFernandez2018 eq. 18-24)
            new, L = attach(hy.ppp)
            if L > -math.inf:
                ch = released(hy.child(hy.logw + base + L))
                gid = next(self._gids)
                ch.groups[gid] = new
                ch.kind[seg] = gid
                add(ch, "new")
        if not children:
            return
        for listener in self.listeners:
            listener("branch", (self.sensors[si], seg, z, refind, [(c.logw, k) for c, k in zip(children, cats)]))
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
        self._version += 1  # the hypotheses change: cached per-hypothesis values are void
        self.hyps = self._merged(self.hyps)

    def _merged(self, hyps: list) -> list:
        """Hypotheses that now say the same about every live track are one: their mixture (MODEL.md
        5.6). People held by the same object in all of them stay as they are; the others are mixed
        per person (exact for one, the multi-Bernoulli approximation for more)."""
        by = {}
        for hy in hyps:
            by.setdefault(hy.key(), []).append(hy)
        if len(by) == len(hyps):
            return hyps
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
            # a hypothesis with fewer of them: the missing ones do not exist there (r = 0)
            size = max(len(r) for r in rests)
            if any(len(r) < size for r in rests):
                nobody = Hidden.nobody(self.tiles)
                rests = [r + [nobody] * (size - len(r)) for r in rests]
            rests = [rests[0]] + [self._pair(rests[0], r) for r in rests[1:]]
            mixed = [self._mix_hidden([(w, rest[j]) for w, rest in zip(wn, rests)]) for j in range(size)]
            new.hidden = common + [u for u in mixed if u.r > 0]
            new.ppp = self._mix_hidden([(w, h.ppp) for w, h in zip(wn, hs)])
            out.append(new)
        return out

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
        return angle, slant, np.where(ahead > 0, self._los(si, pos), 0.0)

    def _ld_frame(self, si, rt, t, ld):
        """The LD2410C's energies of this frame (MODEL.md 4.3), gathered until they are weighed
        (every ld_every s, with the next move of everybody). A frame stands for one frame (ld_frame).
        Without anything to report (no LD2450 target, both flags off) the firmware sends only a
        heartbeat every 5 s, and the first frame after such a silence is there because something
        rose: the silence stands for the energies of the frame before it, as the frames until the
        next heartbeat would have been (measured on thinned full-rate stretches: within 0-7 %; the
        flags are no thresholds on the energies, a censored "below the thresholds" was 5-65 % off).
        A gap longer than IDLE is lost data."""
        dt = t - rt.ld_t
        mg, sg = ld.get("move_gates"), ld.get("still_gates")
        if dt <= 0:
            return
        rt.ld_t = t
        prev, rt.ld_e = rt.ld_e, None
        if not mg or not sg or len(mg) != 9 or len(sg) != 9:
            return
        e = rt.ld_e = np.array(list(mg) + list(sg[2:]), dtype=float)
        st = self._ld_stats.get(si)
        if st is None:
            st = self._ld_stats[si] = ld2410.Stats()
        frame = self.m.ld_frame
        if dt > IDLE:
            st.add(e, frame)
        elif dt > 2 * frame and prev is not None:
            st.add(prev, dt - frame)
            st.add(e, frame)
        else:
            st.add(e, dt)  # the next frame (or one lost on the way)

    def _ld_tiles(self, si: int) -> tuple:
        """The tiles sensor si's LD2410C sees and what a person there puts into its 16 cells:
        (tile indices, standing (k, 16), walking (k, 16)), averaged over each tile."""
        key = ("ld2410", si)
        hit = self._gcache.get(key)
        if hit is None:
            tl = self.tiles
            if tl.n:
                angle, slant, sight = self._ld_geometry(si, tl.points)
                s_still, s_walk = ld2410.expected(self.m, angle, slant, sight)
                ss, sw = tl.average(s_still.T).T, tl.average(s_walk.T).T
                idx = np.nonzero(np.maximum(ss, sw).max(axis=1) > ld2410.FLOOR)[0]
                hit = (idx, ss[idx], sw[idx])
            else:
                hit = (np.zeros(0, dtype=int), np.zeros((0, ld2410.CELLS)), np.zeros((0, ld2410.CELLS)))
            self._gcache[key] = hit
        return hit

    def _ld_points(self, si: int, obj) -> tuple:
        """Where sensor si's LD2410C sees a person (or the unknown ones): (masses (k,), what each puts
        into its cells (k, 16)): the tiles, or a Gaussian's components at their means. The rest of
        the mass (out of its view, behind a door, out of the house) puts nothing in."""
        idx, ss, sw = self._ld_tiles(si)

        def tiles(h, share):
            if not len(idx):
                return np.zeros(0), np.zeros((0, ld2410.CELLS))
            return share * np.concatenate([h.walk[idx], h.still.sum(axis=(0, 1))[idx]]), np.concatenate([sw, ss])

        if isinstance(obj, Hidden):
            return tiles(obj, obj.r)
        w = obj.weights()
        angle, slant, sight = self._ld_geometry(si, obj.pos)
        s_still, s_walk = ld2410.expected(self.m, angle, slant, sight)
        # the standing component per level of its amplitude (4.3), then the walking one
        sh = self.shapes
        aw = sh.amp_w if obj.amw is None else obj.amw
        masses = [(1 - obj.a) * np.concatenate([w[STILL] * aw, [w[WALK]]])]
        S = [np.vstack([sh.amp[:, None] * s_still[STILL][None, :], s_walk[WALK][None, :]])]
        if obj.away is not None and obj.a > 0:
            m_, S_ = tiles(obj.away, obj.a)
            masses.append(m_)
            S.append(S_)
        return np.concatenate(masses), np.concatenate(S)

    @staticmethod
    def _ld_ratio(obj, pts, mu0, st, lik) -> tuple:
        """A person (or the unknown ones) on top of mean energies mu0 (16,): the log likelihood
        ratio per point (k,) and the log of its normalizer - the factor of the whole person, or for
        the unknown ones log((1 + int lambda L) / (1 + Lambda)): at most one of them in view (the
        Poisson process truncated after one; afterwards moment-matched again)."""
        m, S = pts
        lr = np.minimum(st.log_ratio(mu0[None, :] + S, mu0, lik), ld2410.LOG_CAP)
        top = max(float(lr.max()), 0.0)
        inside = float(m @ np.exp(lr - top))
        if isinstance(obj, Undetected):
            return lr, top + math.log(math.exp(-top) + inside) - math.log1p(float(m.sum()))
        return lr, top + math.log(max(1.0 - float(m.sum()), 0.0) * math.exp(-top) + inside)

    def _ld_weigh(self, si: int, st: "ld2410.Stats"):
        """The LD2410C's energies since they were last weighed (MODEL.md 4.3): mean = background +
        what the people put in. Not a product over the people: per hypothesis the people one after the
        other, each given those before at what they put in afterwards (the ones with tracks first,
        the unknown ones last; exact for people at known places). Each person is then weighed given
        all the others, mixed over the hypotheses holding them (the marginals of JIPDA; what the
        others explain says nothing about this person, 0.6.13)."""
        m = self.m
        sid = self.sensors[si]
        lik = ld2410.Likelihood(m)
        b = self.ld_background.b(sid)
        # the still energies lag (the firmware smooths them; time constant ld_memory): over these
        # frames they are what the people put in before (the memory M) and only by 1 - wm what they
        # put in now. The moving ones follow at once.
        M = self._ld_memory.get(si)
        a = math.exp(-st.time / m.ld_memory)
        wm = m.ld_memory / st.time * (1 - a) if M is not None else 0.0
        now = ld2410.cell_values(m, 1.0, 1.0 - wm)
        b0 = b + wm * M * (now < 1) if M is not None else b
        objs = self._objects()
        pts = {}
        for o, _ in objs:
            mm, S = self._ld_points(si, o)
            pts[id(o)] = (mm, S * now)
        seen = {i for i, (mm, _) in pts.items() if len(mm) and mm.sum() > 1e-12}
        prior_w = self.hyp_weights()
        echo = self._ld_echo.get(si)
        if echo is None:
            echo = self._ld_echo[si] = ld2410.Echoes(m)
        echo.predict(st.time, m, self.ld_background.rate(sid))
        e_pts = echo.points(now)
        # the background learns from the people (and echoes) as the filter saw them before these frames
        share = np.zeros(ld2410.CELLS)
        prior_people = []
        for h, hy in enumerate(self.hyps):
            P = sum((pts[id(o)][0] @ pts[id(o)][1] for o in hy.objects() if id(o) in seen), np.zeros(ld2410.CELLS))
            prior_people.append(P)
            share += prior_w[h] * b / (b0 + P + e_pts[0] @ e_pts[1])
        if self.learn_ghosts:
            self.ld_background.learn(sid, share, st)
        # per hypothesis the people one after the other, the echo sources last
        memo = {}
        cache = {}  # (object, what the others put in) -> its ratio and normalizer
        logf = np.zeros(len(self.hyps))
        totals, owns, tracked = [], [], []
        for h, hy in enumerate(self.hyps):
            R = np.zeros(ld2410.CELLS)
            prefix = ()
            own = []
            for o in list(hy.groups.values()) + hy.hidden + [hy.ppp]:
                if id(o) not in seen:
                    continue
                key = (id(o), prefix)
                hit = memo.get(key)
                if hit is None:
                    mm, S = pts[id(o)]
                    lr, norm = cache[(id(o), R.tobytes())] = self._ld_ratio(o, pts[id(o)], b0 + R, st, lik)
                    # what they put in afterwards (for the unknown ones: the moment-matched intensity)
                    post = (mm * np.exp(lr - norm)) @ S
                    hit = memo[key] = (norm, post)
                logf[h] += hit[0]
                own.append((o, hit[1], R))
                R = R + hit[1]
                prefix += (id(o),)
            key = ("echo", R.tobytes())
            hit = memo.get(key)
            if hit is None:
                hit = memo[key] = self._ld_ratio(echo, e_pts, b0 + R, st, lik)[1]
            logf[h] += hit
            totals.append(R)
            owns.append(own)
            # what the people with tracks put in, and the others as they were before these frames
            # (the echo sources compete with the unseen people, 4.3)
            tracked.append(sum((p for o, p, _ in own if isinstance(o, Gauss)), np.zeros(ld2410.CELLS))
                           + sum((pts[id(o)][0] @ pts[id(o)][1] for o in hy.hidden + [hy.ppp] if id(o) in seen),
                                 np.zeros(ld2410.CELLS)))
        base = st.loglik(b0, lik)
        for h, hy in enumerate(self.hyps):
            hy.logw += base + logf[h]
        lw = np.log(np.maximum(prior_w, 1e-300)) + logf
        post_w = np.exp(lw - lw.max())
        current = (post_w / post_w.sum()) @ np.array(totals) / np.maximum(now, 1e-9)
        self._ld_memory[si] = current if M is None else a * M + (1 - a) * current
        log_off = math.log(max(float(echo.p[0]), 1e-300))
        # each person given the others, mixed over the hypotheses holding them (the last one in a
        # hypothesis: the same as above)
        for obj, refs in objs:
            if id(obj) not in seen:
                continue
            hs = sorted(set(refs))
            q = post_w[hs]
            if q.sum() <= 0:
                continue
            q = q / q.sum()
            parts, outs = [], []
            for qh, h in zip(q, hs):
                k = next(i for i, (o, _, _) in enumerate(owns[h]) if o is obj)
                rest = owns[h][k][2] if k == len(owns[h]) - 1 else totals[h] - owns[h][k][1]
                key = (id(obj), rest.tobytes())
                hit = cache.get(key)
                if hit is None:
                    hit = cache[key] = self._ld_ratio(obj, pts[id(obj)], b0 + rest, st, lik)
                lr, norm = hit
                # an unseen person and an echo source are alternatives (both rare): where the person
                # is not, an echo may explain the energies - the person's view weighed against that
                key = ("echo", rest.tobytes())
                ze = cache.get(key)
                if ze is None:
                    ze = cache[key] = self._ld_ratio(echo, e_pts, b0 + rest, st, lik)[1]
                lq = math.log(max(qh, 1e-300)) - norm
                # in view (no echo source) against out of view (an echo source or none)
                if isinstance(obj, Undetected):
                    parts.append(lq + lr + log_off - ze)  # an intensity: outside stays as it is
                else:
                    parts.append(lq + lr + log_off)
                    outs.append(lq + ze)
            parts = np.array(parts)
            top = parts.max(axis=0)
            logf_pts = top + np.log(np.exp(parts - top).sum(axis=0))
            if not isinstance(obj, Undetected):
                logf_pts -= _logsumexp(outs)  # a person: only the shape changes (outside: 1)
            self._ld_apply(si, obj, np.clip(logf_pts, -700.0, ld2410.LOG_CAP))
        # the echo sources given the people with tracks and the unseen ones as they were, mixed over
        # the hypotheses
        q = post_w / post_w.sum()
        parts, outs = [], []
        for qh, h in zip(q, range(len(self.hyps))):
            if qh < 1e-12:
                continue
            lr, norm = self._ld_ratio(echo, e_pts, b0 + tracked[h], st, lik)
            lq = math.log(qh) - norm
            parts.append(lq + lr)
            outs.append(lq)
        parts = np.array(parts)
        top = parts.max(axis=0)
        echo.weigh(np.clip(top + np.log(np.exp(parts - top).sum(axis=0)) - _logsumexp(outs), -700.0, ld2410.LOG_CAP))
        if self.learn_ghosts:
            self.ld_background.learn_echoes(sid, echo.began())
        self._normalize()

    def _ld_apply(self, si: int, obj, logf: np.ndarray):
        """Multiply a person (or the unknown ones) by exp(logf) at the points of _ld_points
        (elsewhere by 1); logf is capped (ld2410.LOG_CAP), so the factors stay finite."""
        idx, _, _ = self._ld_tiles(si)
        n = len(idx)

        def tiles(h, g):
            fw, fs = np.ones(self.tiles.n), np.ones(self.tiles.n)
            fw[idx], fs[idx] = np.exp(g[:n]), np.exp(g[n:])
            return h.weigh(fw, fs)

        if isinstance(obj, Hidden):
            tiles(obj, logf)
            return
        A = len(self.shapes.amp)
        d_away = tiles(obj.away, logf[A + 1:]) if obj.away is not None and obj.a > 0 and n else 0.0
        # the amplitude's levels: their probabilities, and the factor of the standing component
        aw = self.shapes.amp_w if obj.amw is None else obj.amw
        top = float(logf[:A].max())
        f = np.exp(logf[:A] - top)
        s = float(aw @ f)
        obj.amw = aw * f / s if s > 0 else aw.copy()
        obj.reweigh(np.array([top + math.log(s) if s > 0 else -math.inf, logf[A]]), d_away)

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
        means = np.ascontiguousarray(means, dtype=float)
        top = float(means.max()) if len(means) else 0.0
        return kernels.poisson_binomial(np.ascontiguousarray(np.reshape(ps, (-1, len(means))), dtype=float), means,
                                        _lgamma_table(int(top + 10 * math.sqrt(max(top, 0.0)) + 6)))

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
        """The known people of the most probable hypothesis that more probably exist than not
        (Garcia-Fernandez et al. 2018, sec. VI), for the display: those with a track first."""
        hy = self.hyps[int(np.argmax([h.logw for h in self.hyps]))]
        return [self._display(k + 1, obj) for k, obj in enumerate(hy.people()) if getattr(obj, "r", 1.0) >= 0.5]

    def _display(self, pid, obj) -> dict:
        """Where to draw a person: with a measuring track the mean of the mixture (it moves as
        smoothly as the estimate does); without, the densest tile of the most probable place."""
        pr = self._place_probs(obj)
        best = int(np.argmax(pr))
        out = {"id": pid, "places": {self.world.places[i]: round(float(v), 3) for i, v in enumerate(pr) if v >= 0.005},
               "lost": isinstance(obj, Hidden), "r": round(float(getattr(obj, "r", 1.0)), 3), "obj": obj}
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
        for z in self.config.home_zones("room"):  # the stairwell is outside: no state (MODEL.md 6)
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
        zones = self.config.home_zones("room", "area")
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
        return {"ghost_map": self.ghost_map.to_dict(), "ld_background": self.ld_background.to_dict()}

    def use_ld_background(self, bg: "ld2410.Background"):
        """Use a learned LD2410C background (MODEL.md 4.3), per sensor only where it was learned
        with the sensor where it is now."""
        self.ld_background = bg
        bg.use(self.config)

    def load_learned(self, data: dict):
        if data.get("ghost_map"):  # learned offline (tools/ghostmap.py), used if the sensors are where they were
            from .ghostmap import GhostMap
            self.use_ghost_map(GhostMap.from_dict(data["ghost_map"]))
        if data.get("ld_background"):
            self.ld_background.load_dict(data["ld_background"])
            self.ld_background.use(self.config)
