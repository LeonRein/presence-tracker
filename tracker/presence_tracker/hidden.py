"""People without a measuring track (MODEL.md 5.3): their density on a raster, exact sums instead of
samples.

Who has no measuring track is spread widely - over the rooms in view, behind doors, out of the
house. Particles would hold such a spread only with a few samples at any spot, and whether a new
track comes from them would hang on those few (5.10., 16:22: 4800 particles, the weight of "a person"
over "a ghost" scattered by a factor 10^5 between seeds). A point-mass filter (Bucy & Senne 1971,
Bergman 1999) holds the density on a raster instead; in a PMBM filter it is the "undetected" part
(Williams 2015, Garcia-Fernandez et al. 2018), here one density per person. A person whose
tracks are all held lies here too: walking off unseen is exact on the raster (MODEL.md 5.4).

State of an unseen person, as masses that sum to 1:
  walk[h, c]       walking in raster cell c, heading h (8 directions): a velocity-jump process
                   (MODEL.md 3.2) - straight on one cell per tick, turning now and then,
                   bouncing off walls, through doors into the regions behind them
  still[l, k, c]   standing or sitting in cell c, with the stay's rate of getting up go_l and its
                   detectability kappa_k (filtermodel.Shapes, MODEL.md 3.1, 4.1)
  region[r, a]     behind a door in region r, for a time in age bin a (the stay ends by its hazard)
  out              out of the house
"""

import math

import numpy as np

from .filtermodel import STILL, WALK
from .world import CELL, OBSERVED

HC = 0.2  # m, raster of the unseen
HEADINGS = np.array([(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)])
AGE_EDGES = np.concatenate([[0.0], 2.0 ** np.arange(1, 18)])  # s, region stay ages: 2 s ... 36 h


def heading_of(v: np.ndarray) -> np.ndarray:
    """Nearest of the 8 headings for velocities (n, 2)."""
    return (np.round(np.arctan2(v[:, 1], v[:, 0]) / (math.pi / 4)).astype(int)) % 8


class Lattice:
    """The raster and what is fixed on it: moves per heading, doors, rooms, how sensors see."""

    def __init__(self, tracker):
        self.tr = tr = tracker
        w, m = tr.world, tr.m
        self.world = w
        nI = max(int(math.ceil(w.nx * CELL / HC)), 1)
        nJ = max(int(math.ceil(w.ny * CELL / HC)), 1)
        ii, jj = np.meshgrid(np.arange(nI), np.arange(nJ), indexing="ij")
        centers = np.stack([w.x0 + (ii + 0.5) * HC, w.y0 + (jj + 0.5) * HC], axis=-1).reshape(-1, 2)
        label = w.place_of(centers)
        keep = np.flatnonzero(label == OBSERVED)
        self.n = n = len(keep)
        self.centers = centers[keep]
        index = np.full(nI * nJ, -1)
        index[keep] = np.arange(n)
        self.shape = (nI, nJ)
        self._index = index
        self.R = len(w.places) - 2  # regions
        # moves: per heading, the target cell, or a region (-2 - r), or blocked (-1)
        self.target = np.full((8, n), -1)
        I, J = ii.reshape(-1)[keep], jj.reshape(-1)[keep]
        for h, (di, dj) in enumerate(HEADINGS):
            ti, tj = I + di, J + dj
            ok = (ti >= 0) & (ti < nI) & (tj >= 0) & (tj < nJ)
            flat = np.where(ok, ti * nJ + tj, 0)
            lab = np.where(ok, label[flat], -1)
            free = ok & (lab >= 0) & ~w.crosses_wall(self.centers, centers[flat])
            to_cell = free & (lab == OBSERVED)
            to_region = free & (lab > OBSERVED) & (lab < w.outside)
            self.target[h, to_cell] = index[flat[to_cell]]
            self.target[h, to_region] = -2 - (lab[to_region] - 1)
        self.into = np.where(self.target <= -2, -2 - self.target, -1)  # per heading: the region a step goes into
        # diagonal steps are longer: taken with a share 1/sqrt(2) per tick, the speed stays the same
        self.step_share = np.array([1.0 / math.hypot(di, dj) for di, dj in HEADINGS])
        self.tick = HC / m.speed
        # doors: where someone coming out of a region appears (cell, heading inward)
        self.doors = {r: [] for r in range(self.R)}
        for place, watch, inward in w.portals:
            if place == w.outside:
                continue
            c = self.cell_near(watch)
            if c >= 0:
                self.doors[place - 1].append((c, int(heading_of(inward[None, :])[0])))
        self.entries = []  # ways in from outside straight into view
        for watch, inward in w.portals_of(w.outside):
            c = self.cell_near(watch)
            if c >= 0:
                self.entries.append((c, int(heading_of(np.asarray(inward)[None, :])[0])))
        self.open = [p - 1 for p in w.open_places]
        self.room = tr.room_of[w.cell_of(self.centers)] if n else np.zeros(0, dtype=int)
        self.widths = np.diff(np.concatenate([AGE_EDGES, [AGE_EDGES[-1] * 2]]))
        self.A = len(AGE_EDGES)
        self._region_rates = None
        self.sh = tr.shapes
        # per sensor: how well it sees each cell (geometry) and the distance to it
        self.g = {}
        self.dist = {}

    def cell_near(self, xy) -> int:
        """The raster cell of a point, or the nearest one in view."""
        if not self.n:
            return -1
        d = ((self.centers - np.asarray(xy)[None, :]) ** 2).sum(axis=1)
        return int(np.argmin(d))

    def cells_of(self, pos: np.ndarray) -> np.ndarray:
        """Raster cells of points (n, 2) in view (nearest cell where the point's own is not one)."""
        nI, nJ = self.shape
        w = self.world
        i = np.clip(np.floor((pos[:, 0] - w.x0) / HC).astype(int), 0, nI - 1)
        j = np.clip(np.floor((pos[:, 1] - w.y0) / HC).astype(int), 0, nJ - 1)
        c = self._index[i * nJ + j]
        for k in np.flatnonzero(c < 0):
            c[k] = self.cell_near(pos[k])
        return c

    def seen(self, si: int) -> tuple:
        """(geometry 0..1, distance) of every cell for sensor si."""
        if si not in self.g:
            self.g[si] = self.tr._g(si, self.centers) if self.n else np.zeros(0)
            self.dist[si] = self.tr._polar(si, self.centers)[0] if self.n else np.zeros(0)
        return self.g[si], self.dist[si]

    def region_rates(self) -> np.ndarray:
        """(R, A): rate (1/s) at which a stay in each region and age bin ends (from Dwell)."""
        if self._region_rates is None or self.tr.dwell.changed:
            tr = self.tr
            out = np.zeros((self.R, self.A))
            hi = AGE_EDGES + self.widths
            for r in range(self.R):
                name = self.world.places[r + 1]
                for a in range(self.A):
                    s0 = tr.dwell.survival(name, AGE_EDGES[a])
                    s1 = tr.dwell.survival(name, hi[a])
                    out[r, a] = math.log(max(s0, 1e-12) / max(s1, 1e-12)) / self.widths[a]
            self._region_rates = out
        return self._region_rates

    def region_start(self, r: int) -> np.ndarray:
        """Age distribution of a stay in region r seen at a random time: proportional to its
        survival (renewal theory)."""
        name = self.world.places[r + 1]
        mid = AGE_EDGES + 0.5 * self.widths
        s = np.array([self.tr.dwell.survival(name, a) for a in mid]) * self.widths
        return s / s.sum()


class Hidden:
    """The density of one person without a track (module doc). Objects are shared between
    hypotheses and changed only by what applies to all of them (motion, no track started); what
    differs between hypotheses makes a new object."""

    def __init__(self, lat: Lattice):
        self.lat = lat
        self.walk = np.zeros((8, lat.n))
        self.still = np.zeros((len(lat.sh.go), len(lat.sh.kappa), lat.n))
        self.region = np.zeros((lat.R, lat.A))
        self.out = 0.0
        self.clock = 0.0

    @classmethod
    def anywhere(cls, lat: Lattice, share_view=1 / 3, share_regions=1 / 3) -> "Hidden":
        """Nothing known: in view (standing, its stay seen at a random time), behind a door, or out."""
        h = cls(lat)
        sh = lat.sh
        if lat.n:
            h.still += share_view / lat.n * sh.stay_prior(ongoing=True)[:, :, None]
        else:
            share_regions += share_view
        if lat.R:
            for r in range(lat.R):
                h.region[r] = share_regions / lat.R * lat.region_start(r)
            h.out = 1.0 - h.total()
        else:
            h.out = 1.0 - h.total()
        h.out = max(h.out, 0.0)
        h._normalize()
        return h

    @classmethod
    def at_place(cls, lat: Lattice, place: int) -> "Hidden":
        h = cls(lat)
        w = lat.world
        if place == OBSERVED and lat.n:
            h.still += 1.0 / lat.n * lat.sh.stay_prior(ongoing=True)[:, :, None]
        elif place == w.outside or not lat.R:
            h.out = 1.0
        else:
            h.region[place - 1] = lat.region_start(place - 1)
        h._normalize()
        return h

    def copy(self) -> "Hidden":
        h = Hidden.__new__(Hidden)
        h.lat = self.lat
        h.walk, h.still, h.region = self.walk.copy(), self.still.copy(), self.region.copy()
        h.out, h.clock = self.out, self.clock
        return h

    @staticmethod
    def mixture(parts) -> "Hidden":
        """sum_i w_i * density_i for [(w_i, Hidden)], weights summing to 1."""
        w0, h0 = parts[0]
        h = h0.copy()
        h.walk *= w0
        h.still *= w0
        h.region *= w0
        h.out *= w0
        for w, o in parts[1:]:
            h.walk += w * o.walk
            h.still += w * o.still
            h.region += w * o.region
            h.out += w * o.out
        return h

    def total(self) -> float:
        return float(self.walk.sum() + self.still.sum() + self.region.sum() + self.out)

    def _normalize(self) -> float:
        tot = self.total()
        if tot <= 0:
            return -math.inf
        self.walk /= tot
        self.still /= tot
        self.region /= tot
        self.out /= tot
        return math.log(tot)

    # ------------------------------------------------------------ motion

    def move(self, dt: float):
        """One step of the motion (MODEL.md 3.1, 3.2), all transitions as exact exponentials."""
        lat, m, sh = self.lat, self.lat.tr.m, self.lat.sh
        if lat.n:
            walk_all = self.walk.sum(axis=0)
            # walkers stop (the mean walk is walk_length long), standing people get up at their rate
            stop = walk_all * -math.expm1(-m.speed / m.walk_length * dt)
            up_l = self.still * -np.expm1(-sh.go * dt)[:, None, None]
            up = up_l.sum(axis=(0, 1))
            self.walk *= math.exp(-m.speed / m.walk_length * dt)
            self.still -= up_l
            # the detectability changes now and then within a stay (MODEL.md 4.1)
            q = -math.expm1(-m.kappa_switch * dt)
            self.still += q * (self.still.sum(axis=1, keepdims=True) * sh.kappa_w[None, :, None] - self.still)
            self.still += stop[None, None, :] * sh.stay_prior()[:, :, None]
            # walkers turn now and then; who gets up walks off in any direction
            turn = self.walk * -math.expm1(-m.turn_rate * dt)
            self.walk += (turn.sum(axis=0) + up)[None, :] / 8 - turn
            self.clock += dt
            while self.clock >= lat.tick:
                self.clock -= lat.tick
                self._advect()
        if lat.R:
            self._regions(dt)

    def _advect(self):
        """One tick: every walker one cell on along their heading; into a wall: turned round; into
        a region: there, the stay begins."""
        lat = self.lat
        new = np.zeros_like(self.walk)
        for h in range(8):
            mass = self.walk[h]
            go = mass * lat.step_share[h]
            new[h] += mass - go
            tgt = lat.target[h]
            to_cell = tgt >= 0
            np.add.at(new[h], tgt[to_cell], go[to_cell])
            blocked = tgt == -1
            new[(h + 4) % 8][blocked] += go[blocked]
            into = tgt <= -2
            if into.any():
                np.add.at(self.region[:, 0], -2 - tgt[into], go[into])
        self.walk = new

    def _regions(self, dt: float):
        lat, m = self.lat, self.lat.tr.m
        rates = lat.region_rates()
        ends = self.region * -np.expm1(-rates * dt)
        self.region -= ends
        for r in range(lat.R):
            out = float(ends[r].sum())
            doors = lat.doors[r]
            if not doors:
                self.region[r, 0] += out  # no way out drawn: the stay goes on
                continue
            for c, h in doors:
                self.walk[h, c] += out / len(doors)
        # the regions with the way out: leaving the house, coming home
        for r in lat.open:
            leave = self.region[r] * -math.expm1(-m.leave_rate * dt)
            self.region[r] -= leave
            self.out += float(leave.sum())
        ways = len(lat.open) + len(lat.entries)
        if ways and self.out > 0:
            come = self.out * -math.expm1(-m.arrive_rate * ways * dt)
            self.out -= come
            for r in lat.open:
                self.region[r, 0] += come / ways
            for c, h in lat.entries:
                self.walk[h, c] += come / ways
        # the stays age
        older = self.region[:, :-1] * -np.expm1(-dt / lat.widths[:-1])[None, :]
        self.region[:, :-1] -= older
        self.region[:, 1:] += older

    # ------------------------------------------------------------ evidence

    def weigh(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        """Multiply by a likelihood per cell: walkers f_walk (n,), still people f_still (n,) or per
        detectability (K, n)
        (outside the view: 1). Returns the log of the mass that is left (the factor
        for the hypotheses holding this person), and normalizes."""
        if not self.lat.n:
            return 0.0
        self.walk *= f_walk[None, :]
        self.still *= np.asarray(f_still)[..., None, :] if np.ndim(f_still) == 1 else f_still[None]
        return self._normalize()

    def integrate(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        """sum of density x f over the cells in view (f as in weigh)."""
        if not self.lat.n:
            return 0.0
        return float(self.walk.sum(axis=0) @ f_walk + (self.still.sum(axis=0) * f_still).sum())

    def distance(self, other: "Hidden") -> float:
        """L1 distance between two densities (for pairing exchangeable people, MODEL.md 5.6)."""
        return float(np.abs(self.walk - other.walk).sum() + np.abs(self.still - other.still).sum()
                     + np.abs(self.region - other.region).sum() + abs(self.out - other.out))

    def in_view(self) -> np.ndarray:
        """Mass per cell in view (n,)."""
        return self.walk.sum(axis=0) + self.still.sum(axis=(0, 1))

    def walking(self) -> np.ndarray:
        return self.walk.sum(axis=0)

    def places(self) -> np.ndarray:
        """Probability per place (observed, regions..., outside)."""
        return np.concatenate([[self.in_view().sum()], self.region.sum(axis=1), [self.out]])
