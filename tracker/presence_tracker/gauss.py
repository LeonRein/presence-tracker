"""A person in view with a measuring track, or the source of a ghost track (MODEL.md 5.2): a mixture
of two Gaussians, one per mode - standing (row 0) and walking (row 1) - each a Kalman filter over
position x, velocity v and, per track of theirs, the track's offset on them (constant c,
wandering o), with z = x + c + o + w. Between the modes the mixture is kept at two components by
moment matching (GPB / IMM, Saerkkae & Svensson 2023 p. 352; Li 2019 eq. 26-33). Nothing is drawn:
getting up is a component with a small weight that the first measurement of a walk lifts.

A person also has a part "gone through a door" into a region without a sensor (a density over the
tiles and regions, hidden.py, with its weight a): while their tracks are held, walkers move on, and
the share that walking takes through a door (the rates of the tiles at the doors, tiling.py) goes
there. A measurement of their track says they are in view: the part is dropped (coming back to
exactly that spot is neglected). When their last track ends, both parts become one density over the
tiles.

Per axis its own covariance (the moments of a mixture differ between the axes). The standing
component also holds the probability of each kind of stay (MODEL.md 3.1) and of each level of the
stay's detectability (4.1), independent of each other.

Objects are shared between hypotheses and changed in place only by what applies to all of them;
what differs between hypotheses makes a new object (copy first).
"""

import math

import numpy as np

from . import kernels
from .hidden import Hidden
from .filtermodel import STILL, WALK
from .world import CELL

LOG2PI = math.log(2 * math.pi)
X, V = 0, 1  # state indices; a track's c and o follow at slots[seg], slots[seg] + 1

# Sigma points of a component's position (MODEL.md 5.2): what depends on where the person is
# (the rates of new tracks, not being tracked, the sensors' sight) is its expectation over the
# component's distribution, not its value at the mean. The unscented transform of the 2-D position
# (diagonal covariance) with n + kappa = 3: the centre with 1/3 and +-sqrt(3) sigma along each axis
# with 1/6 each, exact for the moments up to the fourth per axis (Saerkkae & Svensson 2023,
# eq. 8.70-8.71; the 1-D Gauss-Hermite rule of order 3 per axis, alg. 8.5).
UNIT = math.sqrt(3.0) * np.array([[0.0, 0.0], [1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0]])
UNIT_W = np.array([1 / 3, 1 / 6, 1 / 6, 1 / 6, 1 / 6])
UNIT_REACH = math.sqrt(3.0)  # the farthest unit point from the centre
# a position cut to a sensor's sight (Gauss.in_sight): Gauss-Hermite points of N(0, 1) per axis, their
# product grid; a cut keeps at least 5 cm of spread per axis (or what it had)
_GH, _GW = np.polynomial.hermite_e.hermegauss(5)
SIGHT_GRID = np.array([(a, b) for a in _GH for b in _GH])
SIGHT_W = np.array([wa * wb for wa in _GW for wb in _GW]) / (_GW.sum() ** 2)
SIGHT_MIN_VAR = 0.05 ** 2


def _collapse(parts):
    """Moment matching of weighted Gaussians [(w, mean (2, d), cov (2, d, d))] (per axis)."""
    tot = sum(w for w, _, _ in parts)
    if tot <= 0:
        _, m, P = parts[0]
        return 0.0, m.copy(), P.copy()
    m = sum(w * mm for w, mm, _ in parts) / tot
    P = np.zeros_like(parts[0][2])
    for w, mm, PP in parts:
        if w <= 0:
            continue
        e = mm - m
        P += w * (PP + e[:, :, None] * e[:, None, :])
    return tot, m, P / tot


_NO_SLOTS = (np.zeros(0, dtype=np.int64), np.zeros(0))
# kernels.sync_step's use and sensors when it does not weigh (no sensor live)
_NO_RATES = (np.zeros(1, dtype=np.bool_), np.zeros((1, 2)), np.zeros((1, 2, 2)), np.zeros((0, 3)),
             np.zeros(2, dtype=np.int64), np.zeros(4), 0.0, 1.0, np.ones(2), 1.0)
_ARGS = {}  # (tiles, shapes) -> step_args
_PARAMS = [None, None, None]  # (model, dt, step_params): the same step for most Gaussians of a move


def step_args(tiles, shapes) -> tuple:
    """What kernels.sync_step takes that stays as it is: the walls and the door tiles (centres,
    spreads, areas, rates), the sigma points, an empty part gone through a door (its region filled
    when one is made), factors 1."""
    key = (tiles, shapes)
    hit = _ARGS.get(key)
    if hit is None:
        if tiles is not None:
            w = tiles.world
            t = tiles.door_tiles
            doors = (tiles.centers[t], tiles.var[t], tiles.area[t], tiles.door_rate[t])
            geo = (w.walls, w.wall_box, w.wall_grid, float(w.x0), float(w.y0), CELL,
                   *(np.ascontiguousarray(a, dtype=float) for a in doors))
            n, R, A = tiles.n, tiles.R, tiles.A
        else:
            geo = (np.zeros((0, 4)), np.zeros((0, 4)), np.zeros((1, 1)), 0.0, 0.0, CELL, np.zeros((0, 2)),
                   np.zeros((0, 2)), np.zeros(0), np.zeros((0, 1)))
            n, R, A = 0, 0, 1
        sig = (UNIT, UNIT_W, UNIT_REACH, np.ascontiguousarray(shapes.kappa, dtype=float))
        scratch = (np.zeros(n), np.zeros((len(shapes.go), len(shapes.kappa), n)), np.zeros((R, A)), 0.0, 1.0)
        hit = _ARGS[key] = (geo, sig, scratch, (np.ones(n), np.ones((1, n))))
    return hit


def step_params(m, dt: float) -> np.ndarray:
    """kernels.sync_step's parameters of a step dt: dt, p_stop, the walkers' stationary velocity
    variance per axis, the standing position's diffusion, the velocity's correlation time, the decay
    of the tracks' wandering offsets, the detectability's switch, const_share."""
    if _PARAMS[0] is not m or _PARAMS[1] != dt:
        _PARAMS[:] = [m, dt, np.array([dt, -math.expm1(-m.speed / m.walk_length * dt), 0.5 * (m.speed ** 2 + m.speed_spread ** 2),
                                       m.still_noise ** 2, 1.0 / m.turn_rate, math.exp(-dt / m.tau),
                                       -math.expm1(-m.kappa_switch * dt), m.const_share])]
    return _PARAMS[2]


def _max2(a: float, b: float) -> float:
    """numpy's max of two (NaN wins)."""
    return a if a >= b or a != a else b


def _log(w):
    with np.errstate(divide="ignore"):
        return np.log(w)


class Gauss:
    __slots__ = ("logw", "mean", "cov", "gow", "ka", "slots", "var", "phantom", "a", "away", "t")

    def __init__(self, logw, mean, cov, gow, ka, slots=None, var=None, phantom=False, a=0.0, away=None, t=None):
        self.logw = np.asarray(logw, dtype=float)  # (2,) log weights of [STILL, WALK], normalized
        self.mean = mean  # (2 modes, 2 axes, d)
        self.cov = cov  # (2 modes, 2 axes, d, d)
        self.gow = gow  # (L,) probability of each kind of stay (for the standing component)
        # (K, A) probability of each level of detectability and of the LD2410C amplitude together (for
        # the standing component, MODEL.md 4.1, 4.3; filtermodel.Shapes.ka_w)
        self.ka = ka
        self.slots = dict(slots or {})  # track -> index of its c (o follows)
        self.var = dict(var or {})  # track -> variance of its offset per axis
        self.phantom = phantom  # the source of a ghost track, not a person
        self.a = a  # weight of the part gone through a door
        self.away = away  # ... its density (hidden.Hidden), or None
        self.t = t  # the time it was moved to (None: made at the tracker's last move of everybody)

    def copy(self) -> "Gauss":
        return Gauss(self.logw.copy(), self.mean.copy(), self.cov.copy(), self.gow.copy(), self.ka.copy(), self.slots,
                     self.var, self.phantom, self.a, self.away.copy() if self.away is not None else None, self.t)

    @property
    def kw(self) -> np.ndarray:
        """(K,) probability of each level of detectability."""
        return self.ka.sum(axis=1)

    @property
    def amw(self) -> np.ndarray:
        """(A,) probability of each level of the LD2410C amplitude."""
        return self.ka.sum(axis=0)

    @property
    def segs(self) -> set:
        return set(self.slots)

    def weights(self) -> np.ndarray:
        """The components' weights (summing to 1; the part gone through a door aside). The two
        numbers as Python floats (numpy's max and sum of two, numpy's exp: the same numbers)."""
        l0, l1 = self.logw.tolist()
        top = _max2(l0, l1)
        if top == -math.inf:
            return np.array([0.5, 0.5])
        w0, w1 = np.exp(self.logw - top).tolist()
        s = 0.0 + w0 + w1
        return np.array([w0 / s, w1 / s])

    def reweigh(self, dlog, d_away: float = 0.0) -> float:
        """Multiply the components' weights by exp(dlog) and the part gone through a door by
        exp(d_away); returns the log of the factor of the whole person (for the hypotheses) and
        normalizes."""
        lw = self.logw + dlog
        top = _max2(*lw.tolist())
        if top > -math.inf:
            e0, e1 = np.exp(lw - top).tolist()
            comps = top + math.log(0.0 + e0 + e1)
        else:
            comps = -math.inf
        if self.a <= 0:
            if comps == -math.inf:
                return -math.inf
            self.logw = lw - comps
            return comps
        parts = np.array([math.log1p(-self.a) + comps if self.a < 1 else -math.inf, math.log(self.a) + d_away])
        total = float(np.logaddexp(*parts))
        if total == -math.inf:
            return -math.inf
        self.a = math.exp(parts[1] - total)
        if comps > -math.inf:
            self.logw = lw - comps
        return total

    # ------------------------------------------------------------ views

    @property
    def pos(self) -> np.ndarray:
        """(2 modes, 2) mean position per component."""
        return self.mean[:, :, X]

    def pos_var(self) -> np.ndarray:
        """(2 modes, 2 axes) variance of the position."""
        return self.cov[:, :, X, X]

    def walking(self) -> float:
        return float(self.weights()[WALK])

    def sigma(self, world=None, mean=None, var=None) -> np.ndarray:
        """(2 modes, P, 2) the sigma points of each component's position (weights UNIT_W), or of
        N(mean, diag var) given per mode (2, 2); with the world: those across a wall seen from the
        component's mean mirrored at it (walls reflect, MODEL.md 3.2)."""
        pos = self.pos
        mean = pos if mean is None else mean
        var = self.pos_var() if var is None else var
        if world is None:
            sd = np.sqrt(np.maximum(var, 0.0))
            return mean[:, None, :] + UNIT[None, :, :] * sd[:, None, :]
        # only components with a wall within reach of their points (from their mean): kernels.sigma_points
        return kernels.sigma_points(np.ascontiguousarray(pos), np.ascontiguousarray(mean, dtype=float),
                                    np.ascontiguousarray(var, dtype=float), UNIT, UNIT_REACH, world.wall_grid, world.x0,
                                    world.y0, CELL, world.walls, world.wall_box)

    def near_sigma(self, z, radius: float, world=None) -> tuple:
        """E[exp(-|x - z|^2 / (2 radius^2))] per component (2,) and the sigma points (2, P, 2) of
        the position weighted by that kernel (a Gaussian again: z as a measurement with variance
        radius^2) - for the expectation of something else times the kernel."""
        if world is not None:  # in compiled loops (kernels.near_parts), the same numbers
            zx, zy = np.asarray(z, dtype=float).tolist()
            expo, sq, pts = kernels.near_parts(np.ascontiguousarray(self.pos), np.ascontiguousarray(self.pos_var()), zx, zy,
                                               float(radius ** 2), UNIT, UNIT_REACH, world.wall_grid, world.x0, world.y0,
                                               CELL, world.walls, world.wall_box)
            return kernels.near_weight(sq, np.exp(expo)), pts
        P, r2 = self.pos_var(), radius ** 2
        z = np.asarray(z)[None, :]
        mean = (self.pos * r2 + z * P) / (P + r2)
        return self.near(z[0], radius), self.sigma(world, mean, P * r2 / (P + r2))

    def near(self, z, radius: float) -> np.ndarray:
        """(2,) E[exp(-|x - z|^2 / (2 radius^2))] per component."""
        v = radius ** 2 + self.pos_var()
        d2 = (self.pos - np.asarray(z)[None, :]) ** 2
        return np.prod(np.sqrt(radius ** 2 / v) * np.exp(-0.5 * d2 / v), axis=1)

    # ------------------------------------------------------------ dynamics

    def predict(self, dt: float, m, shapes, tiles=None):
        """One step of MODEL.md 3.1/3.2 and of the tracks' offsets (4.1), in one compiled call
        (kernels.sync_step without weighing): first the mode changes (standing -> walking at the
        stay's rate, walking -> standing at speed / walk length; who stood on is more likely a long
        stay, who just stopped a fresh one; the detectability changes now and then within a stay and
        is new for a new one, with the LD2410C amplitude, 4.3), the incoming parts of each mode
        matched to one Gaussian (IMM; walking -> standing: the velocity is 0 from now on; standing ->
        walking: off in any direction at the walking speed), then each mode's linear prediction
        (exact discretizations, Saerkkae & Solin 2019, ex. 6.2; the velocity's correlation time
        1 / turn_rate, MODEL.md 3.2). With the tiles also: walls reflect what moves across them
        (kernels.wall_moves, 3.2, 5.2: the augmented unscented transform with cubature points,
        Saerkkae & Svensson 2023 alg. 8.16 / 8.11; where the component already was stays as it is,
        only the motion meets the walls - cutting the spread at a wall trapped people behind it,
        MODEL.md 10), and the walkers' share that walking takes through a door in dt (the rates of
        the tiles at the doors by the walking component's mass there) goes to the part gone through a
        door, which moves on first."""
        if dt <= 0:
            return
        doors = tiles is not None and tiles.n and not self.phantom
        if self.away is not None:
            self.away.move(dt)
        self.step(dt, m, shapes, tiles if doors else None, 0, None, step_args(tiles if doors else None, shapes),
                  _NO_RATES)

    def step(self, dt: float, m, shapes, tiles, flags: int, f, args, rates) -> float:
        """predict (its part gone through a door already moved; tiles None: no walls and doors) and the
        weighing by no track started (kernels.sync_step's flags: 4 the part gone through a door is
        dropped next, 8 weigh, 16 also that part by f = (f_walk, f_still)); args: step_args, rates:
        use and the sensors' context of kernels.gauss_rates. Returns the log factor."""
        geo, sig, scratch, ones = args
        flags |= 1 if tiles is not None else 0
        away = self.away
        if away is not None:
            flags |= 2
            aw = (away.walk, away.still, away.region, float(away.out), float(away.r))
        else:
            aw = scratch
        if self.slots:
            slot_k = np.array(list(self.slots.values()), dtype=np.int64)
            slot_var = np.array([self.var[seg] for seg in self.slots])
        else:
            slot_k, slot_var = _NO_SLOTS
        d, self.mean, self.cov, self.logw, self.gow, self.ka, self.a, out, r, made = kernels.sync_step(
            np.ascontiguousarray(self.mean), np.ascontiguousarray(self.cov), self.logw, self.gow, self.ka, float(self.a),
            step_params(m, dt), slot_k, slot_var, shapes.go, shapes.go_w, shapes.ka_w, flags, *geo, *aw,
            *(ones if f is None else f), *sig, *rates)
        if made:
            away = self.away = Hidden(tiles)
            away.region = scratch[2].copy()
            scratch[2][:] = 0.0
        if away is not None:
            away.out, away.r = out, r
        return d

    def in_sight(self, world, at) -> None:
        """A sensor measured this person: they are in its sight - the radar can't see through the
        walls (MODEL.md 4.1). at: where its sight lines start (SensorConfig.sight_origin). The Kalman
        update does not know walls: a track at a wall pulls the mean across it (8.10. 21:35, a target
        in the bathroom 2 cm before the wall to the study, the person 0.3 m behind it in the study).
        So the position per mode is cut to what the sensor sees: N(mean, diag var) on a grid of
        Gauss-Hermite points (5 per axis), the points it doesn't see (their sight line crosses a
        wall) dropped, matched again (cross-axis covariances dropped); the rest of the state
        (velocity, the tracks' offsets) follows by its regression on position. If it sees none of
        them, they are mirrored at the wall instead, as walls reflect motion (3.2). The weights stay."""
        if self.phantom:
            return
        pos, var = self.pos, np.maximum(self.pos_var(), 0.0)
        sd = np.sqrt(var)
        K = len(pos)
        pts = pos[:, None, :] + SIGHT_GRID[None, :, :] * sd[:, None, :]  # (modes, points, 2)
        flat = pts.reshape(-1, 2)
        p0 = np.repeat(np.asarray(at, dtype=float)[None, :], len(flat), axis=0)
        hidden = world.crosses_wall(p0, flat).reshape(K, -1)
        if not hidden.any():
            return
        mirrored = world.reflect(p0, flat)[0].reshape(pts.shape)
        self.mean, self.cov = self.mean.copy(), self.cov.copy()
        for k in range(K):
            if not hidden[k].any():
                continue
            if hidden[k].all():
                q, w = mirrored[k], SIGHT_W
            else:
                q, w = pts[k][~hidden[k]], SIGHT_W[~hidden[k]]
            w = w / w.sum()
            mx = w @ q
            Px = np.maximum(w @ (q - mx) ** 2, np.minimum(var[k], SIGHT_MIN_VAR))
            for ax in range(self.mean.shape[1]):
                C = self.cov[k, ax]
                pxx = C[X, X]
                if not pxx > 0:
                    continue
                g = C[:, X] / pxx
                self.mean[k, ax] = self.mean[k, ax] + g * (mx[ax] - self.mean[k, ax, X])
                self.cov[k, ax] = C + np.outer(g, g) * (Px[ax] - pxx)

    def _through_doors(self, dt, tiles):
        """The walkers' share that walking takes through a door in dt (tiling.py: the rates of the
        tiles at the doors, by the walking component's mass there) goes to the part gone through a
        door, into the region behind it, its stay just begun, or out of the house (a door to the
        outside, MODEL.md 2); that part moves on."""
        if self.away is not None:
            self.away.move(dt)
        w = self.weights()
        if w[WALK] < 1e-9:
            return
        rate = tiles.near_doors(self.pos[WALK], self.pos_var()[WALK])
        total = float(rate.sum())
        if total <= 0:
            return
        frac = -math.expm1(-total * dt)
        moved = (1 - self.a) * w[WALK] * frac
        if moved <= 0:
            return
        if self.away is None:
            self.away = Hidden(tiles)
        else:
            self.away.scale(self.a / (self.a + moved))
        share = moved / (self.a + moved) * rate / total
        self.away.region[:, 0] += share[:-1]
        self.away.out += share[-1]
        lw = self.logw.copy()
        lw[WALK] += math.log1p(-frac) if frac < 1 else -math.inf
        top = lw.max()
        self.logw = lw - (top + math.log(float(np.exp(lw - top).sum())))
        self.a += moved

    # ------------------------------------------------------------ tracks

    def kappa_weigh(self, f_still, f_walk) -> np.ndarray:
        """A likelihood that depends on the standing component's detectability, f_still (K,) per
        level (and f_walk for the walking one): updates the levels' probabilities and returns the
        log factor per component (for reweigh / update)."""
        ka = self.ka * np.asarray(f_still)[:, None]
        s = float(ka.sum())
        if s > 0:
            self.ka = ka / s
        return np.array([math.log(s) if s > 0 else -math.inf, math.log(f_walk) if f_walk > 0 else -math.inf])

    def add_track(self, seg, var: float, const_share: float):
        """A new track on this person: its offset (c, o), a priori independent of the rest."""
        k, a, d = self.mean.shape
        mean = np.zeros((k, a, d + 2))
        mean[:, :, :d] = self.mean
        cov = np.zeros((k, a, d + 2, d + 2))
        cov[:, :, :d, :d] = self.cov
        cov[:, :, d, d] = var * const_share
        cov[:, :, d + 1, d + 1] = var * (1 - const_share)
        self.mean, self.cov = mean, cov
        self.slots[seg] = d
        self.var[seg] = var

    def drop_track(self, seg):
        """A track ended (or is no longer this person's): its offset is marginalized out."""
        k = self.slots.pop(seg)
        self.var.pop(seg)
        keep = [i for i in range(self.mean.shape[2]) if i not in (k, k + 1)]
        self.mean = self.mean[:, :, keep]
        self.cov = self.cov[:, :, keep][:, :, :, keep]
        self.slots = {s: (j if j < k else j - 2) for s, j in self.slots.items()}

    def update(self, seg, z, white: float, logf=None) -> float:
        """Kalman update of both components with the track's position z (2,), their weights also
        multiplied by exp(logf) (2,) if given. Returns the log of the mixture's factor (the
        predictive density of z, times f)."""
        k = self.slots[seg]
        idx = [X, k, k + 1]
        Ph = self.cov[:, :, :, idx].sum(axis=3)  # (modes, axes, d): P H^T
        S = Ph[:, :, idx].sum(axis=2) + white ** 2  # (modes, axes)
        e = np.asarray(z)[None, :] - self.mean[:, :, idx].sum(axis=2)  # (modes, axes)
        ll = (-0.5 * e * e / S - 0.5 * np.log(S) - 0.5 * LOG2PI).sum(axis=1)
        gain = Ph / S[:, :, None]
        self.mean = self.mean + e[:, :, None] * gain
        self.cov = self.cov - gain[:, :, :, None] * Ph[:, :, None, :]
        lead = 0.0
        if self.a > 0:  # measured: in view, not gone through a door
            lead = math.log1p(-self.a) if self.a < 1 else -math.inf
            self.a, self.away = 0.0, None
        return lead + self.reweigh(ll if logf is None else ll + logf)

    def _ordered(self, segs) -> "Gauss":
        """The same mixture with exactly the tracks segs (others marginalized out), in that order."""
        g = self
        extra = [s for s in g.slots if s not in segs]
        if extra:
            g = g.copy()
            for s in extra:
                g.drop_track(s)
        if list(g.slots) == list(segs):
            return g
        order = [X, V] + [i for s in segs for i in (g.slots[s], g.slots[s] + 1)]
        return Gauss(g.logw.copy(), g.mean[:, :, order], g.cov[:, :, order][:, :, :, order], g.gow.copy(), g.ka.copy(),
                     {s: 2 + 2 * j for j, s in enumerate(segs)}, {s: g.var[s] for s in segs}, g.phantom, g.a,
                     g.away, g.t)

    @staticmethod
    def mixture(parts) -> "Gauss":
        """sum_i w_i * mixture_i for [(w_i, Gauss)] (weights summing to 1): per mode matched to one
        Gaussian; offsets of tracks that not all of them hold are marginalized out; the parts gone
        through a door mixed."""
        common = [s for s in parts[0][1].slots if all(s in g.slots for _, g in parts)]
        parts = [(w, g._ordered(common)) for w, g in parts]
        comps = []
        for k in (STILL, WALK):
            comps.append(_collapse([(w * (1 - g.a) * g.weights()[k], g.mean[k], g.cov[k]) for w, g in parts]))
        ws = np.array([w * (1 - g.a) * g.weights()[STILL] for w, g in parts])
        aw = [(w * g.a, g.away) for w, g in parts if g.a > 0 and g.away is not None]
        a = sum(x for x, _ in aw)
        away = None
        if a > 0:
            away = aw[0][1].copy() if len(aw) == 1 else Hidden.mixture([(x / a, u) for x, u in aw])
        g0 = parts[0][1]
        if ws.sum() > 0:
            gow = sum(wi * g.gow for wi, (_, g) in zip(ws, parts)) / ws.sum()
            ka = sum(wi * g.ka for wi, (_, g) in zip(ws, parts)) / ws.sum()
        else:
            gow, ka = g0.gow.copy(), g0.ka.copy()
        tot = np.array([comps[0][0], comps[1][0]])
        return Gauss(_log(tot / tot.sum()) if tot.sum() > 0 else np.log([0.5, 0.5]), np.stack([comps[0][1], comps[1][1]]),
                     np.stack([comps[0][2], comps[1][2]]), gow, ka, g0.slots, g0.var, g0.phantom, a, away, g0.t)

    # ------------------------------------------------------------ making one

    @staticmethod
    def _offsets(z, mx, Px, var, const_share, white):
        """Given the position's posterior N(mx, Px) per axis and a track's first position z: the
        joint moments of (x, c, o). Given x, c + o + w = z - x splits by the variances (eigene
        Herleitung, MODEL.md 5.4); returns means (2, 3) and covariances (2, 3, 3) per axis."""
        vc, vo = var * const_share, var * (1 - const_share)
        s2 = var + white ** 2
        ac, ao = vc / s2, vo / s2
        mean = np.zeros((2, 3))
        cov = np.zeros((2, 3, 3))
        r = np.asarray(z) - mx
        mean[:, 0] = mx
        mean[:, 1] = ac * r
        mean[:, 2] = ao * r
        cov[:, 0, 0] = Px
        cov[:, 1, 1] = ac * ac * Px + vc - vc * vc / s2
        cov[:, 2, 2] = ao * ao * Px + vo - vo * vo / s2
        cov[:, 1, 2] = cov[:, 2, 1] = ac * ao * Px - vc * vo / s2
        cov[:, 0, 1] = cov[:, 1, 0] = -ac * Px
        cov[:, 0, 2] = cov[:, 2, 0] = -ao * Px
        return mean, cov

    @classmethod
    def _make(cls, seg, var, w, mx, Px, mv, Pv, gow, ka, z, const_share, white, phantom=False) -> "Gauss":
        """Both components from the position's posterior per mode (mx, Px: (2 modes, 2 axes)) and
        the walkers' velocity (mv, Pv: (2 axes)), with the new track seg measured at z."""
        mean = np.zeros((2, 2, 4))
        cov = np.zeros((2, 2, 4, 4))
        for k in (STILL, WALK):
            mo, co = cls._offsets(z, mx[k], Px[k], var, const_share, white)
            idx = [X, 2, 3]
            mean[k][:, idx] = mo
            for a in range(2):
                cov[k, a][np.ix_(idx, idx)] = co[a]
        mean[WALK, :, V] = mv
        cov[WALK, :, V, V] = Pv
        return cls(_log(np.asarray(w, dtype=float) / np.sum(w)), mean, cov, gow, ka, {seg: 2}, {seg: var}, phantom)

    @classmethod
    def source(cls, seg, z, var, m, shapes) -> "Gauss":
        """The source of a new ghost track: anywhere a ghost may be born (evenly), so given the
        first position x = z - c - o - w; standing or walking alike, a stay seen at a random time."""
        s2 = var + m.white ** 2
        Px = np.full((2, 2), s2)
        mx = np.repeat(np.asarray(z, dtype=float)[None, :], 2, axis=0)
        g = cls._make(seg, var, [0.5, 0.5], mx, Px, np.zeros(2), np.full(2, 0.5 * (m.speed ** 2 + m.speed_spread ** 2)),
                      shapes.go_w_ongoing.copy(), shapes.ka_w.copy(), z, m.const_share, m.white, phantom=True)
        # with an even prior x carries all of z's uncertainty: Var x = vc + vo + w^2, Cov(x, c) = -vc
        vc, vo = var * m.const_share, var * (1 - m.const_share)
        for k in (STILL, WALK):
            for a in range(2):
                C = g.cov[k, a]
                C[X, X] = s2
                C[2, 2], C[3, 3], C[2, 3], C[3, 2] = vc, vo, 0.0, 0.0
                C[X, 2] = C[2, X] = -vc
                C[X, 3] = C[3, X] = -vo
            g.mean[k, :, 2:4] = 0.0
            g.mean[k, :, X] = z
        return g

    @classmethod
    def from_tiles(cls, hidden, tiles, f_walk, f_still, seg, z, var, m) -> tuple:
        """A person without a track gets one at z (MODEL.md 5.4): their density times f (the rate or
        chance of this track starting / being found on somebody in each tile) times the density of
        z, each tile as the Gaussian with its mean and spread (anywhere in it alike, matched in the
        first two moments; exact over its 0.1 m cells changed nothing: 6.10., evidence -7, 10 % more
        computing time), matched per mode to one Gaussian. Returns (Gauss, log of the
        mass) - the mass is this alternative's factor for the hypothesis."""
        s2 = var + m.white ** 2
        v = tiles.var + s2  # (n, 2): z given the tile
        dz = np.asarray(z)[None, :] - tiles.centers
        dens = np.exp(-0.5 * (dz * dz / v).sum(axis=1)) / (2 * math.pi * np.sqrt(v.prod(axis=1)))
        gain = tiles.var / v
        post_m = tiles.centers + gain * dz  # x given the tile and z
        post_v = tiles.var * s2 / v
        qw = hidden.walk * f_walk * dens
        fs = np.asarray(f_still) * dens  # (n,) or per detectability (K, n)
        qs = hidden.still * (fs[None, None, :] if fs.ndim == 1 else fs[None, :, :])  # (L, K, n)
        mass = float(qw.sum() + qs.sum())
        if not (mass > 0 and hidden.r > 0):
            return None, -math.inf
        mx, Px, w = np.zeros((2, 2)), np.zeros((2, 2)), np.zeros(2)
        for k, q in ((STILL, qs.sum(axis=(0, 1))), (WALK, qw)):
            w[k] = q.sum()
            if w[k] <= 0:
                mx[k] = z
                Px[k] = s2
                continue
            mx[k] = q @ post_m / w[k]
            Px[k] = q @ (post_v + (post_m - mx[k]) ** 2) / w[k]
        # a walker without a track: which way is not known (walking is a diffusion between tiles)
        mv, Pv = np.zeros(2), np.full(2, 0.5 * (m.speed ** 2 + m.speed_spread ** 2))
        sh = tiles.sh
        gow = qs.sum(axis=(1, 2))
        gow = gow / gow.sum() if gow.sum() > 0 else sh.go_w_ongoing.copy()
        qk = qs.sum(axis=0)  # (K, n)
        if qk.sum() > 0:
            # the amplitude of the stay as the tiles carry it (4.3), where the measurement puts them
            amp = hidden.amp
            ka = np.einsum("kc,kac->ka", qk, amp[:, :-1]) + np.einsum("kc,kc->k", qk, amp[:, -1])[:, None] * sh.amp_given_kappa
            ka = ka / ka.sum()  # g = 1 on the tiles (nothing measured): the prior given the detectability
        else:
            ka = sh.ka_w.copy()
        # times the probability that the person exists (a known person without a track is a
        # Bernoulli, hidden.py; the track says they do)
        return cls._make(seg, var, w, mx, Px, mv, Pv, gow, ka, z, m.const_share, m.white), math.log(mass) + math.log(hidden.r)

    def to_tiles(self, tiles):
        """The density of this person over the tiles (MODEL.md 5.4), when their last track ends:
        each component spread over the tiles, standing ones with their kinds of stay and
        detectability; with the part gone through a door. The velocity and the offsets are lost."""
        h = Hidden(tiles)
        if not tiles.n:
            h.out = 1.0
            return h
        w = self.weights()
        if w[STILL] > 0:
            mass = tiles.gauss_mass(self.pos[STILL], self.pos_var()[STILL])
            h.still += w[STILL] * np.outer(self.gow, self.kw)[:, :, None] * mass[None, None, :]
            # the amplitude of the stay goes with it where it stands (4.3; per detectability): the end of
            # a track is not the end of the stay (Swerling III). Elsewhere nothing measured (g = 1)
            kw = self.kw
            agk = np.where(kw[:, None] > 0, self.ka / np.where(kw > 0, kw, 1.0)[:, None], tiles.sh.amp_given_kappa)
            agk = np.concatenate([agk, np.zeros((len(kw), 1))], axis=1)
            amp = np.array(h.amp)
            amp[:, :, mass > 0] = agk[:, :, None]
            h.amp = amp
        if w[WALK] > 0:
            h.walk += w[WALK] * tiles.gauss_mass(self.pos[WALK], self.pos_var()[WALK])
        h._normalize()
        if self.a > 0 and self.away is not None:
            return Hidden.mixture([(1 - self.a, h), (self.a, self.away)]) if self.a < 1 else self.away.copy()
        return h
