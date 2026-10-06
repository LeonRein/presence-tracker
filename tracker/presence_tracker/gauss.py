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

from .filtermodel import STILL, WALK

LOG2PI = math.log(2 * math.pi)
X, V = 0, 1  # state indices; a track's c and o follow at slots[seg], slots[seg] + 1


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


def _log(w):
    with np.errstate(divide="ignore"):
        return np.log(w)


class Gauss:
    __slots__ = ("logw", "mean", "cov", "gow", "kw", "slots", "var", "phantom", "a", "away", "t")

    def __init__(self, logw, mean, cov, gow, kw, slots=None, var=None, phantom=False, a=0.0, away=None, t=None):
        self.logw = np.asarray(logw, dtype=float)  # (2,) log weights of [STILL, WALK], normalized
        self.mean = mean  # (2 modes, 2 axes, d)
        self.cov = cov  # (2 modes, 2 axes, d, d)
        self.gow = gow  # (L,) probability of each kind of stay (for the standing component)
        self.kw = kw  # (K,) probability of each level of detectability (for the standing component)
        self.slots = dict(slots or {})  # track -> index of its c (o follows)
        self.var = dict(var or {})  # track -> variance of its offset per axis
        self.phantom = phantom  # the source of a ghost track, not a person
        self.a = a  # weight of the part gone through a door
        self.away = away  # ... its density (hidden.Hidden), or None
        self.t = t  # the time it was moved to (None: made at the tracker's last move of everybody)

    def copy(self) -> "Gauss":
        return Gauss(self.logw.copy(), self.mean.copy(), self.cov.copy(), self.gow.copy(), self.kw.copy(), self.slots,
                     self.var, self.phantom, self.a, self.away.copy() if self.away is not None else None, self.t)

    @property
    def segs(self) -> set:
        return set(self.slots)

    def weights(self) -> np.ndarray:
        """The components' weights (summing to 1; the part gone through a door aside)."""
        top = self.logw.max()
        if top == -math.inf:
            return np.array([0.5, 0.5])
        w = np.exp(self.logw - top)
        return w / w.sum()

    def reweigh(self, dlog, d_away: float = 0.0) -> float:
        """Multiply the components' weights by exp(dlog) and the part gone through a door by
        exp(d_away); returns the log of the factor of the whole person (for the hypotheses) and
        normalizes."""
        lw = self.logw + dlog
        top = lw.max()
        comps = top + math.log(float(np.exp(lw - top).sum())) if top > -math.inf else -math.inf
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

    def near(self, z, radius: float) -> np.ndarray:
        """(2,) E[exp(-|x - z|^2 / (2 radius^2))] per component."""
        v = radius ** 2 + self.pos_var()
        d2 = (self.pos - np.asarray(z)[None, :]) ** 2
        return np.prod(np.sqrt(radius ** 2 / v) * np.exp(-0.5 * d2 / v), axis=1)

    def tile_mass(self, tiles) -> np.ndarray:
        """(n,) probability of each tile, mixed over the modes."""
        out = np.zeros(tiles.n)
        for k, w in enumerate(self.weights()):
            if w > 0:
                out += w * tiles.gauss_mass(self.pos[k], self.pos_var()[k])
        return out

    # ------------------------------------------------------------ dynamics

    def predict(self, dt: float, m, shapes, tiles=None):
        """One step of MODEL.md 3.1/3.2 and of the tracks' offsets (4.1): first the mode changes
        (standing -> walking at the stay's rate, walking -> standing at speed / walk length), the
        incoming parts of each mode matched to one Gaussian (IMM), then each mode's linear
        prediction."""
        if dt <= 0:
            return
        w = self.weights()
        stayed = self.gow * np.exp(-shapes.go * dt)
        p_stay = float(stayed.sum())
        p_stop = -math.expm1(-m.speed / m.walk_length * dt)
        w_ss, w_sw = w[STILL] * p_stay, w[STILL] * (1 - p_stay)
        w_ww, w_ws = w[WALK] * (1 - p_stop), w[WALK] * p_stop
        mS, PS = self.mean[STILL], self.cov[STILL]
        mW, PW = self.mean[WALK], self.cov[WALK]
        # walking -> standing: the velocity is 0 from now on
        mWS, PWS = mW.copy(), PW.copy()
        mWS[:, V] = 0.0
        PWS[:, V, :] = 0.0
        PWS[:, :, V] = 0.0
        # standing -> walking: off in any direction at the walking speed (MODEL.md 3.1)
        mSW, PSW = mS.copy(), PS.copy()
        mSW[:, V] = 0.0
        PSW[:, V, :] = 0.0
        PSW[:, :, V] = 0.0
        PSW[:, V, V] = 0.5 * (m.speed ** 2 + m.speed_spread ** 2)
        tot_s, mS, PS = _collapse([(w_ss, mS, PS), (w_ws, mWS, PWS)])
        tot_w, mW, PW = _collapse([(w_ww, mW, PW), (w_sw, mSW, PSW)])
        # the kinds of stay: who stood on is more likely a long stay; who just stopped, a fresh one.
        # The detectability changes now and then within a stay, and is new for a new one
        if tot_s > 0:
            fresh = shapes.go_w
            kept = stayed / p_stay if p_stay > 0 else fresh
            self.gow = (w_ss * kept + w_ws * fresh) / tot_s
            q = -math.expm1(-m.kappa_switch * dt)
            kw = (1 - q) * self.kw + q * shapes.kappa_w
            self.kw = (w_ss * kw + w_ws * shapes.kappa_w) / tot_s
        self.logw = _log(np.array([tot_s, tot_w]))
        top = self.logw.max()
        self.logw -= top + math.log(float(np.exp(self.logw - top).sum()))
        # linear prediction per mode (exact discretizations, Saerkkae & Solin 2019, ex. 6.2)
        d = self.mean.shape[2]
        F = np.broadcast_to(np.eye(d), (2, d, d)).copy()
        Q = np.zeros((2, d, d))
        F[STILL, V, V] = 0.0
        Q[STILL, X, X] = m.still_noise ** 2 * dt
        tau = 1.0 / m.turn_rate  # the velocity's correlation time (MODEL.md 3.2)
        a = math.exp(-dt / tau)
        s2 = 0.5 * (m.speed ** 2 + m.speed_spread ** 2)  # stationary variance per axis
        F[WALK, X, V] = tau * (1 - a)
        F[WALK, V, V] = a
        Q[WALK, V, V] = s2 * (1 - a * a)
        Q[WALK, X, V] = Q[WALK, V, X] = s2 * tau * (1 - a) ** 2
        Q[WALK, X, X] = 2 * s2 * tau * (dt - 2 * tau * (1 - a) + 0.5 * tau * (1 - a * a))
        ao = math.exp(-dt / m.tau)
        for seg, k in self.slots.items():
            F[:, k + 1, k + 1] = ao
            Q[:, k + 1, k + 1] = self.var[seg] * (1 - m.const_share) * (1 - ao * ao)
        self.mean = np.einsum("kij,kaj->kai", F, np.stack([mS, mW]))
        P = np.stack([PS, PW])
        self.cov = np.einsum("kij,kajl,kml->kaim", F, P, F) + Q[:, None, :, :]
        if tiles is not None and tiles.n and not self.phantom:
            self._through_doors(dt, tiles)

    def _through_doors(self, dt, tiles):
        """The walkers' share that walking takes through a door in dt (tiling.py: the rates of the
        tiles at the doors, by the walking component's mass there) goes to the part gone through a
        door, into the region behind it, its stay just begun; that part moves on."""
        from .hidden import Hidden
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
        self.away.region[:, 0] += moved / (self.a + moved) * rate / total
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
        s = float(self.kw @ f_still)
        if s > 0:
            self.kw = self.kw * f_still / s
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
        return Gauss(g.logw.copy(), g.mean[:, :, order], g.cov[:, :, order][:, :, :, order], g.gow.copy(), g.kw.copy(),
                     {s: 2 + 2 * j for j, s in enumerate(segs)}, {s: g.var[s] for s in segs}, g.phantom, g.a,
                     g.away, g.t)

    @staticmethod
    def mixture(parts) -> "Gauss":
        """sum_i w_i * mixture_i for [(w_i, Gauss)] (weights summing to 1): per mode matched to one
        Gaussian; offsets of tracks that not all of them hold are marginalized out; the parts gone
        through a door mixed."""
        from .hidden import Hidden
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
            kw = sum(wi * g.kw for wi, (_, g) in zip(ws, parts)) / ws.sum()
        else:
            gow, kw = g0.gow.copy(), g0.kw.copy()
        tot = np.array([comps[0][0], comps[1][0]])
        return Gauss(_log(tot / tot.sum()) if tot.sum() > 0 else np.log([0.5, 0.5]), np.stack([comps[0][1], comps[1][1]]),
                     np.stack([comps[0][2], comps[1][2]]), gow, kw, g0.slots, g0.var, g0.phantom, a, away, g0.t)

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
    def _make(cls, seg, var, w, mx, Px, mv, Pv, gow, kw, z, const_share, white, phantom=False) -> "Gauss":
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
        return cls(_log(np.asarray(w, dtype=float) / np.sum(w)), mean, cov, gow, kw, {seg: 2}, {seg: var}, phantom)

    @classmethod
    def source(cls, seg, z, var, m, shapes) -> "Gauss":
        """The source of a new ghost track: anywhere a ghost may be born (evenly), so given the
        first position x = z - c - o - w; standing or walking alike, a stay seen at a random time."""
        s2 = var + m.white ** 2
        Px = np.full((2, 2), s2)
        mx = np.repeat(np.asarray(z, dtype=float)[None, :], 2, axis=0)
        g = cls._make(seg, var, [0.5, 0.5], mx, Px, np.zeros(2), np.full(2, 0.5 * (m.speed ** 2 + m.speed_spread ** 2)),
                      shapes.go_w_ongoing.copy(), shapes.kappa_w.copy(), z, m.const_share, m.white, phantom=True)
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
        z, each tile a small Gaussian, matched per mode to one Gaussian. Returns (Gauss, log of the
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
        if not mass > 0:
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
        kw = qs.sum(axis=(0, 2))
        kw = kw / kw.sum() if kw.sum() > 0 else sh.kappa_w.copy()
        return cls._make(seg, var, w, mx, Px, mv, Pv, gow, kw, z, m.const_share, m.white), math.log(mass)

    def to_tiles(self, tiles):
        """The density of this person over the tiles (MODEL.md 5.4), when their last track ends:
        each component spread over the tiles, standing ones with their kinds of stay and
        detectability; with the part gone through a door. The velocity and the offsets are lost."""
        from .hidden import Hidden
        h = Hidden(tiles)
        if not tiles.n:
            h.out = 1.0
            return h
        w = self.weights()
        if w[STILL] > 0:
            h.still += w[STILL] * np.outer(self.gow, self.kw)[:, :, None] * tiles.gauss_mass(self.pos[STILL], self.pos_var()[STILL])[None, None, :]
        if w[WALK] > 0:
            h.walk += w[WALK] * tiles.gauss_mass(self.pos[WALK], self.pos_var()[WALK])
        h._normalize()
        if self.a > 0 and self.away is not None:
            return Hidden.mixture([(1 - self.a, h), (self.a, self.away)]) if self.a < 1 else self.away.copy()
        return h
