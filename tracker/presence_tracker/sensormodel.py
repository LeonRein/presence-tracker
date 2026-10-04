"""What each sensor can see, how reliably, and where it sees ghosts.

Three maps per sensor on a fixed grid (CELL m, anchored at ORIGIN so stored data stays valid
when rooms are edited):

  prior P_D   detection probability from the geometry: 0 behind walls and in closed rooms
              without a sensor, falling toward the edge of the field of view and the maximum
              range
  learned P_D how often the sensor actually reports a person that is there for sure (seen by
              another sensor at the same moment, or tracked with an origin for a while)
  clutter     how often the sensor reports a target where nobody is: only counted where another
              online sensor sees the spot well, reports nothing there, and no known person is
              near. Never from the tracker's own verdicts: someone who always sits where only
              one sensor sees would otherwise be learned away as a ghost.

Plus the measurement error by distance, from the offsets between two sensors that see the same
person at the same time; how long the LD2450 drops a person it saw (re-detection survival, per
walking/still); and for the LD2410C, per sensor and 0.75 m gate, the energy it shows with a
person in that gate versus with nobody near it.

The tracker uses pd_effective and clutter_density for the existence probability of new tracks.
Counts fade with a half-life of HALF_LIFE, so the maps follow changes (furniture, a moved sensor).
"""

import json
import math
import pathlib

import numpy as np

CELL = 0.25  # m
ORIGIN = (-30.0, -30.0)  # grid anchor
SIZE = 240  # cells per side: 60 m
HALF_LIFE = 7 * 24 * 3600.0  # s
PD_MAX = 0.95
EDGE_ANGLE = 15.0  # degrees inside the field-of-view edge where P_D starts to fall
EDGE_RANGE = 1.5  # m before the maximum range where P_D starts to fall
PD_AT_EDGE = 0.4
SURE_AGE = 30.0  # s: a track with an origin that old counts as a person for sure
MAX_ACCURACY_SAMPLES = 20000
GATE = 0.75  # m, LD2410C distance gate
GATES = 9
BINS = 10  # energy 0-100 in steps of 10
# Re-detection survival S(tau): share of LD2450 dropouts of a person who stays put that last
# longer than tau (measured 2026-10-03, both sensors). Gaps shorter than 0.3 s don't count.
GAP_TAUS = (0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 60.0, 120.0)
GAP_PRIOR = {
    "still": (1.0, 0.42, 0.30, 0.26, 0.24, 0.21, 0.18, 0.13, 0.09, 0.03, 0.01),
    "walking": (1.0, 0.74, 0.67, 0.66, 0.64, 0.58, 0.54, 0.39, 0.20, 0.06, 0.02),
}
GAP_PRIOR_WEIGHT = 200  # the prior counts like this many observed gaps
GAP_FLOOR = 1.5  # s: dropouts are counted from here (the tracker's lost_after)
OFFSET_CELL = 0.5  # m, grid of the learned systematic errors of the sensors
OFFSET_PRIOR_N = 10  # measurement pairs: a place's systematic error is believed after about this many


def _offset_cell(pos) -> str:
    return f"{math.floor(float(pos[0]) / OFFSET_CELL)},{math.floor(float(pos[1]) / OFFSET_CELL)}"


def _prior_survival(mode: str, tau: float) -> float:
    prior, taus = GAP_PRIOR[mode], GAP_TAUS
    if tau <= taus[0]:
        return 1.0
    if tau >= taus[-1]:
        return prior[-1] * taus[-1] / tau
    k = max(i for i in range(len(taus)) if taus[i] <= tau)
    f = (math.log(tau) - math.log(taus[k])) / (math.log(taus[k + 1]) - math.log(taus[k]))
    return prior[k] * (prior[k + 1] / prior[k]) ** f
# LD2450 resolution: P(both of two people at distance d get a target | at least one does), by
# distance bin (upper edges), measured 2026-10-03 on pairs of confirmed tracks (both in view).
# One person gets two targets far more rarely (SPLIT_RATE per frame, measured <= 0.0013).
RES_EDGES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, math.inf)
RES_PRIOR = (0.0, 0.02, 0.15, 0.41, 0.76, 0.80, 0.69, 0.59, 0.68)
RES_PRIOR_WEIGHT = 500  # frames
SPLIT_RATE = 0.002
MAX_GAPS = 2000
# LD2410C energy histograms, prior counts per bin (0-10, ..., 90-100): with a person in the gate,
# and with nobody within one gate of it (measured 2026-10-03). Gates beyond 4.5 m: flat, i.e. no
# evidence until learned.
LD_OCC_PRIOR = (1, 2, 3, 3, 3, 3, 3, 3, 3, 3)  # a person sitting dead still shows 20-50 too
LD_EMP_PRIOR = (6, 6, 3, 1.5, 1, 0.5, 0.3, 0.2, 0.1, 0.1)
LD_RATIO_RANGE = (0.2, 10.0)  # one gate energy is weak evidence either way
LD_HISTORY = 120.0  # s of gate energies kept per sensor, labeled "occupied" retroactively when a
                    # dropped person is seen again at their place: without that, only people the
                    # LD2450 sees (i.e. moving a little, high energy) would ever be learned
LD_FLAT_PRIOR = (1,) * BINS
LD_GOOD_GATES = range(2, 6)


def cell_of(x: float, y: float) -> tuple:
    return int((x - ORIGIN[0]) / CELL), int((y - ORIGIN[1]) / CELL)


def cell_center(i: int, j: int) -> tuple:
    return ORIGIN[0] + (i + 0.5) * CELL, ORIGIN[1] + (j + 0.5) * CELL


class SensorModel:
    def __init__(self, config):
        self.config = config
        self.trials = {}  # sensor -> SIZE x SIZE array (fading counts)
        self.hits = {}
        self.exposure = {}
        self.clutter = {}
        self.accuracy = []  # [range_a, range_b, offset] of simultaneous detections of one person
        self.gaps = {"still": [], "walking": []}  # learned LD2450 dropout durations of people who stayed
        self.offsets = {}  # sensor -> "i,j" (OFFSET_CELL grid) -> [sum dx, sum dy, n]: its systematic error there
        self.pairs = [[0.0, 0.0] for _ in RES_EDGES]  # per distance bin: [both detected, one detected] frames
        self.ld_occ = {}  # sensor -> GATES x BINS counts of LD2410C energy with a person sitting in the gate
        self.ld_move = {}  # ... with a person moving in the gate (a different population: higher)
        self.ld_emp = {}  # ... with nobody within two gates
        self.ld_history = {}  # sensor -> [(t, energies)] of the last LD_HISTORY seconds
        self.last_decay = None
        self.changed = False
        self.rebuild()

    # ------------------------------------------------------------ geometry

    def rebuild(self):
        """Prior P_D over the house's bounding box, after a config change."""
        rooms = self.config.zones_of("room")
        self.prior = {}
        if not rooms:
            self.box = (0, 0, 0, 0)
            return
        xs = [p[0] for z in rooms for p in z.geometry.outline(8)]
        ys = [p[1] for z in rooms for p in z.geometry.outline(8)]
        i0, j0 = cell_of(min(xs) - 0.5, min(ys) - 0.5)
        i1, j1 = cell_of(max(xs) + 0.5, max(ys) + 0.5)
        self.box = (i0, j0, i1 + 1, j1 + 1)
        for s in self.config.sensors:
            grid = np.zeros((SIZE, SIZE), dtype=np.float32)
            if s.placed and s.enabled:
                for i in range(i0, i1 + 1):
                    for j in range(j0, j1 + 1):
                        grid[i, j] = self.prior_pd(s, *cell_center(i, j))
            self.prior[s.id] = grid
            for store in (self.trials, self.hits, self.exposure, self.clutter):
                store.setdefault(s.id, np.zeros((SIZE, SIZE), dtype=np.float32))
            for store in (self.ld_occ, self.ld_move, self.ld_emp):
                store.setdefault(s.id, np.zeros((GATES, BINS), dtype=np.float32))

    def prior_pd(self, s, x: float, y: float) -> float:
        if any(z.contains(x, y) for z in self.config.closed_rooms):
            return 0.0
        lx, ly = s.to_local(x, y)
        r = math.hypot(lx, ly)
        if ly <= 0 or r > s.range or not s.sees(x, y, self.config.wall_segments):
            return 0.0
        az = abs(math.degrees(math.atan2(lx, ly)))
        if az > s.fov / 2:
            return 0.0

        def taper(value, start, end):
            if value <= start:
                return 1.0
            return 1.0 - (1.0 - PD_AT_EDGE) * min((value - start) / max(end - start, 1e-6), 1.0)
        return PD_MAX * taper(az, s.fov / 2 - EDGE_ANGLE, s.fov / 2) * taper(r, s.range - EDGE_RANGE, s.range)

    def pd(self, sensor_id: str, x: float, y: float) -> float:
        """Prior detection probability (geometry): from the map, or computed outside of it."""
        i, j = cell_of(x, y)
        i0, j0, i1, j1 = self.box
        grid = self.prior.get(sensor_id)
        if grid is not None and i0 <= i < i1 and j0 <= j < j1:
            return float(grid[i, j])
        s = self.config.sensor_by_id.get(sensor_id)
        return self.prior_pd(s, x, y) if s is not None and s.placed and s.enabled else 0.0

    def pd_effective(self, sensor_id: str, x: float, y: float, weight: float = 20.0) -> float:
        """Detection probability: the learned rate, pulled toward the prior while few trials exist
        (the prior counts like `weight` trials). 0 where the geometry says the sensor can't see."""
        prior = self.pd(sensor_id, x, y)
        if prior <= 0:
            return 0.0
        i, j = cell_of(x, y)
        trials = float(self.trials[sensor_id][i, j]) if sensor_id in self.trials else 0.0
        hits = float(self.hits[sensor_id][i, j]) if sensor_id in self.hits else 0.0
        return (hits + weight * prior) / (trials + weight)

    def clutter_density(self, sensor_id: str, x: float, y: float, prior: float, floor: float,
                        weight: float = 2000.0) -> float:
        """Ghost targets per m^2 and frame of this sensor here: the learned rate, pulled toward the
        prior while few frames were verifiable (the prior counts like `weight` frames)."""
        i, j = cell_of(x, y)
        if sensor_id not in self.exposure or not (0 <= i < SIZE and 0 <= j < SIZE):
            return prior
        exposure = float(self.exposure[sensor_id][i, j])
        rate = (float(self.clutter[sensor_id][i, j]) + weight * prior * CELL * CELL) / (exposure + weight)
        return max(rate / (CELL * CELL), floor)

    # ------------------------------------------------------------ learning

    def learn(self, tracker, sensor, t: float, detections: list, updates: list):
        """After one frame of `sensor`. updates: [(detection, track)] assigned in this frame."""
        self._decay(t)
        sid = sensor.id
        if sid not in self.prior:
            return
        updated = {id(tr) for _, tr in updates}
        confirmed = [tr for tr in tracker.confirmed()]

        # P_D: people who are there for sure, independently of this sensor
        for tr in confirmed:
            if tr.lost(t, tracker.config.params.lost_after):
                continue
            others = any(s != sid and t - ts < 1.0 for s, ts in tr.last_hit_by.items())
            if not (others or (tr.has_origin and t - tr.born > SURE_AGE)):
                continue
            x, y = tr.position()
            if self.pd(sid, x, y) < 0.05:
                continue
            i, j = cell_of(x, y)
            self.trials[sid][i, j] += 1
            if id(tr) in updated:
                self.hits[sid][i, j] += 1

        # clutter: spots another online sensor sees well and reports nothing, nobody known near
        for other in tracker.config.sensors:
            if other.id == sid or other.id not in self.prior or not tracker._online(other.id):
                continue
            ort = tracker.runtime[other.id]
            if t - ort.last_frame > 0.3:
                continue
            mask = (self.prior[other.id] >= 0.8) & (self.prior[sid] > 0.05)
            if not mask.any():
                continue
            mask = mask.copy()
            blocked = [tr.position() for tr in confirmed] + [d.pos for d in ort.detections if not d.hidden]
            for x, y in blocked:
                i, j = cell_of(x, y)
                k = int(1.0 / CELL)
                mask[max(i - k, 0):i + k + 1, max(j - k, 0):j + k + 1] = False
            self.exposure[sid] += mask
            for d in detections:
                if d.stale:
                    continue
                i, j = cell_of(*d.pos)
                if 0 <= i < SIZE and 0 <= j < SIZE and mask[i, j]:
                    self.clutter[sid][i, j] += 1
            break  # one verifying sensor per frame is enough

        # measurement error: the same person seen by two sensors at the same moment
        for d, tr in updates:
            for other_id, other_t in tr.last_hit_by.items():
                if other_id == sid or t - other_t > 0.12:
                    continue
                prev = tracker.runtime[other_id].detections
                near = min(prev, key=lambda e: np.linalg.norm(e.pos - d.pos), default=None)
                if near is None or np.linalg.norm(near.pos - d.pos) > 1.5:
                    continue
                o = tracker.config.sensor_by_id[other_id]
                ra = math.hypot(d.pos[0] - sensor.x, d.pos[1] - sensor.y)
                rb = math.hypot(near.pos[0] - o.x, near.pos[1] - o.y)
                self.accuracy.append((round(ra, 2), round(rb, 2), round(float(np.linalg.norm(near.pos - d.pos)), 3)))
        del self.accuracy[:-MAX_ACCURACY_SAMPLES]
        self.changed = True

    def forget(self, sensor_id: str):
        """The sensor was moved: drop what was learned about it (detection, ghosts, LD2410C)."""
        for store in (self.trials, self.hits, self.exposure, self.clutter, self.ld_occ, self.ld_move, self.ld_emp):
            if sensor_id in store:
                store[sensor_id][:] = 0
        self.ld_history.pop(sensor_id, None)
        self.offsets.clear()  # only differences between sensors are learned
        self.changed = True

    def learn_gap(self, mode: str, duration: float):
        """A person the LD2450 had dropped was seen again at their place after `duration`."""
        if duration >= GAP_FLOOR:
            self.gaps[mode].append(round(duration, 2))
            del self.gaps[mode][:-MAX_GAPS]
            self.changed = True

    def learn_offset(self, sid_a: str, pos_a, sid_b: str, pos_b):
        """One person (sure, alone, still) measured by two sensors at the same moment: their
        systematic difference there, half to each (only the difference is observable)."""
        d = np.asarray(pos_a, dtype=float) - np.asarray(pos_b, dtype=float)
        cell = _offset_cell((np.asarray(pos_a) + np.asarray(pos_b)) / 2)
        for sid, sign in ((sid_a, 0.5), (sid_b, -0.5)):
            acc = self.offsets.setdefault(sid, {}).setdefault(cell, [0.0, 0.0, 0.0])
            acc[0] += sign * d[0]
            acc[1] += sign * d[1]
            acc[2] += 1
        self.changed = True

    def correction(self, sid: str, pos) -> np.ndarray:
        """What to add to a measurement of this sensor here: minus its learned systematic error
        (the place and its neighbours, shrunk toward none while little was seen)."""
        cells = self.offsets.get(sid)
        if not cells:
            return np.zeros(2)
        i, j = math.floor(float(pos[0]) / OFFSET_CELL), math.floor(float(pos[1]) / OFFSET_CELL)
        sx = sy = n = 0.0
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                acc = cells.get(f"{i + di},{j + dj}")
                if acc:
                    wt = 1.0 if di == dj == 0 else 0.25
                    sx += wt * acc[0]
                    sy += wt * acc[1]
                    n += wt * acc[2]
        if n <= 0:
            return np.zeros(2)
        return -np.array([sx, sy]) / (n + OFFSET_PRIOR_N)

    def learn_pair(self, distance: float, both: bool):
        """Two people (sure ones) at this distance in view of a sensor, at least one detected."""
        self.pairs[_res_bin(distance)][0 if both else 1] += 1
        self.changed = True

    def resolution(self, distance: float) -> float:
        """P(both of two people at this distance are detected | at least one is)."""
        k = _res_bin(distance)
        both, one = self.pairs[k]
        return (RES_PRIOR_WEIGHT * RES_PRIOR[k] + both) / (RES_PRIOR_WEIGHT + both + one)

    def redetection_survival(self, mode: str, tau: float) -> float:
        """Share of dropouts (of people who stay put, longer than GAP_FLOOR) that last longer
        than tau."""
        if tau <= GAP_FLOOR:
            return 1.0
        samples = [g for g in self.gaps[mode] if g >= GAP_FLOOR]
        # prior table interpolated log-linearly in tau (given longer than GAP_FLOOR), plus the
        # learned gaps
        p = _prior_survival(mode, tau) / _prior_survival(mode, GAP_FLOOR)
        return (GAP_PRIOR_WEIGHT * p + sum(g > tau for g in samples)) / (GAP_PRIOR_WEIGHT + len(samples))

    def learn_ld2410(self, sensor_id: str, t: float, energies: list, still: set, moving: set, blocked: set):
        """energies: per gate; still/moving: gates with a sure person sitting/walking; blocked:
        gates within two of any person or detection (learned as neither)."""
        if sensor_id not in self.ld_occ or not energies:
            return
        history = self.ld_history.setdefault(sensor_id, [])
        history.append((t, list(energies[:GATES])))
        while history and t - history[0][0] > LD_HISTORY:
            history.pop(0)
        for g, e in enumerate(energies[:GATES]):
            b = min(int(e) // 10, BINS - 1)
            if g in still:
                self.ld_occ[sensor_id][g, b] += 1
            elif g in moving:
                self.ld_move[sensor_id][g, b] += 1
            elif g not in blocked:
                self.ld_emp[sensor_id][g, b] += 1
        self.changed = True

    def learn_ld2410_gap(self, sensor_id: str, gate: int, t_from: float, t_to: float):
        """A person the LD2450 had dropped was seen again at the same place: during the gap the
        gate was occupied, however low the energy was (that is the point)."""
        if sensor_id not in self.ld_occ or not (0 <= gate < GATES):
            return
        for t, energies in self.ld_history.get(sensor_id, []):
            if t_from <= t <= t_to and gate < len(energies):
                b = min(int(energies[gate]) // 10, BINS - 1)
                self.ld_occ[sensor_id][gate, b] += 1
        self.changed = True

    def ld2410_ratio(self, sensor_id: str, gate: int, energy: float, moving: bool = False) -> float:
        """Likelihood ratio: this energy with a person sitting (or moving) in the gate vs. with
        nobody near it."""
        if sensor_id not in self.ld_occ or not (0 <= gate < GATES):
            return 1.0
        b = min(int(energy) // 10, BINS - 1)
        occ_prior = LD_OCC_PRIOR if gate in LD_GOOD_GATES else LD_FLAT_PRIOR
        emp_prior = LD_EMP_PRIOR if gate in LD_GOOD_GATES else LD_FLAT_PRIOR
        occ = (self.ld_move if moving else self.ld_occ)[sensor_id][gate]
        emp = self.ld_emp[sensor_id][gate]
        p_occ = (occ[b] + occ_prior[b]) / (occ.sum() + sum(occ_prior))
        p_emp = (emp[b] + emp_prior[b]) / (emp.sum() + sum(emp_prior))
        return float(min(max(p_occ / p_emp, LD_RATIO_RANGE[0]), LD_RATIO_RANGE[1]))

    def ld2410_stats(self, sensor_id: str) -> list:
        """Per gate: learned frames (occupied, empty) and the ratio for a typical high energy."""
        if sensor_id not in self.ld_occ:
            return []
        return [{"gate": g, "sitting": int(self.ld_occ[sensor_id][g].sum()), "moving": int(self.ld_move[sensor_id][g].sum()),
                 "empty": int(self.ld_emp[sensor_id][g].sum()),
                 "ratio_at_60": round(self.ld2410_ratio(sensor_id, g, 60), 2), "ratio_at_5": round(self.ld2410_ratio(sensor_id, g, 5), 2)}
                for g in range(GATES)]

    def _decay(self, t: float):
        if self.last_decay is None:
            self.last_decay = t
        if t - self.last_decay < 600:
            return
        f = 0.5 ** ((t - self.last_decay) / HALF_LIFE)
        for store in (self.trials, self.hits, self.exposure, self.clutter, self.ld_occ, self.ld_move, self.ld_emp):
            for grid in store.values():
                grid *= f
        self.last_decay = t

    # -------------------------------------------------------------- output

    def accuracy_fit(self) -> dict | None:
        """Total 2D error per sensor sigma(r) = a + b r, from offsets between two sensors:
        E[offset^2] = sigma(ra)^2 + sigma(rb)^2 (both sensors alike). Least squares on a grid."""
        if len(self.accuracy) < 200:
            return None
        s = np.array(self.accuracy)
        ra, rb, d2 = s[:, 0], s[:, 1], s[:, 2] ** 2
        d2 = np.minimum(d2, np.percentile(d2, 95))  # wrong pairs (two people) are outliers
        best = None
        for a in np.arange(0.0, 0.41, 0.01):
            for b in np.arange(0.0, 0.151, 0.005):
                err = ((a + b * ra) ** 2 + (a + b * rb) ** 2 - d2)
                cost = float(np.mean(err * err))
                if best is None or cost < best[0]:
                    best = (cost, a, b)
        return {"base": round(best[1], 3), "slope": round(best[2], 3), "samples": len(self.accuracy)}

    def maps(self, sensor_id: str) -> dict:
        """The three maps over the house, for the UI. None where too little was learned."""
        if sensor_id not in self.prior:
            return {}
        i0, j0, i1, j1 = self.box
        sl = (slice(i0, i1), slice(j0, j1))
        trials, hits = self.trials[sensor_id][sl], self.hits[sensor_id][sl]
        exposure, clutter = self.exposure[sensor_id][sl], self.clutter[sensor_id][sl]

        def grid(values, valid):
            return [[round(float(v), 3) if ok else None for v, ok in zip(row, oks)] for row, oks in zip(values, valid)]
        learned = np.where(trials > 0, hits / np.maximum(trials, 1e-9), 0)
        rate = np.where(exposure > 0, clutter / np.maximum(exposure, 1e-9), 0)
        x0, y0 = cell_center(i0, j0)
        return {
            "cell": CELL, "x0": x0 - CELL / 2, "y0": y0 - CELL / 2, "cols": i1 - i0, "rows": j1 - j0,
            # [column][row], row 0 = lowest y
            "prior": grid(self.prior[sensor_id][sl], np.ones_like(trials, dtype=bool)),
            "learned": grid(learned, trials >= 20),
            "clutter": grid(rate, exposure >= 200),
            "learned_cells": int((trials >= 20).sum()), "clutter_cells": int((exposure >= 200).sum()),
        }

    # --------------------------------------------------------- persistence

    def to_dict(self) -> dict:
        def sparse(store):
            return {sid: {f"{i},{j}": round(float(g[i, j]), 2) for i, j in zip(*np.nonzero(g > 0.01))}
                    for sid, g in store.items()}
        return {"trials": sparse(self.trials), "hits": sparse(self.hits), "exposure": sparse(self.exposure),
                "clutter": sparse(self.clutter), "accuracy": self.accuracy, "last_decay": self.last_decay,
                "gaps": self.gaps, "offsets": {sid: {k: [round(v, 3) for v in acc] for k, acc in cells.items()} for sid, cells in self.offsets.items()}, "pairs": self.pairs, "ld_occ": {k: v.round(2).tolist() for k, v in self.ld_occ.items()},
                "ld_move": {k: v.round(2).tolist() for k, v in self.ld_move.items()},
                "ld_emp": {k: v.round(2).tolist() for k, v in self.ld_emp.items()}}

    def load_dict(self, data: dict):
        """Replace everything learned (e.g. learned offline from recordings and imported)."""
        for name in ("trials", "hits", "exposure", "clutter"):
            store = getattr(self, name)
            for grid in store.values():
                grid[:] = 0
            for sid, cells in data.get(name, {}).items():
                grid = store.setdefault(sid, np.zeros((SIZE, SIZE), dtype=np.float32))
                for key, v in cells.items():
                    i, j = map(int, key.split(","))
                    if 0 <= i < SIZE and 0 <= j < SIZE:
                        grid[i, j] = v
        self.accuracy = [tuple(a) for a in data.get("accuracy", [])][-MAX_ACCURACY_SAMPLES:]
        self.gaps = {k: list(data.get("gaps", {}).get(k, []))[-MAX_GAPS:] for k in ("still", "walking")}
        self.offsets = {sid: {k: [float(v) for v in acc] for k, acc in cells.items()} for sid, cells in data.get("offsets", {}).items()}
        pairs = data.get("pairs")
        self.pairs = [list(map(float, x)) for x in pairs] if pairs and len(pairs) == len(RES_EDGES) else [[0.0, 0.0] for _ in RES_EDGES]
        for name in ("ld_occ", "ld_move", "ld_emp"):
            store = getattr(self, name)
            for sid, rows in data.get(name, {}).items():
                arr = np.array(rows, dtype=np.float32)
                if arr.shape == (GATES, BINS):
                    store[sid] = arr
        self.last_decay = data.get("last_decay")

    def save(self, path: pathlib.Path):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict()))
        tmp.replace(path)

    def load(self, path: pathlib.Path):
        if path.exists():
            self.load_dict(json.loads(path.read_text()))


def _res_bin(distance: float) -> int:
    return next(k for k, edge in enumerate(RES_EDGES) if distance < edge)
