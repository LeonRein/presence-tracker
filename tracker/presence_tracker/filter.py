"""The Bayes filter of MODEL.md: everybody's whereabouts given the LD2450 tracks and the LD2410C energies (4.3). Deterministic:
nothing is drawn, the same data always give the same result.

Hypotheses (MODEL.md 5.1): which live tracks are one person's and which are ghosts, with exact
weights. A new track (or one found again) branches every hypothesis; hypotheses that come to say the
same about the live tracks are merged (5.6). This is the data association of a PMBM filter
(Garcia-Fernandez et al. 2018).

Given a hypothesis, the people are independent:
  - a person with a live track (measured or held): a Gaussian mixture over standing / walking (5.2)
  - a person known from earlier tracks, without one now: a density over the tiles (5.3)
  - the people nobody knows of: a Poisson intensity over the tiles (3.4, 5.5)
The number of people is part of the state: people become known by their tracks, and one who is
almost surely out of the house is given back to the unknown ones.

All of it - the world built from the configuration, the filter, what it learns, the saved formats - is
the Rust core (presence_core, docs/rust-core.md). This is its shell: it hands the core the configuration,
frames, time and pauses, calls the listeners with the core's events and forms the outputs.
"""

import dataclasses
import json
import logging
import math

import numpy as np

from .frames import Detection, SensorRuntime
from .pause import Pauses
from .zones import ZoneState

import presence_core


log = logging.getLogger(__name__)


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


class _Guarded:
    """The Rust core (presence_core.Core) with every getter and method through _core: no call into it
    can let a panic through (the app's housekeeping would end)."""

    __slots__ = ("core",)

    def __init__(self, core):
        self.core = core

    def __getattr__(self, name):
        value = _core(getattr, self.core, name)
        if callable(value):
            return lambda *args: _core(value, *args)
        return value


__all__ = ["Tracker", "plan"]

_ZONE_FIELDS = [f.name for f in dataclasses.fields(ZoneState)]


def plan(config) -> dict:
    """The core's plan of a configuration (MODEL.md 2), without building a world: "observed" (room ids),
    "places" (observed, the regions' ids, outside), "areas", "own_rooms" (per sensor, None: none), "regions"
    (id -> {"name", "rooms", "open", "exits"}), "closed" (the rooms in which the sensors see nobody of the
    home: those of closed regions and the outside rooms)."""
    return presence_core.plan(config.model_json())


class _Seen:
    """The rooms' own LD2450s (MODEL.md 6 "Belegt": the room's own sensor measured somebody in it within
    the last seen_hold s) as the core keeps them, for reading. Silent after the model's silent_after s."""

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

    def silent(self, now: float) -> set:
        """The rooms whose own sensors in use all sent no frame for more than silent_after s, counted
        from the model's start for one that sent nothing (not started: nothing silent)."""
        return {r for r, q in zip(self._tr.rooms, self._tr._c.silent(now)) if q}

    def online(self, sensor_id: str, now: float) -> bool:
        """The sensor sent a frame lately (the web UI's "online" too)."""
        return self._tr._c.online(sensor_id, now)


class Tracker:
    def __init__(self, config, start: float | None = None, people=None, model: dict | None = None):
        """model: the numbers of the model that differ from the core's (presence_core.model_defaults();
        infinite ones as floats). Nothing here is random."""
        self.model = dict(model or {})
        self.config = config
        self.p = config.params
        self.listeners = []
        self.runtime = {}
        self._c = None  # the Rust core (presence_core.Core, _Guarded): the filter, its world and what it learns
        # Lernen pausieren (pause.py): in these intervals nothing learns (the ghost map, MODEL.md 4.2; the
        # LD2410C background and echo rate, 4.3; the destination map, 6 "Ziel"); the tracking goes on
        self.pauses = Pauses()
        self.seen = _Seen(self)  # the rooms' own LD2450 (output only, MODEL.md 6 "Belegt"; in the core)
        self._build()
        if start is not None:
            self._c.start_at(float(start))
        self.reset_people(people)

    # --------------------------------------------------------------- setup

    def _build(self, world=None):
        """A new core in a new world (built of the configuration and model; or this one, presence_core.World,
        if it was built already)."""
        old = self._c
        self._c = _Guarded(_core(presence_core.Core, world if world is not None else self._new_world()))
        self._names()
        if old is not None:  # what the rooms' own LD2450s saw, the ids of new tracks, the time and evidence,
            self._c.adopt(old.core)  # what was learned and the pauses, what the LD2410Cs gathered go on

    def _names(self):
        """The names of the core's plan and its tiles (they change only with the world)."""
        self.sensors = [s.id for s in self.config.sensors]
        self.sidx = {sid: k for k, sid in enumerate(self.sensors)}
        self.plan = self._c.plan()  # as plan(self.config)
        self.rooms, self.places = self.plan["observed"], self.plan["places"]
        self._own = {sid: r for sid, r in zip(self.sensors, self.plan["own_rooms"]) if r is not None}
        self._tiles = self._c.tiles_view()

    def _new_world(self):
        """The core's world of the configuration and the model (docs/rust-core.md 1; their JSON, infinite
        numbers of the model as "inf"), built once (presence_core.World)."""
        model = {k: (str(v) if isinstance(v, float) and not math.isfinite(v) else v) for k, v in self.model.items()}
        return _core(presence_core.World, self.config.model_json(), json.dumps(model))

    @property
    def loglik(self) -> float:
        """log evidence of everything seen so far"""
        return self._c.loglik[0]

    @property
    def loglik_cut(self) -> float:
        """... the part of it that is mass of hypotheses dropped (MODEL.md 5.1)"""
        return self._c.loglik[1]

    def reset_people(self, people=None):
        """Start over (in the core): nothing known about the people (MODEL.md 5.3: no known people, the
        unknown ones anywhere, start_unknown of them expected - no fixed number: the data make known
        people of them, through the tracks there at the start (4.1) and the ways in; without any in
        view, whoever sits in view at the start can only be a ghost, the desk at 22:19 and 08:06; with
        all of them behind doors more come out of the doors than anybody does, 6./7.10.: light wrongly
        on 0.57 instead of 0.14 of 41); or people at the given places ("anywhere" or a place name),
        nobody unknown (for tests and tools)."""
        index = {name: i for i, name in enumerate(self.places)}
        places = None if people is None else [None if w == "anywhere" else index[w] for w in people]
        self._c.reset_people(places)  # no live tracks, nothing of the sensors gathered
        self.started_from = None  # the saved state the people were restored from (restore_people)

    def reset_tracks(self):
        """The sensors' own tracks start over, as after lost data (MODEL.md 4.1): their next frames are a
        start, so whoever a sensor is tracking now counts (app "Tracks zurücksetzen")."""
        self._c.reset_tracks()

    def people_state(self) -> dict:
        """What is known about the people, to start from after a restart (MODEL.md 5.3): per
        hypothesis its weight, its known people and the unknown ones, as densities over the tiles.
        Live tracks do not survive a restart (the sensors' next frames are a start, 4.1): the
        people with tracks go to the tiles as when their last track ends (5.4), ghost tracks end,
        and the hypotheses that then say the same are merged (5.6) - one per number of known
        people (in the core, on copies). The time is that of the last move of everybody; the format
        is the core's (filter/saved.rs)."""
        return json.loads(self._c.people_state())

    def restore_people(self, state: dict | None) -> bool:
        """Start from a saved state (people_state): the posterior then is the prior now (the PMBM
        recursion goes on from it, B_GarciaFernandez2018), moved on over the time between (a gap in
        the data says nothing, MODEL.md 4.4) - at the first frame if the model has not started yet.
        False, and nothing changed, if there is none or it does not fit the tiles (another floor
        plan) or cannot be read (logged)."""
        if not state:
            return False
        try:
            text = json.dumps(state, allow_nan=False)
        except (TypeError, ValueError):
            return False
        why = self._c.restore_people(text)
        if why is not None:
            log.info("the saved people are not taken: %s", why)
            return False
        self.started_from = state  # moved on over the time since t in the core (at the first frame if not started)
        return True

    def reconfigure(self, config):
        """A new configuration. If only sensors changed (turned, moved, recalibrated, switched on
        or off) and the world the people live in stays the same, what is known about the people
        stays: a recalibration tells something about a sensor, nothing about them. The changed
        sensors start over as after lost data (MODEL.md 4.1, 5.3): their tracks end without
        information, their next frame is a start. Otherwise (walls, rooms, doors, parameters, the
        set of sensors, the observed area) the model starts over as after a restart: from what it
        knew (people_state) if the tiles stay the same, else from nothing known. Which way, the core
        decides (presence_core.changes). Returns whether it started over."""
        self.reconfigured_from = None
        self._sync_pauses()  # the ends of the changed sensors' tracks learn, or not, as at any time
        way, changed = presence_core.changes(self.config.model_json(), config.model_json())
        self.config, self.p = config, config.params
        # what was learned follows (in the core): recalibrated sensors keep their parts, a remounted one
        # starts over; the destination map holds while the rooms and doors are the same
        if way == "outputs":
            self._c.set_outputs(config.model_json())  # only what the outputs read: nothing built
            return False
        world = None
        if way == "sensors":
            world = self._new_world()
            try:  # the people stay if they live in the new world too (the core checks first, then changes)
                self._tell(self._c.recalibrate([self.sidx[sid] for sid in changed], world))
                self._names()
                return False
            except presence_core.WorldChanged:  # the sensors changed which rooms are observed: the world is
                pass                            # whole, the model starts over in it
        state = self.people_state()
        # if what follows fails half-way (new tiles, old people), the app saves this (app.h_put_config)
        self.reconfigured_from = state
        self._build(world)  # its sensors' tracks start anew: their next frames are a start
        if not self.restore_people(state):
            self.reset_people()
        return True

    def use_ghost_map(self, gm) -> bool:
        """Use a learned ghost map (MODEL.md 4.2; its dict, as learned()["ghost_map"]) if it was learned
        with the prior the model has now; else (also None) start a new one from the prior. Each sensor's
        part is in its own frame: it follows a recalibrated sensor, and only a sensor mounted elsewhere
        starts its part over. Returns whether the learned map is used; ValueError if it cannot be read."""
        return self._c.use_ghost_map(None if gm is None else json.dumps(gm))

    def use_dest_map(self, dm) -> bool:
        """Use a learned destination map (MODEL.md 6 "Ziel"; its dict) if it was learned with these rooms,
        doors and regions; else (also None) start an empty one. ValueError if it cannot be read."""
        return self._c.use_dest_map(None if dm is None else json.dumps(dm))

    def use_ld_background(self, bg) -> list:
        """Use a learned LD2410C background (MODEL.md 4.3; its dict, None: nothing learned), per sensor only
        where it was learned with the sensor where it is now. Returns why sensors' parts were dropped (a
        part out of range is dropped alone, the others are used); ValueError if it is no background."""
        return self._c.use_ld_background(None if bg is None else json.dumps(bg))

    def hold_background(self, seconds: float):
        """The LD2410Cs learn nothing from their next `seconds` s of frames (after "Tracks zurücksetzen":
        whom the filter does not see yet would be learned as background)."""
        self._c.hold_background(float(seconds))

    def background(self, sensor_id: str) -> tuple:
        """The LD2410C background of this sensor (16 cells, MODEL.md 4.3) and its echo sources' rate per
        s, as the next weighing reads them."""
        b, rate = self._c.background(self.sidx[sensor_id])
        return np.array(b), rate

    def ghost_rates(self, sensor_id: str, pts) -> tuple:
        """The ghost map's rate of ghost births per m^2 and s at the points (n, 2) for this sensor, and
        the s it watched (the web UI)."""
        rates, watched = self._c.ghost_rates(self.sidx[sensor_id], [tuple(map(float, p)) for p in pts])
        return np.array(rates), watched

    # ------------------------------------------------------------- frames

    def process_frame(self, sensor_id: str, t: float, frame: presence_core.Frame):
        """A frame of a sensor at t (in the core: the model starts at the first; a sensor in use moves
        everybody on to t, then its frame is evidence, MODEL.md 4; the ghost map, the LD2410C background
        and the destination map learn where they always did, docs/rust-core.md 2.0)."""
        self._sync_pauses()
        events = self._c.frame(sensor_id, t, frame)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        rt.frames += 1
        sensor = self.config.sensor_by_id.get(sensor_id)
        if sensor is not None:
            rt.detections = [Detection(np.array(d.pos), tuple(d.local), d.speed, d.hidden, d.stale)
                             for d in self._c.detections(self.sidx[sensor_id])]
            self._ld_runtime(rt, frame.ld2410 or {})
            for listener in self.listeners:
                listener("frame", (sensor, t, rt.detections))
        self._tell(events)

    def _tell(self, events):
        """The core's events to the listeners: ("watch", (sensor id, dt)), ("track_end", (sensor id, z0,
        P(ghost), life, data lost)), ("branch", (sensor id, seg, z, found again, [(logw, category)]))."""
        if not self.listeners:
            return
        for kind, si, *rest in events:
            sid = self.sensors[si]
            if kind == "watch":
                payload = (sid, rest[0])
            elif kind == "track_end":
                z0, p_ghost, life, data_lost = rest
                payload = (sid, np.array(z0), p_ghost, life, data_lost)
            else:
                seg, z, refind, cats = rest
                payload = (sid, seg, np.array(z), refind, cats)
            for listener in self.listeners:
                listener(kind, payload)

    @property
    def pauses(self):
        """Lernen pausieren (pause.py): in these intervals nothing learns; the tracking goes on. The app
        keeps changing its object: the core gets it again whenever it changed."""
        return self._pauses

    @pauses.setter
    def pauses(self, pauses):
        self._pauses = pauses
        self._pauses_sent = None

    def paused(self, t0: float, t1: float | None = None) -> bool:
        """Is learning paused at any time of [t0, t1] (t1 None: the moment t0; the core's pauses: the
        calibration data are not kept then)?"""
        self._sync_pauses()
        return self._c.paused(t0, t1)

    def _sync_pauses(self):
        p = self._pauses
        if self._pauses_sent != (id(p), p.changes):
            self._c.set_pauses([(s, e) for s, e in p.intervals + p.known])
            self._pauses_sent = (id(p), p.changes)

    @property
    def now(self) -> float:
        """The model's time (the last frame's or step's)."""
        return self._c.now

    @property
    def start(self) -> float | None:
        """When the model started (its first frame; None: not yet)."""
        return self._c.start

    def step(self, t: float):
        """Time goes on to t (MODEL.md 3; in the core): everybody is moved and weighed by the tracks not
        started meanwhile together, in parts of at most max_step s; who almost surely does not exist is
        given back; the walks are counted into the destination map."""
        self._sync_pauses()
        self._tell(self._c.step(t))

    # ------------------------------------------------------------- reading the filter

    def hyp_weights(self) -> np.ndarray:
        """The hypotheses' weights (core), summing to 1."""
        return np.array(self._c.hyp_weights())

    def hypotheses(self) -> list:
        """The hypotheses for reading (tests, tools): per hypothesis {"logw", "key", "known",
        "unknown_out"}. key: what it says about the live tracks - the track sets of its people with
        tracks and its ghost tracks (hypotheses differing only in the people without a track are one,
        MODEL.md 5.6); known: per known person how probably they exist (1 with a track); unknown_out:
        the mass of the unknown ones outside."""
        return [{"logw": h.logw, "key": (frozenset(frozenset(p) for p in h.people), frozenset(h.ghosts)),
                 "known": h.known, "unknown_out": h.unknown_out}
                for h in self._c.hypotheses()]

    def tracks(self) -> dict:
        """The filter's live tracks: track id -> sensor id."""
        return {seg: self.sensors[si] for seg, si in self._c.tracks()}

    def check(self):
        """The bookkeeping's invariants (in the core: the hypotheses and the live tracks, the holders,
        the pools, the parts gone through a door); ValueError with what is wrong."""
        self._c.check()

    def tiles(self) -> dict:
        """The tiles (MODEL.md 5.3) for drawing where unseen people may be: "id" (their fingerprint: a
        saved state fits only tiles of the same), the side of a square, per tile the lower left corner
        of its square, its room (id; None: in none), which cuts the square, and its centre."""
        t = self._tiles
        return {"id": t.fingerprint, "size": t.size, "squares": t.squares,
                "room": [None if r is None else self.rooms[r] for r in t.rooms], "centers": t.centers}

    def coverage(self) -> dict:
        """The coverage of the running world, that is of the saved configuration (the core's: per tile how
        many sensors see it, per sensor in use its sight per tile, per room of the home its area, the share
        seen, m^2 blind and seen once)."""
        return json.loads(self._c.coverage())

    def tile_near(self, xy) -> int | None:
        """The tile of a point, or of the cell in view nearest to it (None: no tiles)."""
        return self._c.tile_near(tuple(map(float, xy)))

    def sight(self, sensor_id: str, pts) -> np.ndarray:
        """How well the sensor sees a person at the points (n, 2), 0..1 (MODEL.md 4.1)."""
        return np.array(self._c.sight(self.sidx[sensor_id], [tuple(map(float, p)) for p in pts]))

    def observed(self) -> np.ndarray:
        """How well the best sensor can test a person in each tile, 0..1 (MODEL.md 5.5)."""
        return np.array(self._c.observed())

    def sensor_map(self, sensor_id: str) -> dict:
        """For the web UI: what one sensor sees from the geometry (its coarse map, MODEL.md 4.1) and the
        ghost map's rate (ghost tracks per m^2 and hour; None where the sensor doesn't see), the hours
        it watched (the core's, in columns of rows here)."""
        if sensor_id not in self.sidx:
            return {}
        m = self._c.sensor_map(self.sidx[sensor_id])
        (x0, y0), rows = m.corner, m.rows
        columns = [range(i * rows, (i + 1) * rows) for i in range(m.cols)]
        return {"cell": m.cell, "x0": x0, "y0": y0, "cols": m.cols, "rows": rows,
                # [column][row], row 0 = lowest y
                "prior": [[round(float(m.prior[k]), 3) for k in col] for col in columns],
                "ghosts": [[None if m.ghosts[k] is None else round(m.ghosts[k], 4) for k in col] for col in columns],
                "watched_h": round(m.watched_h, 1)}

    def room_at(self, pts) -> list:
        """Per point (x, y) the observed room it is in (id; None: in none). For the tools."""
        return self._c.room_at([tuple(map(float, p)) for p in pts])

    # -------------------------------------------------------------- LD2410C

    def _ld_runtime(self, rt, ld):
        """The LD2410C's gates as sent, for the display (its presence and distance: the core's, ld_shown)."""
        rt.move_gates = ld.get("move_gates")
        rt.still_gates = ld.get("still_gates")

    # -------------------------------------------------------------- outputs (MODEL.md 6; in the core)

    def count_distribution(self) -> dict:
        """room id -> [P(0 people), P(1), ...] over the observed rooms, and "_observed"."""
        c = self._c.count_distribution()
        out = dict(zip(self.rooms, c))
        out["_observed"] = c[-1]
        return out

    def place_distribution(self) -> dict:
        """place name -> [P(0 people), P(1), ...], and "_house" (anywhere but outside)."""
        c = self._c.place_distribution()
        out = dict(zip(self.places, c))
        out["_house"] = c[-1]
        return out

    def persons(self) -> list:
        """The known people of the most probable hypothesis that more probably exist than not
        (Garcia-Fernandez et al. 2018, sec. VI), for the display: those with a track first. Where to
        draw them: with a measuring track the mean of the mixture, without the densest tile of the most
        probable place, and the room there; their most probable place, whether they move (the core's,
        rounded here)."""
        places = self.places
        out = []
        for p in self._c.persons():
            d = {"id": p.id, "places": {places[i]: round(v, 3) for i, v in enumerate(p.places) if v >= 0.005},
                 "place": places[p.place], "lost": p.lost, "r": round(p.r, 3), "room": p.room,
                 "moving": p.moving}
            s = p.shown
            if s is None:
                d.update({"x": None, "y": None, "vx": 0.0, "vy": 0.0, "sigma": 0.0, "walk": 0.0})
            else:
                (x, y), (vx, vy) = s.at, s.velocity
                d.update({"x": round(x, 3), "y": round(y, 3), "vx": round(vx, 3), "vy": round(vy, 3),
                          "sigma": round(s.sigma, 3), "walk": round(s.walk, 3)})
            out.append(d)
        return out

    def _clouds(self) -> list:
        """Where the people of the display may be, for the map: with a track their Gaussians [[weight,
        x, y, sd x, sd y]] as they are and per Gaussian the room its mean is in (id, None: in none); without
        (and their part gone through a door), [[tile, mass per m^2]] of the tiles holding at least 0.2 %
        (anywhere in a tile alike: all the model knows about where an unseen person is; the tiles'
        squares: /api/tiles). How it is drawn is the web UI's."""
        return [{"id": c.id, "gauss": [[round(v, 3) for v in g] for g in c.gauss],
                 "rooms": c.rooms, "tiles": [[i, round(m, 4)] for i, m in c.tiles]}
                for c in self._c.clouds()]

    def occupancy(self) -> dict:
        """Observed room id -> (occupied, source), MODEL.md 6 "Belegt" (in the core): P(somebody there)
        above the threshold c = light_cost / (light_cost + 1), or the room's own LD2450 measured
        somebody in it within the last seen_hold s. source: "filter", "ld2450", "beide" (both), None if
        not occupied. Only an output: reads the filter, changes nothing in it."""
        sources = self._c.occupancy(self.now)
        return {z: (s is not None, s) for z, s in zip(self.rooms, sources)}

    def zone_states(self) -> dict:
        """Per zone its state (MODEL.md 6; the core's, ZoneState field for field): the most probable number of
        people (observed rooms: from all hypotheses), moving / still from the most probable hypothesis'
        people, "about to be entered" and "Ziel" with their thresholds. Occupied, where the probability is
        known: P(somebody there) above the threshold that minimizes the expected cost of the light, or the
        observed room's own LD2450 measured somebody in it (its count then at least 1, and the house's too).
        A room whose own sensors in use all went silent is not available: Home Assistant gets
        "unavailable" instead of its state (ha.py). The rooms of the home first, then its other zones (as
        Home Assistant and the display have them), last the house ("_total")."""
        return {z.id: ZoneState(**{f: getattr(z, f) for f in _ZONE_FIELDS}) for z in self._c.zone_states(self.now)}

    def snapshot(self) -> dict:
        """For the web UI: the people of the most probable hypothesis at their most probable place (and the
        room there), their densities as heat maps, the sensors' raw data, the places without a sensor."""
        t = self.now
        tracks = self.persons()
        clouds = self._clouds()
        count, moving, still = self._c.observed_total()
        sensors = {}
        for sid, rt in self.runtime.items():
            known = sid in self.sidx
            last = self._c.last_frame(self.sidx[sid]) if known else -math.inf
            present, distance = self._c.ld_shown(self.sidx[sid]) if known else (False, 0.0)
            sensors[sid] = {
                "online": self.seen.online(sid, t),  # as Home Assistant's rooms
                "detections": [{"x": round(float(d.pos[0]), 3), "y": round(float(d.pos[1]), 3),
                                "lx": round(d.local[0], 3), "ly": round(d.local[1], 3),
                                "speed": round(d.speed, 2), "hidden": d.hidden or d.stale}
                               for d in rt.detections] if t - last < 1.0 else [],
                "ld2410": {"present": present, "distance": round(distance, 2),
                           "move_gates": rt.move_gates, "still_gates": rt.still_gates},
            }
        # how long people stay in a region (MODEL.md 3.3): log-normal, the same for every region
        median, p90 = self._c.dwell
        dwell = {"median": round(median), "p90": round(p90)}
        regions = {}
        for r in self._c.regions():  # the regions without a sensor, as the core has them
            plan = self.plan["regions"][r.id]
            regions[r.id] = {"name": plan["name"], "rooms": plan["rooms"], "open": plan["open"], "count": r.count,
                             "probabilities": [round(r.p_any, 3)] if r.p_any >= 0.005 else [], "dwell": dwell}
        return {"t": t, "tracks": tracks, "clouds": clouds, "tiling": self._tiles.fingerprint, "sensors": sensors,
                "regions": regions, "seen": {"count": count, "moving": moving, "still": still}}

    # ------------------------------------------------------------- learned

    def learned(self) -> dict:
        """What was learned in its saved formats: {"ghost_map", "ld_background", "dest_map"}."""
        return json.loads(self.learned_snapshot().json())

    def learned_snapshot(self):
        """A copy of what was learned now; its json() packs it without the tracker (the app: in a thread)."""
        return self._c.learned()

    def load_learned(self, data: dict):
        """Take over what was learned (learned()), each part present: the ghost map if learned with the
        prior the model has now, the destination map if with these rooms and doors (ValueError if one
        cannot be read)."""
        if data.get("ghost_map"):  # per sensor unless mounted elsewhere since (offline: tools/ghostmap.py, an old tree)
            self.use_ghost_map(data["ghost_map"])
        if data.get("ld_background"):
            for why in self.use_ld_background(data["ld_background"]):
                log.warning("LD2410C background: a part dropped: %s", why)
        if data.get("dest_map"):
            self.use_dest_map(data["dest_map"])
