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
import json
import math
import os
import zlib

import numpy as np

from .filtermodel import FRAME, IDLE, Model, Shapes
from .frames import Detection, SensorRuntime
from . import kernels, ld2410
from .gauss import Gauss
from .hidden import Hidden, Undetected
from . import destination
from .pause import Pauses
from . import roomseen
from .sensormodel import PD_MAX, SensorModel
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

# "python": the core runs in the world this shell built (its export), not in its own - the aid that checks
# the core's build (docs/rust-core.md 6; rust-port/cmpworld.py), never in the app
WORLD = os.environ.get("PT_WORLD", "core")

MAX_STEP = 0.2  # s: motion is cut into parts no longer than this
GIVE_UP = 0.01  # a known person who exists with less probability (wherever they are: refuted, or where
                # nothing could test them, Tracker._expire) joins the unknown ones with r x their density:
                # only P(several of them come back) changes, by <= GIVE_UP^2 / 2 (0.05: 14 % less
                # computing time, light wrongly off 1.67 instead of 1.17)
RECYCLE_EVERY = 1.0  # s
LD_TILE_MASS = 1e-4  # persons: a tile holding less of a person (or of the unknown ones) standing is weighed
                     # by the LD2410C at its mean amplitude, not level by level (_ld_split; MODEL.md 4.3)
# what only the outputs (MODEL.md 6) or the display read: a change restarts nothing
OUTPUT_PARAMS = ("target_threshold", "target_thresholds", "light_cost", "seen_hold", "approach_cost", "lead_time",
                 "ld2410_hold")
GAP_EXACT = 120.0  # s: the end of a gap in the data is moved as always, what lies before in leaps
LEAP = 15.0  # s   (_predict_gap; 7.10. 08:06, 8 people, 3 h: rooms within 0.022 of moving all as always)
MAX_LEAPS = 2000  # longer gaps in longer leaps (8 h of 8 people: 1.7 s CPU; a week in leaps of 5 min,
                  # 8 h in leaps of 4 min put the rooms up to 0.12 off)


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
    Python - the destination map, tests and tools (docs/rust-core.md 7; thrown away when they are in
    Rust). Only for reading: nothing goes back into the core. serial: the core's state it shows."""

    __slots__ = ("serial", "hyps", "segs", "live")


class _Seen:
    """The rooms' own LD2450s (roomseen.py, MODEL.md 6 "Belegt") as the core keeps them, for reading:
    the app's and the tests' view until the plan is in the core (phase 6). Silent after
    roomseen.SILENT_AFTER s, read at each call."""

    def __init__(self, tracker):
        self._tr = tracker

    @property
    def own(self) -> dict:
        """sensor id -> the id of its own room"""
        return dict(self._tr._own)

    @property
    def last(self) -> dict:
        """room id -> the last time its own LD2450 measured somebody in it"""
        return dict(self._tr._c.seen_last())

    def watched(self) -> set:
        """The rooms with an own sensor in use: these may be silent (Home Assistant: their own availability)."""
        return {r for r, w in zip(self._tr.rooms, self._tr._c.watched()) if w}

    def silent(self, now: float, start: float | None) -> set:
        """The rooms whose own sensors in use all sent no frame for more than roomseen.SILENT_AFTER s
        (as it was when the world was built), counted from start for one that sent nothing (None: not
        started, nothing silent)."""
        return {r for r, q in zip(self._tr.rooms, self._tr._c.silent(now, start)) if q}

    def online(self, sensor_id: str, now: float, start: float | None) -> bool:
        """The sensor sent a frame lately (the web UI's "online" too)."""
        return self._tr._c.online(sensor_id, now, start)


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
        self.seen = _Seen(self)  # the rooms' own LD2450 (output only, MODEL.md 6 "Belegt"; in the core)
        self._build()
        self.use_ghost_map(None)
        self.use_dest_map(None)
        self.reset_people(people)

    # --------------------------------------------------------------- setup

    def _build(self):
        # what the sensors' LD2410C frames left goes on over a rebuild (until 0.33 the runtime held it)
        collected = {sid: self._c.ld_collect(k) for k, sid in enumerate(self.sensors)} if self._c is not None else {}
        old = self._c
        self.world = World(self.config)
        self.sensors = [s.id for s in self.config.sensors]
        self.sidx = {sid: k for k, sid in enumerate(self.sensors)}
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
        self._c = _core(presence_core.Core, *self._world())
        self._c.loglik = loglik
        self._check_world()
        rooms, places, areas, own = self._c.names()
        self._own = {sid: r for sid, r in zip(self.sensors, own) if r is not None}
        self._entry_ids = rooms + areas
        if old is not None:  # what the rooms' own LD2450s saw goes on, and the ids of new tracks
            self._c.adopt(old)
        for si, sid in enumerate(self.sensors):
            if sid in collected:
                self._c.set_ld_collect(si, collected[sid])
        self._sent = None  # the sensors' state the core has
        self._mirror = None
        self._out_cache = {}

    def _check_world(self):
        """The core built its own world: the shell's, still read by what is not ported yet (the
        destination map, the saved state's format and fingerprint, the mirror, /api/tiles), must be the
        same - the rooms and places, and the tiles bit for bit; else a CoreError (nothing would fit)."""
        rooms, places, _, _ = self._c.names()
        tl = self.tiles
        centers, area, room, exits, entries, doors = self._c.tiles()
        same = (rooms == self.rooms and places == self.world.places
                and self._c.shape() == (tl.n, tl.R, tl.A)
                and np.array_equal(np.asarray(centers), np.ravel(tl.centers))
                and np.array_equal(np.asarray(area), tl.area) and list(room) == [int(r) for r in tl.room]
                and list(exits) == [int(x) for x in tl.exits] and list(entries) == [int(c) for c in tl.entries]
                and [list(d) for d in doors] == [[int(c) for c in tl.doors[r]] for r in range(tl.R)])
        if not same:
            raise CoreError(f"the core's world {rooms, places, self._c.shape()} is not the shell's")

    def _world(self) -> tuple:
        """What the core builds its world of (docs/rust-core.md 1): the configuration and the model as
        JSON, roomseen.SILENT_AFTER; with PT_WORLD=python also the world as this shell built it."""
        return (json.dumps(self.config.to_dict()), self._model(), float(roomseen.SILENT_AFTER),
                self._export() if WORLD == "python" else None)

    def _model(self) -> str:
        """The model's numbers as JSON, with the filter's constants (params.rs)."""
        m = self.m
        names = ("still_noise", "kappa_switch", "speed", "speed_spread", "turn_rate", "walk_length", "arrive_rate",
                 "guest_rate", "forget_rate", "find_radius", "res_range", "res_cross", "const_share", "tau",
                 "unseen_life", "white", "pos_every", "find_clutter", "ghost_echo", "hyp_mass", "start_unknown",
                 "go_share", "go_time", "kappa_shape", "ld_amp_shape", "kappa_amp_corr")
        # JSON has no infinity: such numbers go as "inf", "-inf", "nan" (params.rs)
        params = {k: (v if math.isfinite(v) else str(v)) for k in names for v in [float(getattr(m, k))]}
        params.update(acquire=[[float(x) for x in a] for a in m.acquire], track_life=[float(x) for x in m.track_life],
                      refind={k: [float(x) for x in v] for k, v in m.refind.items()},
                      ghost_types=[[float(r), float(life)] for r, life in self._ghost_types()], max_hyps=int(m.max_hyps),
                      give_up=GIVE_UP, max_step=MAX_STEP, gap_exact=GAP_EXACT, leap=LEAP, max_leaps=MAX_LEAPS,
                      ld_moving=[[float(x) for x in r] for r in m.ld_moving],
                      ld_still=[[float(x) for x in r] for r in m.ld_still], ld_beam=[float(x) for x in m.ld_beam],
                      ld_shape=[float(x) for x in m.ld_shape], ld_tau=[float(x) for x in m.ld_tau],
                      ld_gain=[float(x) for x in m.ld_gain], ld_burst=[float(x) for x in m.ld_burst],
                      ld_every=float(m.ld_every), ld_frame=float(m.ld_frame), ld_memory=float(m.ld_memory),
                      ld_echo_life=float(m.ld_echo_life), ld_echo_stages=int(m.ld_echo_stages),
                      ld_echo_ranges=[float(x) for x in m.ld_echo_ranges], ld_echo_amps=[float(x) for x in m.ld_echo_amps],
                      ld_tile_mass=LD_TILE_MASS, stay_points=[float(x) for x in m.stay_points],
                      kappa_levels=int(m.kappa_levels), ld_amp_levels=int(m.ld_amp_levels),
                      ld_view={str(k): float(v) for k, v in (m.ld_view or {}).items()})
        return json.dumps(params)

    def _export(self) -> dict:
        """The world as this shell built it (docs/rust-core.md 6: the WorldExport, the aid that checks
        the core's build): the model, the shapes, the raster and walls, the tiles, per sensor where it is
        and its sight."""
        tl, w, sh = self.tiles, self.world, self.shapes

        def f(a):
            return np.ascontiguousarray(a, dtype=float)

        def i(a):
            return np.ascontiguousarray(a, dtype=np.int64)
        d = tl.door_tiles
        return dict(
            params=self._model(), go=f(sh.go), go_w=f(sh.go_w), go_w_ongoing=f(sh.go_w_ongoing), kappa=f(sh.kappa),
            kappa_w=f(sh.kappa_w), amp=f(sh.amp), ka_w=f(sh.ka_w), amp_given_kappa=f(sh.amp_given_kappa), x0=float(w.x0),
            y0=float(w.y0), nx=int(w.nx), ny=int(w.ny), walls=f(w.walls), wall_dist=f(w.wall_grid), n=int(tl.n), R=int(tl.R),
            A=int(tl.A), tick=float(tl.tick), walk_t=f(tl.T_walk_t), into_t=f(tl.T_in_t), centers=f(tl.centers),
            tile_var=f(tl.var), area=f(tl.area), fine=i(tl.fine), fine_xy=f(tl.fine_xy), door_center=f(tl.centers[d]),
            door_var=f(tl.var[d]), door_area=f(tl.area[d]), door_rates=f(tl.door_rate[d]), door_ptr=i(tl.door_ptr),
            door_list=i(tl.door_list), exits=i(tl.exits), entries=i(tl.entry_idx), region_rates=f(tl.region_rates()),
            widths=f(tl.widths), points=f(tl.points), point_tile=i(tl.point_tile), point_count=f(tl.point_count),
            sensors=[self._export_sensor(si) for si in range(len(self.sensors))],
            rooms=f(np.where(self.room_of >= 0, self.room_of, len(self.rooms))), tile_room=i(tl.room),
            region_start=f([tl.region_start(r) for r in range(tl.R)]).reshape(-1), generator=f(tl.Q))

    def _export_sensor(self, si: int) -> dict:
        """A sensor for the core: where it is, its sight, how well it tests a person per tile; its
        LD2410C's axis, height above a person, line of sight, view of the rooms, tiles (_ld_tiles)."""
        s, tl = self.config.sensors[si], self.tiles

        def f(a):
            return np.ascontiguousarray(a, dtype=float)
        self._grid(si)
        idx, ss, sw = self._ld_tiles(si)
        view = self._ld_view(si)
        return dict(at=(float(s.x), float(s.y)), origin=tuple(map(float, s.sight_origin())), grid=f(self._grid(si)),
                    observed=f(tl.observed((si,))), ld_axis=(float(s._cos), float(s._sin)),
                    ld_dh2=float((s.height - self.p.target_height) ** 2), ld_los=f(self._gcache[("los", si)]),
                    ld_view=None if view is None else f(view), ld_idx=np.ascontiguousarray(idx, dtype=np.int64),
                    ld_still=f(ss), ld_walk=f(sw))

    # the core's state, as the Python filter named it

    @property
    def _version(self) -> int:
        """What the outputs are cached by: the core counts the moves of everybody and the events that
        concern all hypotheses (the Python filter's _version: between them the outputs are kept)."""
        return self._c.epoch

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
        """A call into the core (through _core: a panic is a CoreError)."""
        return _core(getattr(self._c, name), *args)

    def _view(self) -> _Mirror:
        """The mirror of the core's state, made anew when the core changed."""
        m = self._mirror
        if m is None or m.serial != self._c.serial:
            m = self._mirror = self._dump()
        return m

    @property
    def _h(self) -> list:
        """The hypotheses for reading (the mirror's)."""
        return self._view().hyps

    @property
    def hyps(self) -> list:
        """The hypotheses as Python objects, for reading (a mirror of the core's: a change in them
        reaches nothing)."""
        return self._view().hyps

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
        tl = self.tiles
        ds, ms = {}, {}
        for k, (walk, still, region, out, clock, r, amp, keep), intensity in dens:
            h = (Undetected if intensity else Hidden).__new__(Undetected if intensity else Hidden)
            h.tiles = tl
            h.walk, h.still, h.region, h.out, h.clock, h.r, h._amp, h._keep = walk, still, region, out, clock, r, amp, keep
            ds[k] = h
        for k, logw, mean, cov, gow, ka, slots, phantom, a, away, t in mixs:
            g = Gauss(logw, mean, cov, gow, ka, {s: j for s, j, _ in slots}, {s: v for s, _, v in slots}, phantom, a,
                      None if away is None else ds[away], t)
            ms[k] = g
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

    def _send_sensors(self):
        """The sensors' state as the core needs it for which of them watch (_live)."""
        state = [(bool(s.enabled and s.placed and s.id in self.runtime),
                  self.runtime[s.id].last_frame if s.id in self.runtime else -math.inf) for s in self.config.sensors]
        if state != self._sent:
            self._sent = state
            _core(self._c.set_sensors, state)

    def reset_people(self, people=None):
        """Start over (in the core): nothing known about the people (MODEL.md 5.3: no known people, the
        unknown ones anywhere, start_unknown of them expected - no fixed number: the data make known
        people of them, through the tracks there at the start (4.1) and the ways in; without any in
        view, whoever sits in view at the start can only be a ghost, the desk at 22:19 and 08:06; with
        all of them behind doors more come out of the doors than anybody does, 6./7.10.: light wrongly
        on 0.57 instead of 0.14 of 41); or people at the given places ("anywhere" or a place name),
        nobody unknown (for tests and tools)."""
        places = None if people is None else [None if w == "anywhere" else self.world.index[w] for w in people]
        self._call("reset_people", self.now, places)  # no live tracks, nothing of the sensors gathered
        self._forget()
        self.started_from = None  # the saved state the people were restored from (restore_people)

    def reset_tracks(self):
        """The sensors' own tracks start over, as after lost data (MODEL.md 4.1): their next frames are a
        start, so whoever a sensor is tracking now counts (app "Tracks zurücksetzen")."""
        for si in range(len(self.sensors)):
            self._call("reset_tracks", si)
        for rt in self.runtime.values():
            rt.last_frame = -math.inf

    def _forget(self):
        """What the shell keeps of a run, after the core started over (reset_people, restore_people)."""
        self._recycled = self.now
        self._gap_from = None  # time of a restored state: the gap to the first frame is still to predict
        self._out_cache = {}
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
        plan). The shell decodes the format, the core checks and sets it."""
        if not state or state.get("tiles") != self.tiles.fingerprint():
            return False
        try:
            unknown = {int(h["unknown"]) for h in state["hyps"]}
            objs = [(Undetected if k in unknown else Hidden).from_dict(self.tiles, d)
                    for k, d in enumerate(state["densities"])]
            hyps = [(float(h["logw"]), [int(i) for i in h["people"]], int(h["unknown"])) for h in state["hyps"]]
            t = float(state["t"])
        except (KeyError, ValueError, TypeError, IndexError, zlib.error, binascii.Error):
            return False
        if not math.isfinite(t) or any(i < 0 for _, people, u in hyps for i in [*people, u]):
            return False

        def f(a):
            return None if a is None else np.ascontiguousarray(a, dtype=float)
        dens = [(f(o.walk), f(o.still), f(o.region), float(o.out), float(o.r), type(o) is Undetected, f(o._amp))
                for o in objs]
        if self._call("restore", self.now, dens, hyps) is not None:  # what does not fit
            return False
        self._forget()
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
        # the sensors were there, only the app was not watching: what they could have tested lasts
        placed = [si for si, s in enumerate(self.config.sensors) if s.enabled and s.placed]
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
            self._call("reset_tracks", si)
            if sid in self.runtime:
                self.runtime[sid].last_frame = -math.inf  # the next frame is a start (_evidence)
            self._call("ld_reset", si)
        if changed and len(self._call("hyp_weights")):
            self._merge()
            self._prune()
            self._normalize()
        self.world.config = self.config
        self._gcache, self._ghost_total, self._out_cache = {}, {}, {}
        self.tiles.g, self.tiles.dist, self.tiles._observed = {}, {}, {}
        for si in range(len(self.sensors)):
            self._g(si, np.zeros((1, 2)))
        self._call("set_world", *self._world())  # the sensors' sight anew, what is known stays
        self._check_world()
        self._sent = None

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

    def process_frame(self, sensor_id: str, t: float, frame: presence_core.Frame):
        if self.start is None:
            self.start = self.now = self._flushed = self._recycled = t
            if self._gap_from is not None:  # restored: from the saved state's time to now
                self._predict_gap(t - self._gap_from)
                self._gap_from = None
        self._call("heard_from", sensor_id, t)  # its room is not silent (output only, roomseen.py)
        sensor = self.config.sensor_by_id.get(sensor_id)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        prev = rt.last_frame
        rt.frames += 1
        if sensor is None:
            rt.last_frame = t
            return
        si = self.sidx[sensor_id]
        self._end_silent(t)  # this sensor's own tracks too, if its last frame is too long ago
        # in the core: the detections, the sensor's own tracks (ld2450/tracks.rs), its room seen if it measured
        # somebody in it (output only, roomseen.py); kept there for the steps of the evidence
        ev = self._call("frame", si, t, frame)
        rt.detections = [Detection.of(d) for d in ev["detections"]]
        self._ld_runtime(sensor, rt, t, frame.ld2410 or {})
        for listener in self.listeners:
            listener("frame", (sensor, t, rt.detections))
        if not sensor.enabled or not sensor.placed:
            rt.last_frame = t
            return
        if t > self.now:
            # the time since this sensor's last frame counts as watched by it (MODEL.md 4.4)
            self.step(t)
        rt.last_frame = t
        gap = t - prev
        delta = gap if gap <= IDLE else 0.0  # longer: lost data, says nothing
        self._evidence(si, t, delta, ev)
        self._call("ld_frame", si)  # the LD2410C part of the frame, if it has one

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
        for si, since, live in self._call("data_lost", t):  # its next frame is a start (lost data, _evidence)
            t_end = max(since, self.now)
            self._flush(t_end, event=True)  # the end weighs and releases everybody: all at its time
            for seg in live:
                self._end(si, seg, t_end, True)
            self._merge()
            self._normalize()

    def _flush(self, t: float, event: bool = False):
        """Move everybody to time t (in the core: the people without a track all from the last
        flush, each Gaussian from its own time; in parts no longer than MAX_STEP), then the
        LD2410C's energies gathered long enough are weighed. event: what the frame brings concerns
        everybody (the core voids what it cached per hypothesis even if nobody moves)."""
        moves = t > self._flushed
        if not (moves or event):
            return
        self._send_sensors()
        self._call("event" if event else "flush", t)
        if not moves:
            return
        for si in self._c.ld_due():
            self._ld_weigh(si)

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

    # ------------------------------------------------------------ evidence

    def _evidence(self, si, t, delta, ev):
        """What the frame says (MODEL.md 4.1, 4.2), in the core step by step; the ghost map learns
        in between (docs/rust-core.md 2.0)."""
        censored = delta <= 0
        if censored or self._c.concerns_all(si):
            self._flush(t, event=True)  # these weigh, branch or merge everybody: all at t
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
        refound = self._call("track_events", si)
        ended = [seg for seg in ev["ended"] if self._c.has_track(seg)]
        for seg in ended:
            self._end(si, seg, t, ev["data_lost"])
        if ended:
            self._merge()
        # 3. nothing seen before (start, lost data): a person there now is tracked with the share of
        # the time a track lasts against the time until one is started
        if censored:
            self._call("censor", si)
        # 4. new tracks, and lost ones found again; the map at a birth includes this frame's ends
        if refound or ev["born"]:
            found = [(seg, x, y, 0.0, 0.0) for seg, x, y in refound]
            found += [(seg, x, y, self._offset_var(si, (x, y)), self._ghost_rate(si, (x, y))) for seg, x, y in ev["born"]]
            last = FRAME if censored else min(delta, FRAME)
            cats = self._call("branch_all", si, t, last, censored, [seg for seg, _, _ in ev["born"]],
                              self.ghost_map.prior_rate, found)
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
        ended = self._call("end", seg, t, data_lost)
        if ended is None:  # not a track of the filter's
            return
        z0, p_ghost, life, born = ended
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
        """The hypotheses' weights (core), summing to 1."""
        return np.array(self._call("hyp_weights"))

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

    def _ld_weigh(self, si: int):
        """The LD2410C's energies since they were last weighed (MODEL.md 4.3), in the core; the
        background (still Python) learns from b and the means from before the frames and the echo rate
        read before the echo sources moved on (docs/rust-core.md 2.0); nothing while learning is paused."""
        sid = self.sensors[si]
        b, rate = self.ld_background.b(sid), self.ld_background.rate(sid)
        out = self._call("ld_weigh", si, [float(x) for x in b], float(rate))
        if out is None or not self.learn_ghosts or self.pauses.paused(self.now - out["time"], self.now):
            return
        st = ld2410.Stats()
        st.time, st.t_e, st.t_cens, st.t_target = out["time"], out["t_e"], out["t_cens"], out["t_target"]
        quiet = self.now - out["target_t"] if sid in self.runtime else 0.0
        self.ld_background.learn(sid, b, out["mus"], out["prior_w"], st, out["alpha"], out["echo_off"], quiet)
        self.ld_background.learn_echoes(sid, out["began"], rate, out["nobody"], st.time)

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

    # -------------------------------------------------------------- outputs (MODEL.md 6; in the core)

    def _cached(self, name, make):
        """What the shell still computes of the outputs ("Ziel"), once per move of everybody (MAX_STEP)
        or event (_flush), as the core does its own."""
        key = (name, self._version)
        if self._out_cache.get(name, (None,))[0] != key:
            self._out_cache[name] = (key, make())
        return self._out_cache[name][1]

    def count_distribution(self) -> dict:
        """room id -> [P(0 people), P(1), ...] over the observed rooms, and "_observed"."""
        c = self._call("count_distribution")
        out = dict(zip(self.rooms, c))
        out["_observed"] = c[-1]
        return out

    def place_distribution(self) -> dict:
        """place name -> [P(0 people), P(1), ...], and "_house" (anywhere but outside)."""
        c = self._call("place_distribution")
        out = dict(zip(self.world.places, c))
        out["_house"] = c[-1]
        return out

    def present_count(self) -> int:
        """The most probable number of people in view."""
        return int(np.argmax(self.count_distribution()["_observed"]))

    def persons(self) -> list:
        """The known people of the most probable hypothesis that more probably exist than not
        (Garcia-Fernandez et al. 2018, sec. VI), for the display: those with a track first. Where to
        draw them: with a measuring track the mean of the mixture, without the densest tile of the most
        probable place (the core's numbers, rounded here)."""
        places = self.world.places
        out = []
        for pid, pr, lost, r, shown in self._call("persons"):
            d = {"id": pid, "places": {places[i]: round(v, 3) for i, v in enumerate(pr) if v >= 0.005}, "lost": lost,
                 "r": round(r, 3)}
            if shown is None:
                d.update({"x": None, "y": None, "vx": 0.0, "vy": 0.0, "sigma": 0.0, "walk": 0.0})
            else:
                x, y, vx, vy, sigma, walk = shown
                d.update({"x": round(x, 3), "y": round(y, 3), "vx": round(vx, 3), "vy": round(vy, 3),
                          "sigma": round(sigma, 3), "walk": round(walk, 3)})
            out.append(d)
        return out

    def _clouds(self) -> list:
        """Where the people of the display may be, for the map: with a track their Gaussians [[weight,
        x, y, sd x, sd y]] as they are; without (and their part gone through a door), [[tile, mass per
        m^2]] of the tiles holding at least 0.2 % (anywhere in a tile alike: all the model knows about
        where an unseen person is; the tiles' squares: /api/tiles)."""
        return [{"id": pid, "gauss": [[round(v, 3) for v in g] for g in gauss],
                 "tiles": [[i, round(m, 4)] for i, m in tiles]}
                for pid, gauss, tiles in self._call("clouds")]

    # ------------------------------------------------- "about to be entered" (MODEL.md 6)

    def entering(self) -> dict:
        """zone id -> {"p", "eta", "distance", "person"} for the observed rooms and the areas: the
        probability that somebody walking with a track enters the zone within the look-ahead
        (lead_time), and for the one who most probably does, the expected time and distance to it and
        their id in the display (persons(); None if not in the most probable hypothesis). In the core,
        once per move of everybody or event."""
        return {z: {"p": p, "eta": eta, "distance": dist, "person": person}
                for z, (p, dist, eta, person) in zip(self._entry_ids, self._call("entering"))}

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
        geo, dm = self.dest_geo, self.dest_map
        enter = self.entering()
        c_v = self._c.thresholds()[1]  # "wird betreten"'s
        K = len(self.rooms)
        prior = self.m.dest_prior_walks
        found, held, best = self._call("walkers")  # the people with tracks, per hypothesis those it holds
        walkers = {}  # walker -> (P(walking), room, space, f (C,), n, x, y, vx, vy)
        for i, (pw, x, y, vx, vy) in enumerate(found):
            if math.hypot(vx, vy) < destination.MIN_SPEED:
                continue
            near = geo.around(x, y)
            if near is None:
                continue
            room = int(geo.cell_room[near[0][0]])
            cnt = dm.lookup(*near, vx, vy)
            n = float(cnt.sum())
            f = cnt / n if n > 0 else np.zeros(geo.C)
            walkers[i] = (pw, room, int(geo.space[room]), f, n, x, y, vx, vy)
        hw = self.hyp_weights() if held else []
        q_map = np.zeros(K)
        num, den = np.zeros(K), np.zeros(K)
        share = {}
        for wh, mine in zip(hw, held):
            empty = np.ones(K)
            for i, _ in mine:
                wk = walkers.get(i)
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
                share.setdefault(i, np.zeros(K))
                share[i] += wh * pw * rel * (f[:K] + 1e-3)
            q_map += wh * (1.0 - empty)
        display = {i: k + 1 for i, k in held[best]} if held else {}
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
        """Observed room id -> (occupied, source), MODEL.md 6 "Belegt" (in the core): P(somebody there)
        above the threshold c = light_cost / (light_cost + 1), or the room's own LD2450 measured
        somebody in it within the last seen_hold s (roomseen.py). source: "filter", "ld2450", "beide"
        (both), None if not occupied. Only an output: reads the filter, changes nothing in it."""
        sources = self._call("occupancy", self.now)
        return {z: (s is not None, s) for z, s in zip(self.rooms, sources)}

    def zone_states(self) -> dict:
        """Per zone: the most probable number of people (observed rooms: from all hypotheses),
        moving / still from the most probable hypothesis' people, "about to be entered" from all
        (entering()). Occupied, where the probability is known: P(somebody there) above the
        threshold that minimizes the expected cost of the light; about to be entered likewise,
        with the cost of a light switched on in vain against that of entering in the dark
        (MODEL.md 6). An observed room is also occupied while its own LD2450 measures somebody in it
        (occupancy()); its count is then at least 1, and the house's too. A room whose own sensors in
        use all went silent (Tracker.seen.silent) is not available: no data, Home Assistant gets
        "unavailable" instead of its state (ha.py); the state itself is computed as always. The numbers
        are the core's (zone_numbers); here they become ZoneStates, with "Ziel" (until it is in Rust)."""
        from .zones import ZoneState
        zones, (count, moving, still) = self._call("zone_numbers", self.now, self.start)
        c_enter = self._c.thresholds()[1]
        states = {}
        for zid, n, prob, decided, mov, sti, enter, approaching, available in zones:
            st = ZoneState(count=n, moving=mov, still=sti, probability=prob, available=available)
            if decided is not None:
                st.decided, st.source = decided
            if enter is not None:
                st.p_enter, st.distance, st.eta, st.person = enter
                st.approaching, st.c_enter = approaching, c_enter
            states[zid] = st
        # the rooms first, then the areas (as Home Assistant and the display have them)
        kind = {z.id: z.kind for z in self.config.zones}
        states = {z: states[z] for z in sorted(states, key=lambda z: kind[z] != "room")}
        for zid, e in self.targets().items():
            st = states.get(zid)
            if st is None:
                continue
            st.target = e["on"]
            st.c_target = self.threshold(zid)
            st.p_target, st.target_weight, st.target_walks = e["p"], e["weight"], e["walks"]
            st.target_from, st.target_distance, st.target_eta, st.target_person = e["from"], e["distance"], e["eta"], e["person"]
        states["_total"] = ZoneState(count=count, moving=moving, still=still)
        return states

    def snapshot(self) -> dict:
        """For the web UI: the people of the most probable hypothesis at their most probable place,
        their densities as heat maps, the sensors' raw data, the places without a sensor."""
        t = self.now
        tracks = []
        for d in self.persons():
            room = None if d["x"] is None else next(
                (z.id for z in self.config.zones_of("room") if z.contains(d["x"], d["y"])), None)
            tracks.append({**d, "room": room})
        clouds = self._clouds()
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
