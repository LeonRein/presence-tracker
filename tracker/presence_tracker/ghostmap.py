"""Where each sensor starts ghost tracks (MODEL.md 4.2): per sensor and raster cell a Poisson process
with a Gamma prior (Luber, Tipaldi & Arras 2011 = Luber 2014, ch. 6, eq. 6.8-6.13).

The prior has the mean of the global rate (one ghost track in 7 h at night) and in each cell the
weight of one ghost (the Gamma's shape, Luber eq. 6.13; the watching time follows from the rate,
about 4 days). Learned by EM (Kantas et al. 2015, sec. 5), online in the app: every track that
ends counts at the cell where it began, against the time the sensor watched, with the filter's
probability that it was a ghost - judged at its end, but with the prior's rate at the birth in place
of the map's (Park et al. 2020, eq. 37-41: the clutter probability without the clutter estimate).
What the map says at a spot is no evidence for it: else a spot judged "ghost" once is judged so
more and more. That probability is often exactly 1 (the person alternatives fell under the
hypotheses' floor), so the counts are hard decisions; the prior's weight of one ghost per cell keeps
a few of them from making a spot a source (MODEL.md 4.2, 10: with the weight of 0.04 ghosts, until
0.12.0, a seat whose tracks the filter took for ghosts was 21 times the prior after 2.5 h). How long
ghosts live is not learned here: it is what tells ghosts from people, and learned from the filter's
own judgement it followed the people taken for ghosts (filtermodel.ghost_types, estimated offline by
tools/ghostmap.py). Counts fade with FORGET (furniture, the robot's dock move). tools/ghostmap.py
runs the same EM offline over recordings, for judging it on the truth database without learning
from it.

Each sensor's part of the map is kept in that sensor's own frame (SensorConfig.to_local: on the
floor, x to its right, y forward, its scale taken out, its x axis as the LD2450 reports it): where a
sensor sees ghosts is a property of its view - reflections off the walls and the furniture as it sees
them, multipath at its own housing - and a new calibration (heading, scale, mirror, a drawing put
right) changes how its reports are read, not what it reports. In the house frame the map follows the
pose the sensor has now. Only a sensor mounted elsewhere (model.remounted) starts its part over; the
others keep theirs (MODEL.md 4.2, 10; until 0.27 every change of any pose started all of it over).
"""

import json
import logging
import math
import os

import numpy as np

from .model import remounted

log = logging.getLogger(__name__)

CELL = 0.4  # m, in the sensor's frame (its scale taken out)
RANGE = 8.0  # m: the cells reach this far to either side and forward (the LD2450 reports up to 8 m)
NX, NY = int(round(2 * RANGE / CELL)), int(round(RANGE / CELL))
# where a track begins scatters around its source with the spread of the sensor's offset (MODEL.md 4.1,
# 0.2-0.3 m): the counts are spread over the cells with this much (a cell of a clutter map is otherwise
# too sharp: Park et al. 2020, sec. 1)
SPREAD = 0.3  # m
FORGET = 14 * 86400.0  # s: what was learned fades with this time constant (assumed)
DECAY_EVERY = 60.0  # s between two fadings


def pose_of(s) -> tuple:
    """What of a sensor's setup decides where its targets land."""
    return (s.x, s.y, s.heading, s.height, s.mirror, s.scale)


def to_local(pose: tuple, pos) -> tuple:
    """House frame (n, 2) -> the sensor's frame on the floor (lx, ly), as SensorConfig.to_local."""
    x, y, heading, _, mirror, scale = pose
    pos = np.asarray(pos, dtype=float).reshape(-1, 2)
    h = math.radians(heading)
    dx, dy = (pos[:, 0] - x) / scale, (pos[:, 1] - y) / scale
    gy = dx * math.cos(h) + dy * math.sin(h)
    gx = dx * math.sin(h) - dy * math.cos(h)
    return (-gx if mirror else gx), gy


def _local_cell(pose: tuple, pos) -> tuple:
    lx, ly = to_local(pose, pos)
    i = np.clip(np.floor((lx + RANGE) / CELL).astype(int), 0, NX - 1)
    j = np.clip(np.floor(ly / CELL).astype(int), 0, NY - 1)
    return i, j


class GhostMap:
    def __init__(self, prior_rate: float, prior_time: float):
        self.prior_rate = prior_rate  # per m^2 (of the floor) and s
        self.prior_time = prior_time  # s: weight of the prior, as watching time of a cell of CELL^2 m^2
        self.count = {}  # sensor id -> (NX, NY) expected number of ghost births, in its own frame
        self.time = {}  # sensor id -> s watched
        self.poses = {}  # sensor id -> its pose now (pose_of): where its frame lies in the house
        self.forget = FORGET
        self._smooth = {}
        self._faded = None

    @classmethod
    def from_prior(cls, prior_rate: float, prior_ghosts: float) -> "GhostMap":
        """A map from the prior: rate prior_rate everywhere, in each cell with the weight of
        prior_ghosts ghosts (the Gamma prior's shape; its watching time follows from the rate)."""
        return cls(prior_rate, prior_ghosts / (prior_rate * CELL * CELL))

    def use(self, config) -> list:
        """Follow the sensors as they are configured now: a recalibrated one keeps its part (it is in
        its own frame; in the house frame it moves with the new pose); one mounted elsewhere
        (model.remounted) or new starts from the prior; the part of one no longer there goes.
        Returns the ids of the sensors that start from the prior."""
        ids = {s.id for s in config.sensors}
        for sid in [k for k in self.poses if k not in ids]:
            self._drop(sid)
            del self.poses[sid]
        fresh, moved = [], []
        for s in config.sensors:
            old = self.poses.get(s.id)
            if old is None or remounted(old, s):
                (fresh if old is None else moved).append(s.id)
                self._drop(s.id)
            self.poses[s.id] = pose_of(s)
        if moved:
            log.info("ghost map: %s mounted elsewhere, their part starts over", ", ".join(moved))
        return fresh + moved

    def _drop(self, sid: str):
        self.count.pop(sid, None)
        self.time.pop(sid, None)
        self._smooth.pop(sid, None)

    def rate(self, sid: str, pos) -> np.ndarray:
        """Posterior mean rate of ghost births per m^2 and s at pos (n, 2, house frame) for sensor
        sid. A cell of its frame covers (CELL scale)^2 of the floor."""
        pose = self.poses.get(sid)
        if pose is None:
            return np.full(len(np.asarray(pos).reshape(-1, 2)), self.prior_rate)
        i, j = _local_cell(pose, pos)
        n = self.smoothed(sid)[i, j] if sid in self.count else np.zeros(len(i))
        T = self.time.get(sid, 0.0)
        k0 = self.prior_rate * CELL * CELL * self.prior_time  # the prior's weight per cell, in ghosts
        return (k0 + n) / (k0 / self.prior_rate + (CELL * pose[5]) ** 2 * T)

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
        return True

    def add_birth(self, sid: str, pos, p_ghost: float):
        """A track of sensor sid that began at pos (house frame, read with the sensor's pose now)."""
        pose = self.poses.get(sid)
        if pose is None:
            return
        if sid not in self.count:
            self.count[sid] = np.zeros((NX, NY))
        i, j = _local_cell(pose, pos)
        self.count[sid][i[0], j[0]] += p_ghost
        self._smooth.pop(sid, None)

    def to_dict(self) -> dict:
        return {"cell": CELL, "range": RANGE, "prior_rate": self.prior_rate,
                "prior_time": self.prior_time, "time": dict(self.time), "poses": dict(self.poses),
                "count": {k: v.round(4).tolist() for k, v in self.count.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "GhostMap":
        if not (math.isclose(d["cell"], CELL) and math.isclose(d["range"], RANGE)):
            raise ValueError("ghost map of another grid")
        g = cls(d["prior_rate"], d["prior_time"])
        g.time = dict(d["time"])
        g.poses = {k: tuple(v) for k, v in d["poses"].items()}
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
