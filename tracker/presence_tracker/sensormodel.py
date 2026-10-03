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
person at the same time.

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

    def _decay(self, t: float):
        if self.last_decay is None:
            self.last_decay = t
        if t - self.last_decay < 600:
            return
        f = 0.5 ** ((t - self.last_decay) / HALF_LIFE)
        for store in (self.trials, self.hits, self.exposure, self.clutter):
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
                "clutter": sparse(self.clutter), "accuracy": self.accuracy, "last_decay": self.last_decay}

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
        self.last_decay = data.get("last_decay")

    def save(self, path: pathlib.Path):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict()))
        tmp.replace(path)

    def load(self, path: pathlib.Path):
        if path.exists():
            self.load_dict(json.loads(path.read_text()))
