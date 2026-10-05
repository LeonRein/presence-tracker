"""What each sensor can see, where it sees ghosts, and what its LD2410C shows with and without a
person.

  prior P_D   detection probability from the geometry (per CELL m on a fixed grid anchored at
              ORIGIN): 0 behind walls and in closed rooms without a sensor, falling toward the
              edge of the field of view and the maximum range. Learning where a sensor actually
              reports people was measured not to help (Drehbuch 4.10.) and was removed in 0.6.7.
  ghosts      how often the sensor reports a target where nobody is (same grid): only counted where
              another online sensor sees the spot well, reports nothing there, and no known person
              is near. Never from the model's own verdicts: someone who always sits where only one
              sensor sees would otherwise be learned away as a ghost. Fades like the LD2410C counts.
  LD2410C     per sensor and 0.75 m gate, the energy it shows with a person sitting / walking in
              that gate versus with nobody near it, learned. Counts fade with a half-life of
              HALF_LIFE, so they follow changes (furniture, a moved sensor).

The measurement error from pairs of sensors, the LD2450's dropout lengths and its resolution of two
people close together were learned here once; the model measures them offline now or doesn't need
them (0.6.11).
"""

import json
import math
import pathlib

import numpy as np

from .geometry import line_of_sight

CELL = 0.25  # m
ORIGIN = (-30.0, -30.0)  # grid anchor
SIZE = 240  # cells per side: 60 m
HALF_LIFE = 7 * 24 * 3600.0  # s
PD_MAX = 0.95
EDGE_ANGLE = 15.0  # degrees inside the field-of-view edge where P_D starts to fall
EDGE_RANGE = 1.5  # m before the maximum range where P_D starts to fall
PD_AT_EDGE = 0.4
RANGE_TAIL = 1.5  # m: beyond the nominal range the assumed P_D falls by e every this much
RANGE_TAIL_MAX = 4.0  # m beyond the nominal range: assumed 0 from there (about 3 % left)
GATE = 0.75  # m, LD2410C distance gate
GATES = 9
BINS = 10  # energy 0-100 in steps of 10
# Re-detection survival S(tau) of a person who stays put: share of LD2450 dropouts that last
# longer than tau (measured 2026-10-03, both sensors). Gaps shorter than 0.3 s don't count.
GAP_TAUS = (0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 60.0, 120.0)
STILL_GAPS = (1.0, 0.42, 0.30, 0.26, 0.24, 0.21, 0.18, 0.13, 0.09, 0.03, 0.01)
GAP_FLOOR = 1.5  # s: a dropout this long that ends where it began tells where the LD2410C saw a sitter


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
        self.exposure = {}  # sensor -> SIZE x SIZE: frames a spot was verifiable (fading counts)
        self.clutter = {}  # ... and ghosts reported there
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
            for store in (self.exposure, self.clutter):
                store.setdefault(s.id, np.zeros((SIZE, SIZE), dtype=np.float32))
            for store in (self.ld_occ, self.ld_move, self.ld_emp):
                store.setdefault(s.id, np.zeros((GATES, BINS), dtype=np.float32))

    def prior_pd(self, s, x: float, y: float) -> float:
        """Assumed detection probability: full in the field of view, falling toward its edge and
        toward the nominal range - and beyond it not 0 but fading (the LD2450 still reports
        people farther away, less reliably; what it really does there is learned)."""
        if any(z.contains(x, y) for z in self.config.closed_rooms):
            return 0.0
        lx, ly = s.to_local(x, y)
        r = math.hypot(lx, ly)
        if ly <= 0 or r > s.range + RANGE_TAIL_MAX:
            return 0.0
        az = abs(math.degrees(math.atan2(lx, ly)))
        if az > s.fov / 2 or not line_of_sight(s.sight_origin(), (x, y), self.config.wall_segments):
            return 0.0

        def taper(value, start, end):
            if value <= start:
                return 1.0
            return 1.0 - (1.0 - PD_AT_EDGE) * min((value - start) / max(end - start, 1e-6), 1.0)
        far = math.exp(-max(r - s.range, 0.0) / RANGE_TAIL)
        return PD_MAX * taper(az, s.fov / 2 - EDGE_ANGLE, s.fov / 2) * taper(r, s.range - EDGE_RANGE, s.range) * far

    def pd(self, sensor_id: str, x: float, y: float) -> float:
        """Prior detection probability (geometry): from the map, or computed outside of it."""
        i, j = cell_of(x, y)
        i0, j0, i1, j1 = self.box
        grid = self.prior.get(sensor_id)
        if grid is not None and i0 <= i < i1 and j0 <= j < j1:
            return float(grid[i, j])
        s = self.config.sensor_by_id.get(sensor_id)
        return self.prior_pd(s, x, y) if s is not None and s.placed and s.enabled else 0.0

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

    def forget(self, sensor_id: str):
        """The sensor was moved: drop what was learned about it (ghosts, LD2410C)."""
        for store in (self.exposure, self.clutter, self.ld_occ, self.ld_move, self.ld_emp):
            if sensor_id in store:
                store[sensor_id][:] = 0
        self.ld_history.pop(sensor_id, None)
        self.changed = True

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
        for store in (self.exposure, self.clutter, self.ld_occ, self.ld_move, self.ld_emp):
            for grid in store.values():
                grid *= f
        self.last_decay = t

    # -------------------------------------------------------------- output

    def maps(self, sensor_id: str) -> dict:
        """The detection probability and the learned ghost rate over the house, for the UI (ghosts:
        None where too little was verifiable)."""
        if sensor_id not in self.prior:
            return {}
        i0, j0, i1, j1 = self.box
        sl = (slice(i0, i1), slice(j0, j1))
        exposure, clutter = self.exposure[sensor_id][sl], self.clutter[sensor_id][sl]
        rate = np.where(exposure > 0, clutter / np.maximum(exposure, 1e-9), 0)
        x0, y0 = cell_center(i0, j0)
        return {
            "cell": CELL, "x0": x0 - CELL / 2, "y0": y0 - CELL / 2, "cols": i1 - i0, "rows": j1 - j0,
            # [column][row], row 0 = lowest y
            "prior": [[round(float(v), 3) for v in row] for row in self.prior[sensor_id][sl]],
            "clutter": [[round(float(v), 3) if ok else None for v, ok in zip(row, oks)]
                        for row, oks in zip(rate, exposure >= 200)],
            "clutter_cells": int((exposure >= 200).sum()),
        }

    # --------------------------------------------------------- persistence

    def to_dict(self) -> dict:
        def sparse(store):
            return {sid: {f"{i},{j}": round(float(g[i, j]), 2) for i, j in zip(*np.nonzero(g > 0.01))}
                    for sid, g in store.items()}
        return {"exposure": sparse(self.exposure), "clutter": sparse(self.clutter),
                "last_decay": self.last_decay, "ld_occ": {k: v.round(2).tolist() for k, v in self.ld_occ.items()},
                "ld_move": {k: v.round(2).tolist() for k, v in self.ld_move.items()},
                "ld_emp": {k: v.round(2).tolist() for k, v in self.ld_emp.items()}}

    def load_dict(self, data: dict):
        """Replace everything learned (e.g. learned offline from recordings and imported)."""
        for name in ("exposure", "clutter"):
            store = getattr(self, name)
            for grid in store.values():
                grid[:] = 0
            for sid, cells in data.get(name, {}).items():
                grid = store.setdefault(sid, np.zeros((SIZE, SIZE), dtype=np.float32))
                for key, v in cells.items():
                    i, j = map(int, key.split(","))
                    if 0 <= i < SIZE and 0 <= j < SIZE:
                        grid[i, j] = v
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

