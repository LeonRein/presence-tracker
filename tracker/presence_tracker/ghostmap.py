"""Where each sensor starts ghost tracks (MODEL.md 4.2): per sensor and raster cell a Poisson process
with a Gamma prior (Luber, Tipaldi & Arras 2011 = Luber 2014, ch. 6, eq. 6.8-6.13).

The prior has the mean of the global rate (one ghost track in 7 h at night) and the weight of a few
hours of watching. Learned by EM (Kantas et al. 2015, sec. 5), online in the app: every track that ends counts with the
filter's probability that it was a ghost (judged at its end) at the cell where it began, against the
time the sensor watched; how long ghosts live (a mixture of exponentials) is fitted to the tracks'
lives weighted the same way (online EM, sufficient statistics). Counts fade with FORGET (furniture,
the robot's dock move). tools/ghostmap.py runs the same EM offline over recordings, for judging it
on the truth database without learning from it.

A map belongs to the sensors' poses it was learned with: when one is moved, added or removed (they
disturb each other), all of it starts over from the prior.
"""

import json
import math
import os

import numpy as np

CELL = 0.4  # m
# where a track begins scatters around its source with the spread of the sensor's offset (MODEL.md 4.1,
# 0.2-0.3 m): the counts are spread over the cells with this much (a cell of a clutter map is otherwise
# too sharp: Park et al. 2020, sec. 1)
SPREAD = 0.3  # m
FORGET = 14 * 86400.0  # s: what was learned fades with this time constant (assumed)
DECAY_EVERY = 60.0  # s between two fadings
PRIOR_GHOSTS = 20.0  # weight of the prior over the kinds of ghosts, in ghosts


class GhostMap:
    def __init__(self, x0: float, y0: float, nx: int, ny: int, prior_rate: float, prior_time: float):
        self.x0, self.y0, self.nx, self.ny = x0, y0, nx, ny
        self.prior_rate = prior_rate  # per m^2 and s
        self.prior_time = prior_time  # s: weight of the prior, as watching time
        self.count = {}  # sensor id -> (nx, ny) expected number of ghost births
        self.time = {}  # sensor id -> s watched
        self.poses = {}  # sensor id -> (x, y, heading) it was learned with
        self.types = ()  # kinds of ghosts: ((share, mean life s), ...), learned with the map
        self.life_stats = None  # online EM over the kinds: (weights (k,), weighted lives (k,))
        self.forget = FORGET
        self._smooth = {}
        self._faded = None

    @classmethod
    def for_world(cls, world, prior_rate: float, prior_time: float) -> "GhostMap":
        nx = int(math.ceil(world.nx * 0.1 / CELL))
        ny = int(math.ceil(world.ny * 0.1 / CELL))
        return cls(world.x0, world.y0, nx, ny, prior_rate, prior_time)

    def _cell(self, pos) -> tuple:
        pos = np.asarray(pos, dtype=float).reshape(-1, 2)
        i = np.clip(np.floor((pos[:, 0] - self.x0) / CELL).astype(int), 0, self.nx - 1)
        j = np.clip(np.floor((pos[:, 1] - self.y0) / CELL).astype(int), 0, self.ny - 1)
        return i, j

    def rate(self, sid: str, pos) -> np.ndarray:
        """Posterior mean rate of ghost births per m^2 and s at pos (n, 2) for sensor sid."""
        i, j = self._cell(pos)
        n = self.smoothed(sid)[i, j] if sid in self.count else np.zeros(len(i))
        T = self.time.get(sid, 0.0)
        A = CELL * CELL
        return (self.prior_rate * A * self.prior_time + n) / (A * (self.prior_time + T))

    def smoothed(self, sid: str) -> np.ndarray:
        """The counts spread with SPREAD (a Gaussian, separable; the sum stays)."""
        if sid not in self._smooth:
            r = int(math.ceil(3 * SPREAD / CELL))
            k = np.exp(-0.5 * (np.arange(-r, r + 1) * CELL / SPREAD) ** 2)
            k /= k.sum()
            c = self.count[sid]
            c = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 0, c)
            c = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, c)
            self._smooth[sid] = c
        return self._smooth[sid]

    def add_watch(self, sid: str, seconds: float):
        self.time[sid] = self.time.get(sid, 0.0) + seconds

    def fade(self, t: float) -> bool:
        """What was learned fades with FORGET, applied every DECAY_EVERY s; returns whether it did."""
        if self._faded is None:
            self._faded = t
        if not self.forget or t - self._faded < DECAY_EVERY:
            return False
        f = math.exp(-(t - self._faded) / self.forget)
        self._faded = t
        for sid in self.time:
            self.time[sid] *= f
        for sid in self.count:
            self.count[sid] *= f
        self._smooth = {}
        if self.life_stats is not None:
            self.life_stats = (self.life_stats[0] * f, self.life_stats[1] * f)
        return True

    def add_life(self, life: float, p_ghost: float, default_types):
        """Online EM step for how long ghosts live: a track that lived life s and was a ghost with
        p_ghost (Cappe & Moulines style sufficient statistics, started from default_types)."""
        types = self.types or tuple(default_types)
        if self.life_stats is None:
            share = np.array([s for s, _ in types], dtype=float)
            share /= share.sum()
            mean = np.array([m for _, m in types], dtype=float)
            self.life_stats = (PRIOR_GHOSTS * share, PRIOR_GHOSTS * share * mean)
        share = np.array([s for s, _ in types], dtype=float)
        share /= share.sum()
        mean = np.array([m for _, m in types], dtype=float)
        x = max(life, 0.089)
        r = share / mean * np.exp(-x / mean)
        r /= max(r.sum(), 1e-300)
        s0, s1 = self.life_stats
        s0, s1 = s0 + p_ghost * r, s1 + p_ghost * r * x
        self.life_stats = (s0, s1)
        self.types = tuple((float(a / s0.sum()), float(b / a)) for a, b in zip(s0, s1))

    def add_birth(self, sid: str, pos, p_ghost: float):
        if sid not in self.count:
            self.count[sid] = np.zeros((self.nx, self.ny))
        i, j = self._cell(pos)
        self.count[sid][i[0], j[0]] += p_ghost
        self._smooth.pop(sid, None)

    def matches(self, config) -> bool:
        """Learned with the sensors where they are now (and no other ones)?"""
        if {s.id for s in config.sensors} != set(self.poses):
            return False
        for s in config.sensors:
            pose = self.poses.get(s.id)
            if pose is None or abs(pose[0] - s.x) > 0.05 or abs(pose[1] - s.y) > 0.05 or abs(pose[2] - s.heading) > 2:
                return False
        return True

    def to_dict(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "nx": self.nx, "ny": self.ny, "prior_rate": self.prior_rate,
                "prior_time": self.prior_time, "time": self.time, "poses": self.poses, "types": self.types,
                "life_stats": [v.tolist() for v in self.life_stats] if self.life_stats is not None else None,
                "count": {k: v.round(4).tolist() for k, v in self.count.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "GhostMap":
        g = cls(d["x0"], d["y0"], d["nx"], d["ny"], d["prior_rate"], d["prior_time"])
        g.time = dict(d["time"])
        g.poses = {k: tuple(v) for k, v in d["poses"].items()}
        g.types = tuple(tuple(t) for t in d.get("types", ()))
        if d.get("life_stats"):
            g.life_stats = tuple(np.array(v) for v in d["life_stats"])
        g.count = {k: np.array(v) for k, v in d["count"].items()}
        return g

    def save(self, path):
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path) -> "GhostMap":
        with open(path) as f:
            return cls.from_dict(json.load(f))
