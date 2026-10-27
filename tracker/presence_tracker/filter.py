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
import json
import math
import zlib

import numpy as np

from .filtermodel import FRAME, IDLE, STILL, WALK, Model, Shapes
from .frames import SensorRuntime, detections as frame_detections
from . import kernels, ld2410
from .gauss import Gauss
from .hidden import Hidden, Undetected
from . import destination
from .pause import Pauses
from .roomseen import RoomSeen
from .sensormodel import PD_MAX, SensorModel
from .sensortracks import LOST, SensorTracks
from .tiling import Tiling
from .unobserved import Dwell
from .world import CELL, OBSERVED, World

import presence_core  # the Rust core (docs/rust-core.md): since phase 3 the hypotheses and their objects


class CoreError(RuntimeError):
    """The Rust core panicked: a bug in the core, not in the data. PyO3 raises a panic as a
    BaseException, which the app's handler (app._model_failed: log, start the model over, as after a
    FloatingPointError) would let through and the app would stop; as this it is handled the same."""


def _core(call, *args):
    """Call into the Rust core, a panic as CoreError."""
    try:
        return call(*args)
    except presence_core.PanicException as e:
        raise CoreError(f"the Rust core failed: {e}") from e

__all__ = ["Model", "Tracker"]

MAX_STEP = 0.2  # s: motion is cut into parts no longer than this
MOUNT_RADIUS = 0.3  # m: targets this close to a sensor come from its mount
GIVE_UP = 0.01  # a known person who exists with less probability (wherever they are: refuted, or where
                # nothing could test them, Tracker._expire) joins the unknown ones with r x their density:
                # only P(several of them come back) changes, by <= GIVE_UP^2 / 2 (0.05: 14 % less
                # computing time, light wrongly off 1.67 instead of 1.17)
RECYCLE_EVERY = 1.0  # s
LD_TILE_MASS = 1e-4  # persons: a tile holding less of a person (or of the unknown ones) standing is weighed
                     # by the LD2410C at its mean amplitude, not level by level (_ld_split; MODEL.md 4.3)
ENTER_MIN_WALK = 0.02  # "wird betreten" (MODEL.md 6): a person walking with less probability can raise no
                       # zone's p_enter by more than this; not moved on
# what only the outputs (MODEL.md 6) or the display read: a change restarts nothing
OUTPUT_PARAMS = ("target_threshold", "target_thresholds", "light_cost", "seen_hold", "approach_cost", "lead_time",
                 "ld2410_hold")
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


class _Mirror:
    """The core's hypotheses as Python objects (Hyp, Gauss, Hidden) for what still reads them in
    Python - the LD2410C, the outputs, the destination map, tests and tools (docs/rust-core.md 7,
    phase 3; thrown away when they are in Rust). serial: the core's state it shows; ids: per object
    (Python id) its index in the core; objs keeps those objects alive."""

    __slots__ = ("serial", "hyps", "segs", "live", "ids", "objs")


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
        self._c = None  # the Rust core (presence_core.Core): the hypotheses and their objects
        self._ids = itertools.count(1)
        self.ghost_map = None  # where each sensor starts ghost tracks (ghostmap.py)
        # what each LD2410C sees without anybody (ld2410.py, learned online, MODEL.md 4.3)
        self.ld_background = ld2410.Background(ld2410.prior(self.m), self.m.ld_prior_time, self.m.ld_forget,
                                               self.m.ld_echo_rate, self.m.ld_echo_prior_time, self.m.ld_anchor_forget,
                                               self.m.ld_anchor_quiet)
        self.ld_background.use(config)
        self.learn_ghosts = True  # learn the map online (MODEL.md 4.2); off for the offline EM tool
        self.dest_map = None  # where walks go (destination.py, learned online, MODEL.md 6 "Ziel")
        self.learn_dest = True
        # Lernen pausieren (pause.py): in these intervals nothing of the above learns; the tracking goes on
        self.pauses = Pauses()
        self.seen = RoomSeen(config)  # the rooms' own LD2450 (output only, MODEL.md 6 "Belegt")
        self._build()
        self.use_ghost_map(None)
        self.use_dest_map(None)
        self.reset_people(people)

    # --------------------------------------------------------------- setup

    def _build(self):
        self.world = World(self.config)
        self.sensors = [s.id for s in self.config.sensors]
        self.sidx = {sid: k for k, sid in enumerate(self.sensors)}
        self.tracks = {sid: SensorTracks(sid, self._ids) for sid in self.sensors}
        self._gcache = {}
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
        self.dest_geo = destination.Geometry(self)
        loglik = self._c.loglik if self._c is not None else (0.0, 0.0)  # the evidence goes on over a rebuild
        self._c = _core(presence_core.Core, self._export())
        self._c.loglik = loglik
        self._sent = (None, set())  # the sensors' state and the sets of live sensors the core has
        self._mirror = None
        self._dirty = False  # the mirror was handed out and may have been changed: loaded before the next call

    def _export(self) -> dict:
        """The world as this tracker built it, for the core (docs/rust-core.md 6: the WorldExport,
        until the build is in Rust): the model, the shapes, the raster and walls, the tiles, per
        sensor where it is and its sight."""
        tl, w, m, sh = self.tiles, self.world, self.m, self.shapes
        names = ("still_noise", "kappa_switch", "speed", "speed_spread", "turn_rate", "walk_length", "arrive_rate",
                 "guest_rate", "forget_rate", "find_radius", "res_range", "res_cross", "const_share", "tau",
                 "unseen_life", "white", "pos_every", "find_clutter", "ghost_echo", "hyp_mass")
        # JSON has no infinity: such numbers go as "inf", "-inf", "nan" (params.rs)
        params = {k: (v if math.isfinite(v) else str(v)) for k in names for v in [float(getattr(m, k))]}
        params.update(acquire=[[float(x) for x in a] for a in m.acquire], track_life=[float(x) for x in m.track_life],
                      refind={k: [float(x) for x in v] for k, v in m.refind.items()},
                      ghost_types=[[float(r), float(life)] for r, life in self._ghost_types()], max_hyps=int(m.max_hyps),
                      give_up=GIVE_UP, max_step=MAX_STEP, gap_exact=GAP_EXACT, leap=LEAP, max_leaps=MAX_LEAPS)

        def f(a):
            return np.ascontiguousarray(a, dtype=float)

        def i(a):
            return np.ascontiguousarray(a, dtype=np.int64)
        d = tl.door_tiles
        return dict(
            params=json.dumps(params), go=f(sh.go), go_w=f(sh.go_w), go_w_ongoing=f(sh.go_w_ongoing), kappa=f(sh.kappa),
            kappa_w=f(sh.kappa_w), amp=f(sh.amp), ka_w=f(sh.ka_w), amp_given_kappa=f(sh.amp_given_kappa), x0=float(w.x0),
            y0=float(w.y0), nx=int(w.nx), ny=int(w.ny), walls=f(w.walls), wall_dist=f(w.wall_grid), n=int(tl.n), R=int(tl.R),
            A=int(tl.A), tick=float(tl.tick), walk_t=f(tl.T_walk_t), into_t=f(tl.T_in_t), centers=f(tl.centers),
            tile_var=f(tl.var), area=f(tl.area), fine=i(tl.fine), fine_xy=f(tl.fine_xy), door_center=f(tl.centers[d]),
            door_var=f(tl.var[d]), door_area=f(tl.area[d]), door_rates=f(tl.door_rate[d]), door_ptr=i(tl.door_ptr),
            door_list=i(tl.door_list), exits=i(tl.exits), entries=i(tl.entry_idx), region_rates=f(tl.region_rates()),
            widths=f(tl.widths), points=f(tl.points), point_tile=i(tl.point_tile), point_count=f(tl.point_count),
            sensors=[(float(s.x), float(s.y), *map(float, s.sight_origin()), f(self._grid(si)))
                     for si, s in enumerate(self.config.sensors)])

    # the core's state, as the Python filter named it

    @property
    def _version(self) -> int:
        return self._c.version

    @_version.setter
    def _version(self, v: int):
        self._c.version = v

    @property
    def _flushed(self) -> float:
        return self._c.flushed

    @_flushed.setter
    def _flushed(self, t: float):
        self._c.flushed = t

    @property
    def loglik(self) -> float:
        """log evidence of everything seen so far"""
        return self._c.loglik[0]

    @property
    def loglik_cut(self) -> float:
        """... the part of it that is mass of hypotheses dropped (_cut)"""
        return self._c.loglik[1]

    # the mirror of the hypotheses (phase 3)

    def _call(self, name, *args):
        """A call into the core (through _core: a panic is a CoreError); a mirror that may have been
        changed is loaded first."""
        if self._dirty:
            self._push()
        return _core(getattr(self._c, name), *args)

    def _view(self) -> _Mirror:
        """The mirror of the core's state, made anew when the core changed."""
        m = self._mirror
        if m is None or (not self._dirty and m.serial != self._c.serial):
            m = self._mirror = self._dump()
        return m

    @property
    def _h(self) -> list:
        """The hypotheses for reading (the mirror's)."""
        return self._view().hyps

    @property
    def hyps(self) -> list:
        """The hypotheses as Python objects (a mirror of the core's): what is changed in them reaches
        the core before its next call."""
        m = self._view()
        self._dirty = True
        return m.hyps

    @hyps.setter
    def hyps(self, value):
        m = self._view()
        m.hyps = list(value)
        self._dirty = True

    @property
    def segs(self) -> dict:
        """The live tracks (seg id -> si, t, zt, gt, var, z, z0, born, lost, map_odds), for reading."""
        return self._view().segs

    @property
    def _live_by_sensor(self) -> dict:
        return self._view().live

    def _dump(self) -> _Mirror:
        dens, mixs, hyps, tracks = _core(self._c.dump)
        m = _Mirror()
        m.serial = self._c.serial
        m.ids, m.objs = {}, []
        tl = self.tiles
        ds, ms = {}, {}
        for k, (walk, still, region, out, clock, r, amp, keep), intensity in dens:
            h = (Undetected if intensity else Hidden).__new__(Undetected if intensity else Hidden)
            h.tiles = tl
            h.walk, h.still, h.region, h.out, h.clock, h.r, h._amp, h._keep = walk, still, region, out, clock, r, amp, keep
            ds[k] = h
            m.ids[id(h)] = k
            m.objs.append(h)
        for k, logw, mean, cov, gow, ka, slots, phantom, a, away, t in mixs:
            g = Gauss(logw, mean, cov, gow, ka, {s: j for s, j, _ in slots}, {s: v for s, _, v in slots}, phantom, a,
                      None if away is None else ds[away], t)
            ms[k] = g
            m.ids[id(g)] = k
            m.objs.append(g)
        m.hyps = [Hyp(logw, {s: "g" if o is None else o for s, o in tr}, {s: life for s, life, _ in gh},
                      {g: ms[j] for g, j in people}, [ds[j] for j in hidden], ds[unknown],
                      {s: ms[src] for s, _, src in gh})
                  for logw, tr, gh, people, hidden, unknown in hyps]
        m.segs, m.live = {}, {si: [] for si in range(len(self.sensors))}
        for seg, si, (t, zt, gt, var), z, z0, born, lost, odds in tracks:
            info = {"si": si, "t": t, "zt": zt, "var": var, "z": z, "z0": z0, "born": born,
                    "lost": None if lost is None else dict(zip(("kind", "t", "z", "u"), lost)), "map_odds": odds}
            if gt is not None:
                info["gt"] = gt
            m.segs[seg] = info
            m.live[si].append(seg)
        return m

    def _push(self):
        """Load the mirror's hypotheses into the core (after the LD2410C weighed them, or a test
        changed them)."""
        m = self._mirror
        self._dirty = False
        dens, dpos, mixs, mpos = [], {}, [], {}

        def density(h) -> int:
            if id(h) not in dpos:
                dpos[id(h)] = len(dens)
                dens.append(h)
            return dpos[id(h)]

        def mixture(g) -> int:
            if id(g) not in mpos:
                if g.away is not None:
                    density(g.away)
                mpos[id(g)] = len(mixs)
                mixs.append(g)
            return mpos[id(g)]

        def f(a):
            return None if a is None else np.ascontiguousarray(a, dtype=float)
        hyps = [(float(hy.logw), [(s, None if k == "g" else k) for s, k in hy.kind.items()],
                 [(s, f(hy.ghost[s]), mixture(hy.phantom[s])) for s in hy.ghost if s in hy.phantom],
                 [(gid, mixture(g)) for gid, g in hy.groups.items()], [density(u) for u in hy.hidden], density(hy.ppp))
                for hy in m.hyps]
        dargs = [(m.ids.get(id(h)), f(h.walk), f(h.still), f(h.region), float(h.out), float(h.clock), float(h.r),
                  type(h) is Undetected, f(h._amp), f(h._keep)) for h in dens]
        margs = [(m.ids.get(id(g)), f(g.logw), f(g.mean), f(g.cov), f(g.gow), f(g.ka),
                  [(s, int(j), float(g.var[s])) for s, j in g.slots.items()], bool(g.phantom), float(g.a),
                  None if g.away is None else dpos[id(g.away)], g.t) for g in mixs]
        _core(self._c.load, dargs, margs, hyps)
        self._mirror = None  # the core gave the new objects their ids: read anew

    def _send_sensors(self):
        """The sensors' state as the core needs it for which of them watch (_live)."""
        state = [(bool(s.enabled and s.placed and s.id in self.runtime),
                  self.runtime[s.id].last_frame if s.id in self.runtime else -math.inf) for s in self.config.sensors]
        if state != self._sent[0]:
            self._sent = (state, self._sent[1])
            _core(self._c.set_sensors, state)

    def _send_observed(self, sensors):
        """How well these sensors see each tile (Tiling.observed), once per set."""
        key = tuple(sensors)
        if key not in self._sent[1]:
            self._sent[1].add(key)
            _core(self._c.set_observed, list(key), np.ascontiguousarray(self.tiles.observed(key), dtype=float))

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
        self._call("clear", self.now)  # no tracks, moved to now, version 0
        self._gap_from = None  # time of a restored state: the gap to the first frame is still to predict
        self._out_cache = {}
        self._ld_stats = {}  # sensor index -> the LD2410C's frames since they were last weighed (ld2410.Stats)
        self._ld_memory = {}  # sensor index -> what the people put into its cells lately (the still ones lag)
        self._ld_echo = {}  # sensor index -> its echo sources (ld2410.Echoes)
        self._dest_learner = destination.Learner()  # the walks under way (they follow the live tracks)
        self._dest_t = -math.inf

    def people_state(self) -> dict:
        """What is known about the people, to start from after a restart (MODEL.md 5.3): per
        hypothesis its weight, its known people and the unknown ones, as densities over the tiles.
        Live tracks do not survive a restart (the sensors' next frames are a start, 4.1): the
        people with tracks go to the tiles as when their last track ends (5.4), ghost tracks end,
        and the hypotheses that then say the same are merged (5.6) - one per number of known
        people (in the core, on copies). The time is that of the last move of everybody."""
        hyps, dens = self._call("people_state")
        objs = []
        for walk, still, region, out, clock, r, amp, keep in dens:
            h = Hidden(self.tiles)
            h.walk, h.still, h.region, h.out, h.clock, h.r, h._amp, h._keep = walk, still, region, out, clock, r, amp, keep
            objs.append(h)
        return {"t": self._flushed, "tiles": self.tiles.fingerprint(), "densities": [o.to_dict() for o in objs],
                "hyps": [{"logw": lw, "people": people, "unknown": u} for lw, people, u in hyps]}

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
        always (Hidden.move), what lies before in steps of LEAP s (Hidden.leap), at most MAX_LEAPS;
        what the sensors placed could have tested lasts (in the core)."""
        if gap <= 0:
            return
        if gap > GAP_EXACT and not self._c.has_walk_end():
            _core(self._c.set_walk_end, np.ascontiguousarray(self.tiles.walk_end(), dtype=float))
        # the sensors were there, only the app was not watching: what they could have tested lasts
        placed = [si for si, s in enumerate(self.config.sensors) if s.enabled and s.placed]
        self._send_observed(placed)
        self._call("predict_gap", gap, placed)

    def reconfigure(self, config):
        """A new configuration. If only sensors changed (turned, moved, recalibrated, switched on
        or off) and the world the people live in stays the same, what is known about the people
        stays: a recalibration tells something about a sensor, nothing about them. The changed
        sensors start over as after lost data (MODEL.md 4.1, 5.3): their tracks end without
        information, their next frame is a start. Otherwise (walls, rooms, doors, parameters, the
        set of sensors, the observed area) the model starts over as after a restart: from what it
        knew (people_state) if the tiles stay the same, else from nothing known. Returns whether it
        started over."""
        self.reconfigured_from = None
        changed = self._changed_sensors(self.config, config)
        state = self.people_state() if changed is None else None
        # if what follows fails half-way (new tiles, old people), the app saves this (app.h_put_config)
        self.reconfigured_from = state
        self.config = config
        self.p = config.params
        self.seen.use(config)
        self.sensor_model.config = config
        self.sensor_model.rebuild()
        if changed is not None:
            self._recalibrated(changed)
        else:
            self.dwell = Dwell(self.p)
            self._build()
        self.use_ghost_map(self.ghost_map)  # it follows recalibrated sensors, a remounted one starts over
        self.use_dest_map(self.dest_map)  # it holds while the rooms and doors are the same
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
        for k in OUTPUT_PARAMS:  # how the outputs decide changes nothing of the model
            a["params"].pop(k, None)
            b["params"].pop(k, None)
        for z in a["zones"] + b["zones"]:  # nor what the zones are called
            z.pop("name", None)

        def regions(c):
            return {rid: {k: v for k, v in r.items() if k != "name"} for rid, r in c.regions.items()}
        if a != b or [s["id"] for s in sa] != [s["id"] for s in sb] or regions(old) != regions(new):
            return None
        if not np.array_equal(World(new).labels, self.world.labels):
            return None
        return [x["id"] for x, y in zip(sa, sb) if dict(x, name=None) != dict(y, name=None)]

    def _recalibrated(self, changed):
        """Sensors changed, the people's world not (reconfigure): the people stay as they are; the
        changed sensors' tracks end as at lost data, their next frame counts as a start."""
        if any(self._c.live_tracks(self.sidx[sid]) for sid in changed):
            self._flush(self.now)  # the ends weigh and release everybody: all at now
        for sid in changed:
            si = self.sidx[sid]
            for seg in self._c.live_tracks(si):
                self._end(si, seg, self.now, True)
            self.tracks[sid] = SensorTracks(sid, self._ids)
            if sid in self.runtime:
                self.runtime[sid].last_frame = -math.inf  # the next frame is a start (_evidence)
            for d in (self._ld_stats, self._ld_memory, self._ld_echo):
                d.pop(si, None)
        if changed and self._h:
            self._merge()
            self._prune()
            self._normalize()
        self.world.config = self.config
        self._gcache, self._ghost_total, self._out_cache = {}, {}, {}
        self.tiles.g, self.tiles.dist, self.tiles._observed = {}, {}, {}
        for si in range(len(self.sensors)):
            self._g(si, np.zeros((1, 2)))
        self._version += 1
        _core(self._c.set_world, self._export())  # the sensors' sight anew, what is known stays
        self._sent = (None, set())

    def use_ghost_map(self, gm) -> bool:
        """Use a learned ghost map (MODEL.md 4.2) if it was learned with the prior the model has now;
        else start a new one from the prior. Each sensor's part is in its own frame: it follows a
        recalibrated sensor, and only a sensor mounted elsewhere starts its part over (GhostMap.use).
        Returns whether the learned map is used."""
        from .ghostmap import GhostMap
        new = GhostMap.from_prior(sum(rate for rate, _ in self.m.ghost_types), self.m.ghost_prior_ghosts)
        ok = (gm is not None and math.isclose(gm.prior_rate, new.prior_rate)
              and math.isclose(gm.prior_time, new.prior_time))
        self.ghost_map = gm if ok else new
        self.ghost_map.use(self.config)
        self._ghost_total = {}
        return ok

    def use_dest_map(self, dm) -> bool:
        """Use a learned destination map (MODEL.md 6 "Ziel") if it was learned with these rooms, doors
        and regions; else start an empty one."""
        ok = dm is not None and dm.fingerprint == self.dest_geo.fingerprint() and dm.count.shape[0] == self.dest_geo.n
        self.dest_map = dm if ok else destination.DestinationMap.for_geometry(self.dest_geo)
        if hasattr(self, "_out_cache"):
            self._out_cache.pop("targets", None)
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
            grid, los = self._spread_sight(si, grid), self._spread_sight(si, los)
            self._gcache[si] = grid
            self._gcache[("los", si)] = los
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
        w = self.world
        return kernels.grid_at(self._grid(si), np.ascontiguousarray(pos, dtype=float).reshape(-1, 2), float(w.x0), float(w.y0),
                               CELL).reshape(pos.shape[:-1])

    def _polar(self, si: int, pos: np.ndarray) -> tuple:
        s = self.config.sensors[si]
        dx, dy = pos[..., 0] - s.x, pos[..., 1] - s.y
        return np.hypot(dx, dy), np.arctan2(dy, dx)

    def _los(self, si: int, pos: np.ndarray) -> np.ndarray:
        """How much of a person at the points (n, 2) is in the line of sight of sensor si (0..1,
        the LD2410C's sight; _grid)."""
        self._grid(si)
        w = self.world
        return kernels.grid_at(self._gcache[("los", si)], np.ascontiguousarray(pos, dtype=float).reshape(-1, 2), float(w.x0),
                               float(w.y0), CELL)

    # ------------------------------------------------------------- frames

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        if self.start is None:
            self.start = self.now = self._flushed = self._recycled = t
            if self._gap_from is not None:  # restored: from the saved state's time to now
                self._predict_gap(t - self._gap_from)
                self._gap_from = None
        self.seen.heard_from(sensor_id, t)  # its room is not silent (output only, roomseen.py)
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
        self._end_silent(t)  # this sensor's own tracks too, if its last frame is too long ago
        ev = self.tracks[sensor_id].update(t, frame, dets) if sensor_id in self.tracks else None
        measured = {id(d) for _, d in ev.born + ev.measured} if ev else set()
        for d in dets:
            d.stale = not d.hidden and id(d) not in measured
        self.seen.frame(sensor, t, dets)
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
        self._end_silent(t)
        self.now = t
        if t - self._flushed >= MAX_STEP - 1e-9:
            self._flush(t)
        if t - self._recycled >= RECYCLE_EVERY:
            self._recycled = t
            self._recycle()
        if self.learn_dest and t - self._dest_t >= MAX_STEP - 1e-9:
            # the walks counted into the destination map (MODEL.md 6 "Ziel"): reads the hypotheses only
            self._dest_t = t
            if self.pauses.paused(t):  # learning paused: the walks under way are dropped, none counted
                self._dest_learner = destination.Learner()
            else:
                self._dest_learner.observe(self, self.dest_geo, self.dest_map)

    def _end_silent(self, t: float):
        """A sensor without a frame for more than LOST s has lost its data: its tracks are over
        (MODEL.md 4.1, 4.4), at its last frame + LOST, as its next frame would end them - without
        waiting for that frame. Until 0.18 they lived on while it was silent: a board that died while
        tracking somebody held them as a person with a track (existence 1, not fading) for hours, the
        room occupied after they had left."""
        for si, sid in enumerate(self.sensors):
            tracks = self.tracks.get(sid)
            if tracks is None or t - tracks.last <= LOST:
                continue
            live = self._c.live_tracks(si)
            if not live:
                continue
            t_end = max(tracks.last + LOST, self.now)
            self._flush(t_end)  # the end weighs and releases everybody: all at its time
            self._version += 1
            for seg in live:
                self._end(si, seg, t_end, True)
            tracks.raw = []  # its next frame is a start (lost data, _evidence)
            self._merge()
            self._normalize()

    def _flush(self, t: float):
        """Move everybody to time t (in the core: the people without a track all from the last
        flush, each Gaussian from its own time; in parts no longer than MAX_STEP), then the
        LD2410C's energies gathered long enough are weighed."""
        if t <= self._flushed:
            return
        t0 = self._flushed
        self._send_sensors()
        parts = int(math.ceil((t - t0) / MAX_STEP - 1e-9))
        for k in range(parts):
            self._send_observed(self._live(t0 + (t - t0) * (k + 1) / parts))
        self._call("flush", t)
        for si in sorted(self._ld_stats):
            if self._ld_stats[si].time >= self.m.ld_every:
                self._ld_weigh(si, self._ld_stats.pop(si))

    def _live(self, t: float) -> list:
        return [si for si, sid in enumerate(self.sensors)
                if sid in self.runtime and t - self.runtime[sid].last_frame <= IDLE
                and self.config.sensors[si].enabled and self.config.sensors[si].placed]

    def _recycle(self):
        """Known people who almost surely do not exist join the unknown ones (MODEL.md 5.5; in the
        core): those the measurements refuted, and those whose record expired where nothing could
        test it, out of the house too (_expire). The unknown ones are one Poisson process for all
        hypotheses, as in the PMBM (B_GarciaFernandez2018 eq. 7-10): what the hypotheses give back is
        added to it weighted by their probability."""
        if not self._call("recycle"):
            return
        self._flush(self.now)
        self._merge()
        self._version += 1

    def _objects(self, phantoms=False) -> list:
        """The distinct objects of all hypotheses (people; with phantoms: also the sources of ghost
        tracks), each with the hypotheses holding it (repeated as often as they hold it); of the
        mirror."""
        seen = {}
        for h, hy in enumerate(self._h):
            for obj in hy.objects() + (list(hy.phantom.values()) if phantoms else []):
                seen.setdefault(id(obj), (obj, []))[1].append(h)
        return list(seen.values())

    # ------------------------------------------------------------ evidence

    def _evidence(self, si, t, delta, ev):
        """What the frame says (MODEL.md 4.1, 4.2), in the core step by step; the ghost map learns
        in between (docs/rust-core.md 2.0)."""
        censored = delta <= 0
        events = censored or ev.born or ev.ended or self._c.any_lost([seg for seg, _ in ev.measured])
        if events:
            self._flush(t)  # these weigh, branch or merge everybody: all at t
            self._version += 1
        self._send_sensors()
        # 1. no ghost track was born meanwhile (echoes come with walkers in view)
        if delta > 0:
            self._call("watch", si, delta, self._ghost_rate_total(si))
            learn = not self.pauses.paused(t - delta, t)
            if self.learn_ghosts and learn:
                self.ghost_map.add_watch(self.sensors[si], delta)
                if self.ghost_map.fade(t):
                    self._ghost_total = {}
            for listener in self.listeners if learn else ():
                listener("watch", (self.sensors[si], delta))
        # 2. the sensor's tracks: measured (or found again), lost, still held, gone
        refound = self._call("track_events", si, t, [(seg, float(d.pos[0]), float(d.pos[1])) for seg, d in ev.measured],
                             [(seg, kind, float(d.pos[0]), float(d.pos[1])) for seg, kind, d in ev.lost],
                             [(seg, float(d.pos[0]), float(d.pos[1])) for seg, d in ev.held])
        ended = [seg for seg in ev.ended if self._c.has_track(seg)]
        for seg in ended:
            self._end(si, seg, t, ev.data_lost)
        if ended:
            self._merge()
        # 3. nothing seen before (start, lost data): a person there now is tracked with the share of
        # the time a track lasts against the time until one is started
        if censored:
            self._call("censor", si)
        # 4. new tracks, and lost ones found again; the map at a birth includes this frame's ends
        if refound or ev.born:
            found = [(seg, x, y, 0.0, 0.0) for seg, x, y in refound]
            found += [(seg, float(d.pos[0]), float(d.pos[1]), self._offset_var(si, d.pos), self._ghost_rate(si, d.pos))
                      for seg, d in ev.born]
            last = FRAME if censored else min(delta, FRAME)
            cats = self._call("branch_all", si, t, last, censored, [seg for seg, _ in ev.born], self.ghost_map.prior_rate,
                              found)
            for k, (seg, x, y, _, _) in enumerate(found):
                for listener in self.listeners if cats[k] else ():
                    listener("branch", (self.sensors[si], seg, np.array([x, y]), k < len(refound), cats[k]))
        self._normalize()

    def _offset_var(self, si, pos) -> float:
        """Variance (per axis, m^2) of where a track sits on a person at this distance: the
        measured spread along and across the line of sight (MODEL.md 4.1)."""
        p = self.p
        s = self.config.sensors[si]
        dist = math.hypot(pos[0] - s.x, pos[1] - s.y)
        sr = p.range_sigma_base + p.range_sigma_slope * dist
        st = p.lateral_sigma_base + p.lateral_sigma_slope * dist
        return 0.5 * (sr * sr + st * st)

    def _end(self, si, seg, t, data_lost):
        """The sensor dropped the track (in the core: for a person this follows from not finding
        it again; a ghost died: its hazard; a person left without tracks goes to the people
        without one). The ghost map learns where ghosts begin, with P(ghost) as judged now, but
        without what the map itself said at the birth (MODEL.md 4.2)."""
        z0, p_ghost, life, born = self._call("end", seg, t, data_lost)
        z0 = np.array(z0)
        learn = not self.pauses.paused(born, t)  # a track of a paused time teaches nothing
        if self.learn_ghosts and learn and p_ghost > 0:
            self.ghost_map.add_birth(self.sensors[si], z0, p_ghost)
            self._ghost_total.pop(si, None)
        for listener in self.listeners if learn else ():
            listener("track_end", (self.sensors[si], z0, p_ghost, life, data_lost))

    # ------------------------------------------------------- bookkeeping

    def _merge(self):
        """Hypotheses that now say the same about every live track are one (MODEL.md 5.6; core)."""
        self._call("merge")

    def _prune(self):
        """Keep the strongest hypotheses (MODEL.md 5.1; core), strongest first."""
        self._call("prune")

    def _normalize(self, dropped: float = 0.0):
        """The hypotheses' weights to sum 1 (core); FloatingPointError if they are not finite (the
        app's model starts over)."""
        self._call("normalize", dropped)

    def hyp_weights(self) -> np.ndarray:
        lw = np.array([h.logw for h in self._h])
        w = np.exp(lw - lw.max())
        return w / w.sum()

    def check(self):
        """The bookkeeping's invariants (for tests)."""
        keys = set()
        segs = self.segs
        for hy in self._h:
            k = hy.key()
            assert k not in keys, "two hypotheses alike"
            keys.add(k)
            assert set(hy.kind) == set(segs), "hypothesis and live tracks differ"
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
        self._call("check")  # the core's own: holders, pools, the parts gone through a door

    # -------------------------------------------------------------- LD2410C

    def _ld_geometry(self, si: int, pos, view: bool = True) -> tuple:
        """(degrees off the LD2410C's axis, slant distance, in sight) of the points (n, 2). In sight:
        the geometric sight times the measured view of the room the point is in (_ld_view; view=False:
        the geometric sight alone, as the view is measured against it)."""
        s = self.config.sensors[si]
        pos = np.asarray(pos, dtype=float).reshape(-1, 2)
        dx, dy = pos[:, 0] - s.x, pos[:, 1] - s.y
        ahead = dx * s._cos + dy * s._sin
        side = dx * s._sin - dy * s._cos
        angle = np.degrees(np.abs(np.arctan2(side, np.maximum(ahead, 1e-9))))
        slant = np.sqrt(dx * dx + dy * dy + (s.height - self.p.target_height) ** 2)
        sight = np.where(ahead > 0, self._los(si, pos), 0.0)
        if view:
            v = self._ld_view(si)
            if v is not None:
                i, j = self.world.cell_of(pos)
                sight = sight * v[self.room_of[i, j]]
        return angle, slant, sight

    def _ld_view(self, si: int):
        """Per room (index as room_of, the last for none) how much of what the geometric sight
        expects sensor si's LD2410C gets from a person there, against its own room (MODEL.md 4.3,
        "Blick des LD2410C"): measured from people the LD2450 of that room tracked while nobody was
        in the sensor's own room (tools/ld2410/footprint.py), shrunk towards the geometric 1. None:
        nothing measured for it (the geometric sight everywhere)."""
        key = ("ld_view", si)
        if key not in self._gcache:
            table = self.m.ld_view
            out = None
            if table:
                ox, oy = self.config.sensors[si].sight_origin()
                i, j = self.world.cell_of(np.array([[ox, oy]]))
                k = int(self.room_of[i[0], j[0]])
                if k >= 0:
                    own = self.rooms[k]
                    v = np.array([float(table.get(f"{own}<-{r}", 1.0)) for r in self.rooms] + [1.0])
                    v[k] = 1.0
                    out = v if (v != 1.0).any() else None
            self._gcache[key] = out
        return self._gcache[key]

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
        if not any(mg) and not any(sg[2:]):
            # every energy 0: no measurement but the firmware's "no value" (mm(): NaN -> 0). Without
            # anybody moving ring 0 is 9-16 (MODEL.md 4.3); none of 1.5 million recorded frames had all
            # 0. Weighed, it outvoted two LD2450 tracks of somebody sitting there (P 4e-6, BUGS 14)
            return
        e = rt.ld_e = np.array(list(mg) + list(sg[2:]), dtype=float)
        st = self._ld_stats.get(si)
        if st is None:
            st = self._ld_stats[si] = ld2410.Stats()
        frame = self.m.ld_frame
        # the LD2450 of the housing has something in sight (in a silence it had nothing)
        target = any(not d.hidden for d in rt.detections)
        if target or dt > IDLE:  # how long it has seen nothing (a gap in the data says nothing: from now)
            rt.ld_target_t = t
        if dt > IDLE:
            st.add(e, frame, target)
        elif dt > 2 * frame and prev is not None:
            st.add(prev, dt - frame)
            st.add(e, frame, target)
        else:
            st.add(e, dt, target)  # the next frame (or one lost on the way)

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

    def _ld_split(self, si: int, h, share: float) -> tuple:
        """The standing mass of the tiles sensor si's LD2410C sees (h a person or the unknown ones,
        times share): per level of the amplitude (A + 1, k), per tile (k,), and which tiles hold enough
        of a measured stay (not g = 1) to be weighed level by level (LD_TILE_MASS; the others at their
        mean amplitude, 1 where nothing was measured)."""
        key = (si, id(h))
        hit = self._ld_split_cache.get(key)
        if hit is None:
            idx = self._ld_tiles(si)[0]
            still = kernels.amp_mass(h.still, h.amp, idx)
            ms = still.sum(axis=0)
            hit = self._ld_split_cache[key] = (still, ms, share * (ms - still[-1]) > LD_TILE_MASS)
        return hit

    def _ld_points(self, si: int, obj) -> tuple:
        """Where sensor si's LD2410C sees a person (or the unknown ones): (masses (k,), what each puts
        into its cells (k, 16)): the tiles - walking, and standing at each level of the amplitude of
        their stay (4.3) -, or a Gaussian's components at their means. The rest of the mass (out of
        its view, behind a door, out of the house) puts nothing in."""
        idx, ss, sw = self._ld_tiles(si)

        def tiles(h, share):
            if not len(idx):
                return np.zeros(0), np.zeros((0, ld2410.CELLS))
            if not h.measured:  # g = 1 everywhere
                both = self._gcache.get(("ld2410 both", si))
                if both is None:
                    both = self._gcache[("ld2410 both", si)] = np.concatenate([sw, ss])
                return kernels.ld_tile_masses(h.walk, h.still, idx, float(share)), both
            still, ms, big = self._ld_split(si, h, share)
            amp = self.shapes.tile_amp
            # the small tiles at their mean amplitude: numpy's product, the rest compiled (the same numbers)
            return kernels.ld_tile_points(h.walk, still, ms, big, amp @ still[:, ~big], idx, sw, ss, amp, float(share))

        if isinstance(obj, Hidden):
            return tiles(obj, obj.r)
        w = obj.weights()
        angle, slant, sight = self._ld_geometry(si, obj.pos)
        # ld2410.expected, the standing profile at the standing component, the walking one at the walking
        rows = ld2410.expected_rows(self.m, angle, slant, sight, (STILL, WALK))
        s_still, s_walk = {STILL: rows[STILL]}, {WALK: rows[WALK]}
        # the standing component per level of its amplitude (4.3), then the walking one
        sh = self.shapes
        aw = obj.amw
        # a component out of its view (as a tile: expected energy below FLOOR) is not in view: mass 0
        # here, so it is weighed by 1 like the rest out of view (_ld_weigh). Until 0.25 it counted as
        # in view with nothing put in, and got P(no echo source) where its part gone through a door
        # got 1: every LD2410C with a likely echo source pushed every tracked person in the house
        # through a door (MODEL.md 10, "Meldungen 8.10. abends")
        vs = float(s_still[STILL].max()) > ld2410.FLOOR
        vw = float(s_walk[WALK].max()) > ld2410.FLOOR
        masses = [(1 - obj.a) * np.concatenate([w[STILL] * aw * vs, [w[WALK] * vw]])]
        S = [np.vstack([sh.amp[:, None] * s_still[STILL][None, :], s_walk[WALK][None, :]])]
        if obj.away is not None and obj.a > 0:
            m_, S_ = tiles(obj.away, obj.a)
            masses.append(m_)
            S.append(S_)
        return np.concatenate(masses), np.concatenate(S)

    @staticmethod
    def _ld_ratio(obj, pts, mu0, st, lik) -> tuple:
        """A person (or the unknown ones) on top of mean energies mu0 (16,): the log likelihood
        ratio per point (k,), the log of its normalizer - the factor of the whole person, or for
        the unknown ones log((1 + int lambda L) / (1 + Lambda)): at most one of them in view (the
        Poisson process truncated after one; afterwards moment-matched again) - and what they put in
        afterwards (16,) (kernels.ld_ratio)."""
        return st.ratio(pts[0], pts[1], mu0, lik, isinstance(obj, Undetected))

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
        self._ld_split_cache = {}  # per person: what of them stands where this LD2410C sees (_ld_split)
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
        rate = self.ld_background.rate(sid)
        echo.predict(st.time, m, rate)
        e_pts = echo.points(now)
        # what the background learns (below, MODEL.md 4.3): the people as the filter saw them before
        # these frames, per hypothesis the mean they and the background put in, and the probability
        # that nobody was in view; nothing while learning is paused (pause.py)
        learn = self.learn_ghosts and not self.pauses.paused(self.now - st.time, self.now)
        if learn:
            mus, nobody = [], 0.0
            for h, hy in enumerate(self._h):
                mu, none = b0.copy(), 1.0
                for o in hy.objects():
                    if id(o) in seen:
                        mm, S = pts[id(o)]
                        mu = mu + mm @ S
                        mass = float(mm.sum())
                        none *= 1.0 / (1.0 + mass) if isinstance(o, Undetected) else max(1.0 - mass, 0.0)
                mus.append(mu)
                nobody += prior_w[h] * none
        # per hypothesis the people one after the other, the echo sources last
        memo = {}
        cache = {}  # (object, what the others put in) -> its ratio and normalizer
        logf = np.zeros(len(self._h))
        totals, owns, tracked = [], [], []
        for h, hy in enumerate(self._h):
            R = np.zeros(ld2410.CELLS)
            prefix = ()
            own = []
            for o in list(hy.groups.values()) + hy.hidden + [hy.ppp]:
                if id(o) not in seen:
                    continue
                key = (id(o), prefix)
                hit = memo.get(key)
                if hit is None:
                    # with what they put in afterwards (for the unknown ones: the moment-matched intensity)
                    lr, norm, post = cache[(id(o), R.tobytes())] = self._ld_ratio(o, pts[id(o)], b0 + R, st, lik)
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
        self._dirty = True  # the mirror's hypotheses and objects change: loaded before the next call
        for h, hy in enumerate(self._h):
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
                lr, norm, _ = hit
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
                # what is out of its view (a Gaussian's component there, _ld_points) stays as it is
                logf_pts = np.where(pts[id(obj)][0] > 0, logf_pts, 0.0)
            self._ld_apply(si, obj, np.clip(logf_pts, -700.0, ld2410.LOG_CAP))
        # the echo sources given the people with tracks and the unseen ones as they were, mixed over
        # the hypotheses
        q = post_w / post_w.sum()
        parts, outs = [], []
        for qh, h in zip(q, range(len(self._h))):
            if qh < 1e-12:
                continue
            lr, norm, _ = self._ld_ratio(echo, e_pts, b0 + tracked[h], st, lik)
            lq = math.log(qh) - norm
            parts.append(lq + lr)
            outs.append(lq)
        parts = np.array(parts)
        top = parts.max(axis=0)
        echo.weigh(np.clip(top + np.log(np.exp(parts - top).sum(axis=0)) - _logsumexp(outs), -700.0, ld2410.LOG_CAP))
        if learn:
            # the background where no echo source is on (as judged with these frames), and the
            # echo sources' rate where nobody is in view
            rt = self.runtime.get(sid)
            quiet = self.now - rt.ld_target_t if rt is not None else 0.0
            self.ld_background.learn(sid, b, np.array(mus), prior_w, st, lik.alpha, float(echo.p[0]), quiet)
            self.ld_background.learn_echoes(sid, echo.began(), rate, nobody, st.time)
        self._normalize()

    def _ld_apply(self, si: int, obj, logf: np.ndarray):
        """Multiply a person (or the unknown ones) by exp(logf) at the points of _ld_points
        (elsewhere by 1); logf is capped (ld2410.LOG_CAP), so the factors stay finite."""
        idx, _, _ = self._ld_tiles(si)
        n = len(idx)
        AT = len(self.shapes.tile_amp)

        def tiles(h, g, share):
            # standing: per level of the amplitude; the factor per detectability is the sum over the
            # levels given it, and the amplitude's levels given it are reweighed (Swerling III, 4.3).
            # A tile with little of the person: one factor at its mean amplitude (_ld_points)
            # the factors at the tiles idx (1 elsewhere: Hidden.weigh_at)
            if not h.measured:  # g = 1 everywhere (_ld_points)
                e = np.exp(g)
                return h.weigh_at(idx, e[:n], e[n:])
            fw = np.exp(g[:n])
            fs = np.empty((len(self.shapes.kappa), n))
            big = self._ld_split(si, h, share)[2]
            nb = int(big.sum())
            ib = idx[big]
            gs = g[n:n + AT * nb].reshape(AT, nb)
            top = gs.max(axis=0) if nb else np.zeros(0)
            E = np.exp(gs - top[None, :])  # (A, nb)
            amp = h.amp[:, :, ib] * E[None, :, :]  # (K, A, nb)
            tot = amp.sum(axis=1)  # (K, nb)
            h.amp[:, :, ib] = np.where(tot[:, None, :] > 0, amp / np.where(tot > 0, tot, 1.0)[:, None, :], h.amp[:, :, ib])
            fs[:, big] = tot * np.exp(top)[None, :]
            fs[:, ~big] = np.exp(g[n + AT * nb:])[None, :]
            return h.weigh_at(idx, fw, fs)

        if isinstance(obj, Hidden):
            tiles(obj, logf, obj.r)
            return
        A = len(self.shapes.amp)
        d_away = tiles(obj.away, logf[A + 1:], obj.a) if obj.away is not None and obj.a > 0 and n else 0.0
        # the amplitude's levels: their probabilities, and the factor of the standing component
        top = float(logf[:A].max())
        f = np.exp(logf[:A] - top)
        ka = obj.ka * f[None, :]  # with the detectability (MODEL.md 4.3)
        s = float(ka.sum())
        if s > 0:
            obj.ka = ka / s
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
                 for w, hy in zip(hw, self._h)]
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
        hy = self._h[int(np.argmax([h.logw for h in self._h]))]
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

    # ------------------------------------------------- "about to be entered" (MODEL.md 6)

    def entering(self) -> dict:
        """zone id -> {"p", "eta", "distance", "person"} for the observed rooms and the areas: the
        probability that somebody walking with a track enters the zone within the look-ahead
        (lead_time), and for the one who most probably does, the expected time
        and distance to it and their id in the display (persons(); None if not in the most
        probable hypothesis)."""
        return self._cached("entering", lambda: self._entering(self.p.lead_time))

    def _entry_targets(self):
        """The zones a "wird betreten" is computed for - the observed rooms (by the tiles' rooms)
        and the areas - as (zone ids, (sample points, zones) share of each point in each zone,
        rasters (zones, nx, ny) of their cells)."""
        cached = getattr(self, "_targets", None)
        if cached is not None and cached[0] is self.tiles:
            return cached[1:]
        tl, w = self.tiles, self.world
        areas = self.config.home_zones("area")
        ids = list(self.rooms) + [z.id for z in areas]
        A = np.zeros((len(tl.points), len(ids)))
        masks = np.zeros((len(ids), w.nx, w.ny), dtype=bool)
        room = tl.room[tl.point_tile]
        for k in range(len(self.rooms)):
            A[room == k, k] = 1.0
            masks[k] = self.room_of == k
        ci, cj = w.cell_of(tl.points)
        for k, z in enumerate(areas, start=len(self.rooms)):
            masks[k] = w.zone_mask(z)
            A[masks[k][ci, cj], k] = 1.0
        self._targets = (self.tiles, ids, A, masks)
        return ids, A, masks

    def _walk_ahead(self, obj, h: float):
        """The position (mean (2,), variance (2,)) of a person's walking component h s ahead: the
        OU velocity of MODEL.md 3.2 over one step h, the walls reflecting the move as in
        Gauss.walls (sigma points, 5.2); door gaps are no wall."""
        m = self.m
        s2 = 0.5 * (m.speed ** 2 + m.speed_spread ** 2)
        F, Q = kernels.motion(2, float(h), float(m.still_noise ** 2), 1.0 / m.turn_rate, float(s2), 0.0,
                              np.zeros(0, dtype=np.int64), np.zeros(0))
        F, Q = F[WALK], Q[WALK]
        m0 = np.ascontiguousarray(obj.mean[WALK, :, :2])
        P0 = np.ascontiguousarray(obj.cov[WALK, :, :2, :2])
        mean = m0 @ F.T
        cov = np.einsum("ij,ajk,lk->ail", F, P0, F) + Q[None]
        (x0, y0), (x1, y1) = m0[:, 0].tolist(), mean[:, 0].tolist()
        reach = math.hypot(x1 - x0, y1 - y0) + math.sqrt(8.0) * (
            math.sqrt(max(float(P0[:, 0, 0].max()), 0.0)) + abs(F[0, 1]) * math.sqrt(max(float(P0[:, 1, 1].max()), 0.0))
            + math.sqrt(max(Q[0, 0], 0.0)))
        if not self.world.clear(x0, y0, reach):
            _, mean, cov = kernels.wall_moves(m0, P0, F, Q, mean, np.ascontiguousarray(cov), self.world.walls,
                                              self.world.wall_box)
        return mean[:, 0], cov[:, 0, 0]

    def _target_mass(self, mean, var, A) -> np.ndarray:
        d = self.tiles.gauss_points(mean, var)
        return np.zeros(A.shape[1]) if d is None else d @ A

    def _entering(self, h: float) -> dict:
        """entering() for the look-ahead h (s).

        Per person with a track (not a ghost source), only their walking component moves on
        (standing people sway by centimetres; getting up goes anywhere): where it is h/2 and h
        s ahead - unless the walk ends first (rate speed / walk length, MODEL.md 3.2): then
        where it stopped, the time of stopping on the nodes 0, h/2, h -, against where it is
        now. Their rise in each zone, max(P(in it at h/2), P(in it at h)) - P(in it now), at
        least 0, is the probability that they enter it within h (a lower bound of being in it
        at some time; whoever is in it stays). People of a hypothesis are independent:
        P(somebody enters) = 1 - prod(1 - rise), mixed over the hypotheses (5.1).
        Deterministic, no samples."""
        ids, A, masks = self._entry_targets()
        Z = len(ids)
        out = {z: {"p": 0.0, "eta": None, "distance": None, "person": None} for z in ids}
        if not Z or h <= 0:
            return out
        m = self.m
        mu = m.speed / m.walk_length
        rise, walk = {}, {}
        for obj, _ in self._objects():
            if not isinstance(obj, Gauss) or obj.phantom:
                continue
            w = float(obj.weights()[WALK]) * (1.0 - obj.a)
            if w < ENTER_MIN_WALK:
                continue
            pos, vel = obj.mean[WALK, :, 0], obj.mean[WALK, :, 1]
            speed = math.hypot(float(vel[0]), float(vel[1]))
            now = self._target_mass(pos, obj.cov[WALK, :, 0, 0], A)
            half = self._target_mass(*self._walk_ahead(obj, 0.5 * h), A)
            end = self._target_mass(*self._walk_ahead(obj, h), A)
            # who stops stays where they stopped: the time of stopping on the three nodes
            # (0, h/2, h), each taking the stops nearest to it
            q1, q3 = (math.exp(-mu * h * f) for f in (0.25, 0.75))
            at_half = (1.0 - q1) * now + q1 * half
            at_end = (1.0 - q1) * now + (q1 - q3) * half + q3 * end
            r = w * np.maximum(np.maximum(at_half, at_end) - now, 0.0)
            if r.max() > 0:
                rise[id(obj)] = r
                walk[id(obj)] = (obj, pos.copy(), vel.copy(), speed)
        if not rise:
            return out
        hw = self.hyp_weights()
        p = np.zeros(Z)
        share = {k: np.zeros(Z) for k in rise}
        for wh, hy in zip(hw, self._h):
            empty = np.ones(Z)
            for obj in hy.people():
                r = rise.get(id(obj))
                if r is not None:
                    empty *= 1.0 - r
                    share[id(obj)] += wh * r
            p += wh * (1.0 - empty)
        best = self._h[int(np.argmax([h.logw for h in self._h]))]
        display = {id(o): k + 1 for k, o in enumerate(best.people())}
        for k, z in enumerate(ids):
            out[z]["p"] = float(min(max(p[k], 0.0), 1.0))
            if p[k] < 0.01:
                continue
            key = max(share, key=lambda q: share[q][k])
            obj, pos, vel, speed = walk[key]
            dist = self._ray_distance(pos, vel, masks[k])
            out[z].update({"distance": dist, "eta": dist / speed if dist is not None and speed > 0.05 else None,
                           "person": display.get(key)})
        return out

    def _ray_distance(self, pos, vel, mask, reach: float = 5.0, step: float = 0.05):
        """How far a walker at pos walks in the direction of vel until they are in the zone (its
        raster mask), if no wall is in the way and within reach; else None."""
        speed = math.hypot(float(vel[0]), float(vel[1]))
        if speed < 1e-6:
            return None
        s = np.arange(0.0, reach + step / 2, step)
        pts = pos[None, :] + s[:, None] * (vel / speed)[None, :]
        i, j = self.world.cell_of(pts)
        inside = np.flatnonzero(mask[i, j])
        if not len(inside):
            return None
        hit = int(inside[0])
        if hit and self.world.crosses_wall(pos[None, :], pts[hit][None, :])[0]:
            return None
        return float(s[hit])

    # ------------------------------------------------------- "Ziel" (MODEL.md 6)

    def threshold(self, zone: str) -> float:
        """The room's threshold for "Ziel" (its own, else the default)."""
        p = self.p
        try:
            return float((p.target_thresholds or {}).get(zone, p.target_threshold))
        except (TypeError, ValueError, AttributeError):
            return float(p.target_threshold)

    def targets(self) -> dict:
        """room id -> "Ziel" for the observed rooms: {"p", "on", "map", "weight", "walks", "from",
        "distance", "eta", "person"} (MODEL.md 6).

        The walkers' own motion ("wird betreten", p_enter) is the prior; the learned map of where walks
        went (destination.py) its data, per walker a Dirichlet posterior with dest_prior_walks walks of
        prior weight. For the room: P = (1 - lam) q_move + lam q_map with
          - q_move = p_enter on the scale of the room's threshold c: its odds times odds(c) / odds(c_v),
            so that q_move >= c exactly where p_enter > c_v ("wird betreten" on);
          - q_map = sum_h w_h (1 - prod_k (1 - P(k walks) f_k(room))), f_k = the share of the walks
            counted at walker k's cell and velocity that went to the room (by a door, not across a
            divider: there 0);
          - lam = n / (n + prior) with n the walks counted for the walkers who could go there
            (weighted by P(walking) and their hypotheses' weights).
        On if P >= c; with nothing learned there (lam = 0) exactly when "wird betreten" is on. Only
        reads the hypotheses and the map."""
        return self._cached("targets", self._target_outputs)

    def _target_outputs(self) -> dict:
        from .filtermodel import WALK
        geo, dm = self.dest_geo, self.dest_map
        enter = self.entering()
        p = self.p
        c_v = p.approach_cost / (p.approach_cost + 1.0)
        K = len(self.rooms)
        prior = self.m.dest_prior_walks
        walkers = {}  # id(obj) -> (P(walking), room, space, f (C,), n, x, y, vx, vy)
        for obj, _ in self._objects():
            if not isinstance(obj, Gauss) or obj.phantom:
                continue
            pw = float(obj.weights()[WALK]) * (1.0 - obj.a)
            if pw < ENTER_MIN_WALK:
                continue
            x, y = float(obj.mean[WALK, 0, 0]), float(obj.mean[WALK, 1, 0])
            vx, vy = float(obj.mean[WALK, 0, 1]), float(obj.mean[WALK, 1, 1])
            if math.hypot(vx, vy) < destination.MIN_SPEED:
                continue
            near = geo.around(x, y)
            if near is None:
                continue
            room = int(geo.cell_room[near[0][0]])
            cnt = dm.lookup(*near, vx, vy)
            n = float(cnt.sum())
            f = cnt / n if n > 0 else np.zeros(geo.C)
            walkers[id(obj)] = (pw, room, int(geo.space[room]), f, n, x, y, vx, vy)
        hw = self.hyp_weights() if self._h else []
        q_map = np.zeros(K)
        num, den = np.zeros(K), np.zeros(K)
        share = {}
        for wh, hy in zip(hw, self._h):
            empty = np.ones(K)
            for obj in hy.people():
                wk = walkers.get(id(obj))
                if wk is None:
                    continue
                pw, room, space, f, n, *_ = wk
                # the rooms this walker could go to next: by a door, or across a divider (there f = 0)
                rel = geo.allowed[space, :K] | (geo.space == space)
                rel[room] = False
                pk = pw * f[:K] * rel
                empty *= 1.0 - pk
                num += wh * pw * n * rel
                den += wh * pw * rel
                share.setdefault(id(obj), np.zeros(K))
                share[id(obj)] += wh * pw * rel * (f[:K] + 1e-3)
            q_map += wh * (1.0 - empty)
        best = self._h[int(np.argmax([h.logw for h in self._h]))] if self._h else None
        display = {id(o): k + 1 for k, o in enumerate(best.people())} if best is not None else {}
        out = {}
        for k, z in enumerate(self.rooms):
            c = min(max(self.threshold(z), 1e-6), 1.0 - 1e-6)
            pe = enter.get(z, {}).get("p", 0.0)
            n = num[k] / den[k] if den[k] > 0 else 0.0
            lam = n / (n + prior) if n > 0 else 0.0
            if pe <= 0.0:
                q_move = 0.0
            elif pe >= 1.0:
                q_move = 1.0
            else:
                o = pe / (1.0 - pe) * (c / (1.0 - c)) / (c_v / (1.0 - c_v))
                q_move = o / (1.0 + o)
            if lam > 0:
                prob = (1.0 - lam) * q_move + lam * float(q_map[k])
                on = prob >= c
            else:
                prob = q_move
                on = pe > c_v  # nothing learned for these walkers: "wird betreten" decides
            e = {"p": float(min(max(prob, 0.0), 1.0)), "on": bool(on), "map": float(q_map[k]), "weight": lam,
                 "walks": n, "from": None, "distance": None, "eta": None, "person": None}
            if prob >= 0.01 and share:
                key = max(share, key=lambda q: share[q][k])
                if share[key][k] > 0:
                    pw, room, space, f, _, x, y, vx, vy = walkers[key]
                    dist = geo.door_distance(space, k, x, y)
                    speed = math.hypot(vx, vy)
                    e.update({"from": self.rooms[room], "distance": dist,
                              "eta": dist / speed if dist is not None and speed > 0.05 else None,
                              "person": display.get(key)})
            out[z] = e
        return out

    def occupancy(self) -> dict:
        """Observed room id -> (occupied, source), MODEL.md 6 "Belegt": P(somebody there) above the
        threshold c = light_cost / (light_cost + 1), or the room's own LD2450 measured somebody in it
        within the last seen_hold s (roomseen.py). source: "filter", "ld2450", "beide" (both), None
        if not occupied. Only an output: reads the filter, changes nothing in it."""
        p = self.p
        c = p.light_cost / (p.light_cost + 1.0)
        counts = self.count_distribution()
        seen = self.seen.seen(self.now, p.seen_hold)
        out = {}
        for z in self.rooms:
            if z not in counts:
                continue
            f, s = 1 - float(counts[z][0]) > c, z in seen
            out[z] = (f or s, "beide" if f and s else "filter" if f else "ld2450" if s else None)
        return out

    def zone_states(self) -> dict:
        """Per zone: the most probable number of people (observed rooms: from all hypotheses),
        moving / still from the most probable hypothesis' people, "about to be entered" from all
        (entering()). Occupied, where the probability is known: P(somebody there) above the
        threshold that minimizes the expected cost of the light; about to be entered likewise,
        with the cost of a light switched on in vain against that of entering in the dark
        (MODEL.md 6). An observed room is also occupied while its own LD2450 measures somebody in it
        (occupancy()); its count is then at least 1, and the house's too. A room whose own sensors in
        use all went silent (roomseen.RoomSeen.silent) is not available: no data, Home Assistant gets
        "unavailable" instead of its state (ha.py); the state itself is computed as always."""
        from .zones import ZoneState
        p = self.p
        c = p.light_cost / (p.light_cost + 1.0)
        states = {}
        region_of = {room: rid for rid, r in self.config.regions.items() for room in r["rooms"]}
        counts = self.count_distribution()
        places = self.place_distribution()
        occupancy = self.occupancy()
        raised = False  # a count raised to 1 by the room's own LD2450
        for z in self.config.home_zones("room"):  # the stairwell is outside: no state (MODEL.md 6)
            st = ZoneState()
            rid = region_of.get(z.id)
            if rid is None:
                if z.id in counts:
                    st.count = int(np.argmax(counts[z.id]))
                    st.probability = 1 - float(counts[z.id][0])
                    st.decided, st.source = occupancy[z.id]
                    if st.source in ("ld2450", "beide") and st.count == 0:
                        st.count, raised = 1, True
            else:
                dist = places.get(rid, [1.0])
                st.probability = 1 - float(dist[0])
                if len(self.config.regions[rid]["rooms"]) == 1:
                    st.count = int(np.argmax(dist))
                    st.decided = st.probability > c
                    st.source = "filter" if st.decided else None
            states[z.id] = st
        total = ZoneState()
        zones = self.config.home_zones("room", "area")
        for z in zones:
            states.setdefault(z.id, ZoneState())
        total.count = max(int(np.argmax(places["_house"])), int(raised))
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
        c_enter = p.approach_cost / (p.approach_cost + 1.0)
        for zid, e in self.entering().items():
            st = states.get(zid)
            if st is None:
                continue
            st.p_enter = e["p"]
            st.approaching = e["p"] > c_enter
            st.c_enter = c_enter
            st.eta, st.distance, st.person = e["eta"], e["distance"], e["person"]
        for zid, e in self.targets().items():
            st = states.get(zid)
            if st is None:
                continue
            st.target = e["on"]
            st.c_target = self.threshold(zid)
            st.p_target, st.target_weight, st.target_walks = e["p"], e["weight"], e["walks"]
            st.target_from, st.target_distance, st.target_eta, st.target_person = e["from"], e["distance"], e["eta"], e["person"]
        for st in (*states.values(), total):
            st.moving = min(st.moving, st.count)
            st.still = min(st.still, st.count - st.moving)
        for zid in self.seen.silent(self.now, self.start):
            if zid in states:
                states[zid].available = False
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
                "online": self.seen.online(sid, t, self.start),  # as Home Assistant's rooms (roomseen.py)
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
        return {"ghost_map": self.ghost_map.to_dict(), "ld_background": self.ld_background.to_dict(),
                "dest_map": self.dest_map.to_dict()}

    def use_ld_background(self, bg: "ld2410.Background"):
        """Use a learned LD2410C background (MODEL.md 4.3), per sensor only where it was learned
        with the sensor where it is now."""
        self.ld_background = bg
        bg.use(self.config)

    def load_learned(self, data: dict):
        if data.get("ghost_map"):  # learned offline (tools/ghostmap.py), per sensor unless mounted elsewhere since
            from .ghostmap import GhostMap
            self.use_ghost_map(GhostMap.from_dict(data["ghost_map"]))
        if data.get("ld_background"):
            self.ld_background.load_dict(data["ld_background"])
            self.ld_background.use(self.config)
        if data.get("dest_map"):  # used if the rooms and doors are the same
            self.use_dest_map(destination.DestinationMap.from_dict(data["dest_map"]))
