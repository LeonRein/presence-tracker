"""People without a measuring track (MODEL.md 5.3): their density over the tiles (tiling.py), exact
sums instead of samples.

Who has no measuring track is spread widely - over the rooms in view, behind doors, out of the
house. Particles would hold such a spread only with a few samples at any spot, and whether a new
track comes from them would hang on those few (5.10., 16:22: 4800 particles, the weight of "a person"
over "a ghost" scattered by a factor 10^5 between seeds). A point-mass filter (Bucy & Senne 1971,
Bergman 1999) holds the density on a raster instead - here on tiles, pieces of the rooms in which the
sensors see about equally well (0.8 held a 0.2 m raster with 8 walking directions: four times the
computing time of 0.6.21). In a PMBM filter it is the "undetected" part (Williams 2015,
Garcia-Fernandez et al. 2018), here one density per person. A person whose tracks are all held lies
here too.

State of an unseen person, as masses that sum to 1:
  walk[c]          walking in tile c: walkers spread to the neighbouring tiles and through doors into
                   the regions behind them (the diffusion limit of MODEL.md 3.2, tiling.py)
  still[l, k, c]   standing or sitting in tile c, with the stay's rate of getting up go_l and its
                   detectability kappa_k (filtermodel.Shapes, MODEL.md 3.1, 4.1)
  region[r, a]     behind a door in region r, for a time in age bin a (the stay ends by its hazard)
  out              out of the house

Undetected (below) is the same as an intensity: the people nobody knows of yet (MODEL.md 3.4, 5.5), a
Poisson point process - the undetected part of a PMBM.
"""

import math

import numpy as np

from . import kernels

AGE_EDGES = np.concatenate([[0.0], 2.0 ** np.arange(1, 18)])  # s, region stay ages: 2 s ... 36 h


class Hidden:
    """The density of one person without a track (module doc). Objects are shared between
    hypotheses and changed only by what applies to all of them (motion, no track started); what
    differs between hypotheses makes a new object."""

    __slots__ = ("tiles", "walk", "still", "region", "out", "clock")

    def __init__(self, tiles):
        self.tiles = tiles
        self.walk = np.zeros(tiles.n)
        self.still = np.zeros((len(tiles.sh.go), len(tiles.sh.kappa), tiles.n))
        self.region = np.zeros((tiles.R, tiles.A))
        self.out = 0.0
        self.clock = 0.0

    @classmethod
    def anywhere(cls, tiles, share_view=1 / 3, share_regions=1 / 3) -> "Hidden":
        """Nothing known: in view (standing anywhere alike, its stay seen at a random time), behind
        a door, or out."""
        h = cls(tiles)
        if tiles.n:
            h.still += share_view * tiles.sh.stay_prior(ongoing=True)[:, :, None] * (tiles.area / tiles.area.sum())[None, None, :]
        else:
            share_regions += share_view
        for r in range(tiles.R):
            h.region[r] = share_regions / tiles.R * tiles.region_start(r)
        h.out = max(1.0 - h.total(), 0.0)
        h._normalize()
        return h

    @classmethod
    def at_place(cls, tiles, place: int) -> "Hidden":
        h = cls(tiles)
        w = tiles.world
        if place == 0 and tiles.n:
            h.still += tiles.sh.stay_prior(ongoing=True)[:, :, None] * (tiles.area / tiles.area.sum())[None, None, :]
        elif place == w.outside or not tiles.R:
            h.out = 1.0
        else:
            h.region[place - 1] = tiles.region_start(place - 1)
        h._normalize()
        return h

    def copy(self) -> "Hidden":
        h = type(self).__new__(type(self))
        h.tiles = self.tiles
        h.walk, h.still, h.region = self.walk.copy(), self.still.copy(), self.region.copy()
        h.out, h.clock = self.out, self.clock
        return h

    @staticmethod
    def mixture(parts) -> "Hidden":
        """sum_i w_i * density_i for [(w_i, Hidden)], weights summing to 1."""
        w0, h0 = parts[0]
        h = h0.copy()
        h.scale(w0)
        for w, o in parts[1:]:
            h.walk += w * o.walk
            h.still += w * o.still
            h.region += w * o.region
            h.out += w * o.out
        return h

    def scale(self, k: float):
        self.walk *= k
        self.still *= k
        self.region *= k
        self.out *= k

    def total(self) -> float:
        return float(self.walk.sum() + self.still.sum() + self.region.sum() + self.out)

    def _normalize(self) -> float:
        tot = self.total()
        if tot <= 0:
            return -math.inf
        self.scale(1.0 / tot)
        return math.log(tot)

    # ------------------------------------------------------------ motion

    def move(self, dt: float):
        """One step of the motion (MODEL.md 3.1, 3.2), all transitions as exact exponentials;
        walking on between the tiles in ticks."""
        tl = self.tiles
        m, sh = tl.tr.m, tl.sh
        if tl.n:
            # walkers stop (the mean walk is walk_length long), standing people get up at their rate;
            # who stays: the detectability changes now and then within a stay (MODEL.md 4.1), drawn
            # anew from kappa_w; who stops begins a fresh stay (its kind from go_w, its
            # detectability from kappa_w: Shapes.stay_prior)
            kernels.hidden_stays(self.walk, self.still, -math.expm1(-m.speed / m.walk_length * dt), -np.expm1(-sh.go * dt),
                                 -math.expm1(-m.kappa_switch * dt), sh.kappa_w, sh.go_w)
            self.clock += dt
            while self.clock >= tl.tick:
                self.clock -= tl.tick
                self.region[:, 0] += tl.T_in @ self.walk
                self.walk = tl.T_walk @ self.walk
        if tl.R:
            self._regions(dt)

    def _regions(self, dt: float):
        tl = self.tiles
        m = tl.tr.m
        # stays end (out at a door of the region, walking; without one the stay goes on); from the
        # regions with the way out leaving the house, coming home; the stays age
        ways = len(tl.open) + len(tl.entries)
        self.out = kernels.hidden_regions(self.region, self.walk, self.out, -np.expm1(-tl.region_rates() * dt),
                                          tl.door_ptr, tl.door_list, tl.open_idx, tl.entry_idx,
                                          -math.expm1(-m.leave_rate * dt), -math.expm1(-m.arrive_rate * ways * dt),
                                          -np.expm1(-dt / tl.widths[:-1]))

    # ------------------------------------------------------------ evidence

    def _times(self, f_walk, f_still):
        self.walk *= f_walk
        self.still *= f_still if np.ndim(f_still) == 2 else np.asarray(f_still)[None, :]

    def weigh(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        """Multiply by a likelihood per tile: walkers f_walk (n,), still people f_still (n,) or per
        detectability (K, n) (outside the view: 1). Returns the log of the mass that is left (the
        factor for the hypotheses holding this person), and normalizes."""
        if not self.tiles.n:
            return 0.0
        self._times(f_walk, f_still)
        return self._normalize()

    def integrate(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        """sum of density x f over the tiles in view (f as in weigh)."""
        if not self.tiles.n:
            return 0.0
        return float(self.walk @ f_walk + (self.still.sum(axis=0) * f_still).sum())

    def distance(self, other: "Hidden") -> float:
        """L1 distance between two densities (for pairing exchangeable people, MODEL.md 5.6)."""
        return float(np.abs(self.walk - other.walk).sum() + np.abs(self.still - other.still).sum()
                     + np.abs(self.region - other.region).sum() + abs(self.out - other.out))

    def in_view(self) -> np.ndarray:
        """Mass per tile in view (n,)."""
        return self.walk + self.still.sum(axis=(0, 1))

    def walking(self) -> np.ndarray:
        return self.walk

    def places(self) -> np.ndarray:
        """Probability per place (observed, regions..., outside)."""
        return np.concatenate([[self.in_view().sum()], self.region.sum(axis=1), [self.out]])


class Undetected(Hidden):
    """The people nobody knows of (MODEL.md 3.4, 5.5): a Poisson point process over the tiles, its
    intensity in the same arrays as a Hidden person's density but not normalized - the masses are
    expected numbers of people. Everything about motion is linear and the same as for a person;
    on top, newcomers arrive at the ways in, and people out of the house are forgotten now and then
    (whoever comes back after that is a newcomer). Evidence weighs it by the Poisson void
    probability: P(none of them did it) = exp(-(mass before - mass after))
    (Garcia-Fernandez et al. 2018, eq. 18-24)."""

    __slots__ = ()

    @classmethod
    def anywhere(cls, tiles, people: float = 1.0, **kw) -> "Undetected":
        h = super().anywhere(tiles, **kw)
        h.scale(people)
        return h

    @classmethod
    def none(cls, tiles) -> "Undetected":
        return cls(tiles)

    def add(self, person: Hidden):
        """A person given up on (almost surely out of the house) joins the unknown ones."""
        self.walk += person.walk
        self.still += person.still
        self.region += person.region
        self.out += person.out

    def move(self, dt: float):
        super().move(dt)
        tl, m = self.tiles, self.tiles.tr.m
        self.out *= math.exp(-m.forget_rate * dt)
        if len(tl.open) + len(tl.entries):
            come = m.guest_rate * dt
            for r in tl.open:
                self.region[r, 0] += come
            for c in tl.entries:
                self.walk[c] += come

    def weigh(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        if not self.tiles.n:
            return 0.0
        before = float(self.walk.sum() + self.still.sum())
        self._times(f_walk, f_still)
        return -(before - float(self.walk.sum() + self.still.sum()))

    def _normalize(self) -> float:
        return 0.0
