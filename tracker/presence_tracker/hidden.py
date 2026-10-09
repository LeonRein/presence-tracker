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

A known person here is a Bernoulli (Garcia-Fernandez et al. 2018, eq. 31): with the probability r
the person exists (MODEL.md 5.5), and then the density below. r < 1 comes from hypotheses that differ
only in whether a track was a person (merged, Tracker._merged); evidence weighs it like the density
(weigh). What is read from it (in_view, walking, places, integrate) is times r.

State of an unseen person, as masses that sum to 1:
  walk[c]          walking in tile c: walkers spread to the neighbouring tiles and through doors into
                   the regions behind them or out of the house (the diffusion limit of MODEL.md 3.2,
                   tiling.py)
  still[l, k, c]   standing or sitting in tile c, with the stay's rate of getting up go_l and its
                   detectability kappa_k (filtermodel.Shapes, MODEL.md 3.1, 4.1)
  amp[k, a, c]     the LD2410C amplitude of the stay (MODEL.md 4.3): P(level a of g | still, kappa_k,
                   tile c) on the levels of the grid of a person with a track and g = 1 (the last): a
                   stay measured with a track keeps its posterior (Swerling III), one that nothing
                   measured has g = 1; a conditional, not a mass (sums to 1 over a)
  region[r, a]     behind a door in region r, for a time in age bin a (the stay ends by its hazard)
  out              out of the house

Undetected (below) is the same as an intensity: the people nobody knows of yet (MODEL.md 3.4, 5.5), a
Poisson point process - the undetected part of a PMBM.
"""

import base64
import math
import zlib

import numpy as np

from . import kernels

SAVE_CUT = 1e-6  # shares of a density below this are saved as 0 (Hidden.to_dict)
AGE_EDGES = np.concatenate([[0.0], 2.0 ** np.arange(1, 18)])  # s, region stay ages: 2 s ... 36 h


def amp_prior(tiles) -> np.ndarray:
    """(K, A + 1, n) the amplitude of a stay nothing measured, in every tile: g = 1, the last level
    (filtermodel.Shapes.tile_fresh). Shared: copy before changing it."""
    hit = getattr(tiles, "_amp_prior", None)
    if hit is None or hit.shape[2] != tiles.n:
        hit = tiles._amp_prior = np.ascontiguousarray(
            np.repeat(tiles.sh.tile_fresh[:, :, None], tiles.n, axis=2))
        hit.setflags(write=False)
    return hit


_NO_KEEP = np.ones((1, 1))  # hidden_stays without a measured amplitude (not used)
MEASURED_CUT = 1e-6  # a share of measured amplitude below this everywhere: nothing measured (Hidden.amp)


class Hidden:
    """The density of one person without a track (module doc). Objects are shared between
    hypotheses and changed only by what applies to all of them (motion, no track started); what
    differs between hypotheses makes a new object."""

    __slots__ = ("tiles", "walk", "still", "_amp", "_keep", "region", "out", "clock", "r")

    def __init__(self, tiles):
        self.tiles = tiles
        self.walk = np.zeros(tiles.n)
        self.still = np.zeros((len(tiles.sh.go), len(tiles.sh.kappa), tiles.n))
        self._amp = None  # nothing measured: g = 1 everywhere (amp_prior)
        self._keep = None
        self.region = np.zeros((tiles.R, tiles.A))
        self.out = 0.0
        self.clock = 0.0
        self.r = 1.0  # probability that the person exists (an intensity: always 1)

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
        h._amp = None if self._amp is None else self._amp.copy()
        h._keep = None if self._keep is None else self._keep.copy()
        h.out, h.clock, h.r = self.out, self.clock, self.r
        return h

    def to_dict(self) -> dict:
        """For saving (Tracker.people_state): the masses as shares of the whole in float16 (3
        digits), shares below SAVE_CUT as 0, zlib, base64: 6 kB for a person instead of 80 kB as
        float32 (7.10. 08:06, 9 densities). Over all 18 000 masses of a person the rounding stays
        below 5e-4."""
        tot = self.total()
        if not (math.isfinite(tot) and min(self.walk.min(initial=0), self.still.min(initial=0),
                                           self.region.min(initial=0), self.out) >= 0):
            raise ValueError("a density that is no density")
        scale = tot if tot > 0 else 1.0

        def pack(a):
            a = a / scale
            a = np.where(a < SAVE_CUT, 0.0, a).astype("<f2")
            return base64.b64encode(zlib.compress(a.tobytes(), 9)).decode()
        amp = {} if not self.measured else {"amp": base64.b64encode(zlib.compress(self.amp.astype("<f2").tobytes(), 9)).decode()}
        return {"walk": pack(self.walk), "still": pack(self.still), "region": pack(self.region), **amp,
                "out": float(self.out), "scale": float(scale), "r": float(self.r)}

    @classmethod
    def from_dict(cls, tiles, d: dict) -> "Hidden":
        """The inverse of to_dict, on tiles of the same fingerprint; ValueError if it does not fit."""
        h = cls(tiles)
        scale = float(d["scale"])
        if not (math.isfinite(scale) and scale > 0):
            raise ValueError("saved density is broken")

        def unpack(s, like):
            a = np.frombuffer(zlib.decompress(base64.b64decode(s)), dtype="<f2").astype(float) * scale
            if a.size != like.size or not np.all(np.isfinite(a)) or (a < 0).any():
                raise ValueError("saved density does not fit the tiles")
            return a.reshape(like.shape)
        h.walk, h.still, h.region = unpack(d["walk"], h.walk), unpack(d["still"], h.still), unpack(d["region"], h.region)
        if "amp" in d:  # the amplitudes of the stays (without: nothing measured, g = 1)
            a = np.frombuffer(zlib.decompress(base64.b64decode(d["amp"])), dtype="<f2").astype(float)
            if a.size != h.amp.size or not np.all(np.isfinite(a)) or (a < 0).any():
                raise ValueError("saved amplitudes do not fit the tiles")
            a = a.reshape(h.amp.shape)
            tot = a.sum(axis=1, keepdims=True)
            h.amp = np.where(tot > 0, a / np.where(tot > 0, tot, 1.0), amp_prior(tiles))
        h.out = float(d["out"])
        h.r = float(d.get("r", 1.0)) if cls is Hidden else 1.0
        if (not (math.isfinite(h.out) and h.out >= 0) or not 0.0 <= h.r <= 1.0
                or (cls is Hidden and not h.total() > 0 and h.r > 0)):
            raise ValueError("saved density is empty or broken")  # a person is somewhere; nobody unknown may be
        h._normalize()  # a person's masses sum to 1
        return h

    @property
    def amp(self) -> np.ndarray:
        """(K, A + 1, n) P(level of the amplitude | still, detectability, tile): the levels of the
        grid (a stay measured with a track) and g = 1 (nothing measured). Moving on only mixes it with
        g = 1 (fresh stays, the detectability drawn anew): kept as
        the share that is no fresh draw (_keep, kernels.hidden_stays) and applied when it is read -
        exact, as every step mixes towards the same prior."""
        if self._amp is None:
            return amp_prior(self.tiles)
        if self._keep is not None:
            w = self._keep[:, None, :]
            self._amp = w * self._amp + (1.0 - w) * self.tiles.sh.tile_fresh[:, :, None]
            self._keep = None
            if 1.0 - float(self._amp[:, -1, :].min()) < MEASURED_CUT:
                self._amp = None
                return amp_prior(self.tiles)
        return self._amp

    @amp.setter
    def amp(self, value: np.ndarray):
        self._amp = value
        self._keep = None

    @property
    def measured(self) -> bool:
        """Whether any stay of this person carries an amplitude measured with a track (else g = 1)."""
        if self._keep is not None:
            self.amp  # what moving on mixed in (it may leave nothing measured)
        return self._amp is not None

    @staticmethod
    def mixture(parts) -> "Hidden":
        """sum_i w_i * Bernoulli_i for [(w_i, Hidden)], weights summing to 1: exists with sum_i w_i
        r_i, then the density sum_i w_i r_i density_i / that. A part with r_i = 0 (nobody: a
        hypothesis without this person, Tracker._merged) adds only to not existing."""
        r = sum(w * o.r for w, o in parts)
        if not r > 0:
            h = parts[0][1].copy()
            h.r = 0.0
            return h
        h = None
        num = None
        measured = any(o.measured for w, o in parts if w * o.r > 0)
        for w, o in parts:
            k = w * o.r / r
            if k <= 0:
                continue
            if measured:  # the amplitudes mix with the still mass they belong to
                part = k * o.still.sum(axis=0)[:, None, :] * o.amp
                num = part if num is None else num + part
            if h is None:
                h = o.copy()
                h.scale(k)
                continue
            h.walk += k * o.walk
            h.still += k * o.still
            h.region += k * o.region
            h.out += k * o.out
        h._amp, h._keep = None, None
        if measured:
            h._set_amp(num)
        h.r = min(r, 1.0)
        return h

    def _set_amp(self, num):
        """amp from sum_i (still mass_i (K, n)) x amp_i, divided by the still mass now (where there is
        none: the prior)."""
        S = self.still.sum(axis=0)[:, None, :]
        self.amp = np.where(S > 0, num / np.where(S > 0, S, 1.0), amp_prior(self.tiles))

    @classmethod
    def nobody(cls, tiles) -> "Hidden":
        """A person who does not exist (r = 0): the partner of a known person in a hypothesis that
        lacks them, when hypotheses are merged (Tracker._merged)."""
        h = cls.at_place(tiles, len(tiles.world.places) - 1)
        h.r = 0.0
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
            measured = self._amp is not None
            if measured and self._keep is None:
                self._keep = np.ones((len(sh.kappa), tl.n))
            kernels.hidden_stays(self.walk, self.still, self._keep if measured else _NO_KEEP, -math.expm1(-m.speed / m.walk_length * dt),
                                 -np.expm1(-sh.go * dt), -math.expm1(-m.kappa_switch * dt), sh.kappa_w, sh.go_w,
                                 measured)
            self.clock += dt
            while self.clock >= tl.tick:
                self.clock -= tl.tick
                gone = tl.T_in @ self.walk
                self.region[:, 0] += gone[:tl.R]
                self.out += gone[tl.R]
                self.walk = tl.T_walk @ self.walk
        if tl.R or tl.ways:
            self._regions(dt)
        self._arrive(dt)

    def leap(self, dt: float):
        """The motion over a step dt much longer than a walk (a data gap of minutes to days, MODEL.md
        5.3): standing people get up as in move; every walk is taken whole, wherever it ends
        (Tiling.walk_end) - stopped in a tile, beginning a new stay, gone into a region or out. What is
        lost: a short stay begun within the step ends no earlier than the next step (fresh stays
        last 26 min on average; the slow kinds of stay make that). The last minutes of a gap are
        moved with move, so the fast parts (walking, short stays) are as in move afterwards."""
        tl = self.tiles
        m, sh = tl.tr.m, tl.sh
        if tl.n:
            up = -np.expm1(-sh.go * dt)
            s = self.still.sum(axis=1)
            rise = up[:, None] * s
            q = -math.expm1(-m.kappa_switch * dt)
            self.still *= ((1 - up) * (1 - q))[:, None, None]
            kept = self.still.sum(axis=0)  # (K, n): with their own amplitude
            self.still += sh.kappa_w[None, :, None] * (q * (s - rise))[:, None, :]
            self.walk += rise.sum(axis=0)
        if tl.R or tl.ways:
            self._regions(dt)
        self._arrive(dt)
        if tl.n:
            end = tl.walk_end() @ self.walk
            self.walk = np.zeros(tl.n)
            self.still += sh.stay_prior()[:, :, None] * end[None, None, :tl.n]
            # the stays begun in the step (the detectability anew, walks ended): nothing measured
            if self.measured:
                self._set_amp(kept[:, None, :] * self.amp + (self.still.sum(axis=0) - kept)[:, None, :]
                              * sh.tile_fresh[:, :, None])
            self.region[:, 0] += end[tl.n:tl.n + tl.R]
            self.out += end[tl.n + tl.R]

    def _arrive(self, dt: float):
        """Newcomers (only the unknown people have them)."""

    def _regions(self, dt: float):
        tl = self.tiles
        m = tl.tr.m
        # stays end (through one of the region's doors: walking into view, or out of the house; without
        # any the stay goes on); coming home by the ways in; the stays age
        self.out = kernels.hidden_regions(self.region, self.walk, self.out, -np.expm1(-tl.region_rates() * dt),
                                          tl.door_ptr, tl.door_list, tl.exits, tl.entry_idx,
                                          -math.expm1(-m.arrive_rate * tl.ways * dt), -np.expm1(-dt / tl.widths[:-1]))

    # ------------------------------------------------------------ evidence

    def _times(self, f_walk, f_still):
        self.walk *= f_walk
        self.still *= f_still if np.ndim(f_still) == 2 else np.asarray(f_still)[None, :]

    def weigh(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        """Multiply by a likelihood per tile: walkers f_walk (n,), still people f_still (n,) or per
        detectability (K, n) (outside the view: 1; not existing: 1). Returns the log of the factor
        for the hypotheses holding this person, 1 - r + r * (mass that is left), and normalizes:
        the Bernoulli update of Garcia-Fernandez et al. 2018 eq. 31 (r = 1: the mass that is left)."""
        if not self.tiles.n:
            return 0.0
        self._times(f_walk, f_still)
        q = self._normalize()
        if self.r >= 1.0:
            return q
        if q == -math.inf:  # nothing left where the person could be: they do not exist
            r, self.r = self.r, 0.0
            return math.log1p(-r)
        if q > 0:  # in the form that cannot overflow (q is at most ld2410.LOG_CAP + log n today)
            z = self.r + (1.0 - self.r) * math.exp(-q)
            self.r = self.r / z
            return q + math.log(z)
        z = 1.0 - self.r + self.r * math.exp(q)
        self.r = self.r * math.exp(q) / z
        return math.log(z)

    def integrate(self, f_walk: np.ndarray, f_still: np.ndarray) -> float:
        """sum of r x density x f over the tiles in view (f as in weigh)."""
        if not self.tiles.n:
            return 0.0
        return self.r * float(self.walk @ f_walk + (self.still.sum(axis=0) * f_still).sum())

    def distance(self, other: "Hidden") -> float:
        """L1 distance between two Bernoullis, r x density (for pairing exchangeable people, MODEL.md
        5.6)."""
        a, b = self.r, other.r
        return float(np.abs(a * self.walk - b * other.walk).sum() + np.abs(a * self.still - b * other.still).sum()
                     + np.abs(a * self.region - b * other.region).sum() + abs(a * self.out - b * other.out))

    def in_view(self) -> np.ndarray:
        """Expected number per tile in view (n,): r x the mass."""
        return self.r * (self.walk + self.still.sum(axis=(0, 1)))

    def walking(self) -> np.ndarray:
        return self.r * self.walk

    def places(self) -> np.ndarray:
        """Probability per place (observed, regions..., outside); 1 - r: nowhere (does not exist)."""
        return self.r * np.concatenate([[(self.walk.sum() + self.still.sum())], self.region.sum(axis=1), [self.out]])


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
        """A person given up on (almost surely out of the house or not existing) joins the unknown
        ones with r x their density."""
        self.add_scaled(person, 1.0)

    def add_scaled(self, other: Hidden, k: float):
        """+ k x r x the density of other (a person, or another intensity: r = 1)."""
        k = k * other.r
        measured = self.measured or (other.measured and k > 0)
        if measured:
            num = self.still.sum(axis=0)[:, None, :] * self.amp + k * other.still.sum(axis=0)[:, None, :] * other.amp
        self.walk += k * other.walk
        self.still += k * other.still
        if measured:
            self._set_amp(num)
        self.region += k * other.region
        self.out += k * other.out

    def _arrive(self, dt: float):
        tl, m = self.tiles, self.tiles.tr.m
        self.out *= math.exp(-m.forget_rate * dt)
        if tl.ways:
            come = m.guest_rate * dt
            self.region[:, 0] += come * tl.exits
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
