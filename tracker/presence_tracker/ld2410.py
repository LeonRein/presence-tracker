"""The LD2410C's energies per 0.75 m gate (MODEL.md 4.3): what a person puts into them, their
likelihood, and the background the app learns per sensor.

Per frame the LD2410C sends 9 "moving" and 9 "still" energies (0..100) for the rings of 0.75 m slant
distance; still in rings 0 and 1 is always 0 (no still detection there). The 16 cells used here are
moving 0..8 and still 2..8. Each energy ~ Gamma(shape alpha, mean mu), 100 censored, with
mu = background + sum over the people of their expected energy (incoherent superposition). The
likelihood per cell and resolution cell is the form of track-before-detect (Salmond & Birch 2001,
Boers & Driessen 2004); the frames are correlated, so their log-likelihoods count with dt / tau
(composite likelihood, Varin, Reid & Firth 2011, tau the integrated correlation time).
"""

import json
import math
import os

import numpy as np

from . import kernels
from .filtermodel import STILL, WALK

GATE = 0.75  # m
CELLS = 16  # moving 0..8, still 2..8
MOVING = slice(0, 9)
STILL_CELLS = slice(9, 16)
CENTRE = np.concatenate([(np.arange(9) + 0.5) * GATE, (np.arange(2, 9) + 0.5) * GATE])
CAP = 99.5  # energies at 100 are capped: censored above this
FLOOR = 0.05  # expected energy below this: the tile is out of the LD2410C's view
LOG_CAP = 600.0  # likelihood ratios are capped at e^this (keeps the densities finite)


def cell_values(m, kind_moving, kind_still) -> np.ndarray:
    """(16,) from a value for the moving cells and one for the still cells."""
    return np.concatenate([np.full(9, float(kind_moving)), np.full(7, float(kind_still))])


def _profile(params, r, centre):
    A, n, delta, sigma, tail, ell = params
    D = centre[None, :] - r[:, None] - delta
    main = np.exp(-0.5 * (D / sigma) ** 2)
    k = np.where(D <= 0, main, (1 - tail) * main + tail * np.exp(-np.maximum(D, 0.0) / ell))
    return A * r[:, None] ** (-n) * k


def expected(m, angle, slant, sight) -> tuple:
    """Expected energy a person puts into the 16 cells, (standing (k, 16), walking (k, 16)), at the
    given degrees off the axis, slant distances (m) and sight (bool): the profile measured 6.10.
    (MODEL.md 4.3) times the beam."""
    angle = np.asarray(angle, dtype=float).reshape(-1)
    r = np.maximum(np.asarray(slant, dtype=float).reshape(-1), 0.3)
    full, none = m.ld_beam
    v = np.clip((none - angle) / (none - full), 0.0, 1.0) * np.asarray(sight, dtype=float).reshape(-1)
    out = []
    for mode in (STILL, WALK):
        mov = _profile(m.ld_moving[mode], r, CENTRE[MOVING])
        sti = _profile(m.ld_still[mode], r, CENTRE[STILL_CELLS])
        out.append(v[:, None] * np.concatenate([mov, sti], axis=1))
    return out[0], out[1]


def log_gammaq(a, x) -> np.ndarray:
    """log of the regularized upper incomplete gamma function Q(a, x), x >= 0, a and x broadcast
    (Numerical Recipes 6.2: the series for x < a + 1, the continued fraction (Lentz) above)."""
    a, x = np.broadcast_arrays(np.asarray(a, dtype=float), np.asarray(x, dtype=float))
    out = np.zeros(x.shape)
    lead = -x + a * np.log(np.maximum(x, 1e-300)) - _lgamma(a)
    small = x < a + 1
    if small.any():
        xs, as_ = x[small], a[small]
        term = 1.0 / as_
        total = term.copy()
        for k in range(1, 40):
            term = term * xs / (as_ + k)
            total += term
        out[small] = np.log(np.maximum(1.0 - np.exp(lead[small]) * total, 1e-300))
    big = ~small
    if big.any():
        xb, ab = x[big], a[big]
        bb = xb + 1 - ab
        c = np.full(xb.shape, 1e300)
        d = 1.0 / bb
        h = d.copy()
        for i in range(1, 30):
            an = -i * (i - ab)
            bb = bb + 2
            d = an * d + bb
            d = np.where(np.abs(d) < 1e-300, 1e-300, d)
            c = bb + an / c
            c = np.where(np.abs(c) < 1e-300, 1e-300, c)
            d = 1.0 / d
            h = h * d * c
        out[big] = lead[big] + np.log(np.maximum(h, 1e-300))
    return out


_GRID = np.linspace(math.log(0.05), math.log(1e5), 4001)  # log mean energy
_TABLES = {}


def log_censored(alpha: np.ndarray, mu: np.ndarray) -> np.ndarray:
    """log P(energy >= CAP) for Gamma(alpha, mean mu): log_gammaq on a fine grid of log mu,
    interpolated (alpha takes one value per kind of cell)."""
    out = np.empty(np.broadcast(alpha, mu).shape)
    alpha = np.broadcast_to(alpha, out.shape)
    mu = np.broadcast_to(mu, out.shape)
    lm = np.log(np.maximum(mu, 1e-300))
    for a in np.unique(alpha):
        tab = _TABLES.get(float(a))
        if tab is None:
            tab = _TABLES[float(a)] = log_gammaq(a, CAP * a / np.exp(_GRID))
        k = alpha == a
        out[k] = np.interp(lm[k], _GRID, tab)
    return out


_BY_ALPHA = {}


def _censored_tables(alpha: np.ndarray) -> tuple:
    """For kernels.ld_censored: per cell the row of its table of log P(energy >= CAP) on _GRID
    (log_censored's), and the tables (one per value of alpha)."""
    key = alpha.tobytes()
    hit = _BY_ALPHA.get(key)
    if hit is None:
        values = sorted(set(alpha.tolist()))
        log_censored(np.array(values), np.ones(len(values)))  # fills _TABLES
        hit = _BY_ALPHA[key] = (np.array([values.index(a) for a in alpha.tolist()], dtype=np.int64),
                                np.stack([_TABLES[float(v)] for v in values]))
    return hit


def _lgamma(a: np.ndarray) -> np.ndarray:
    return np.vectorize(math.lgamma, otypes=[float])(a) if a.size else a


class Stats:
    """The frames of one sensor since the last evaluation, as the sufficient statistics of the
    Gamma likelihood per cell: time (s) of uncensored frames, time x energy, time x log energy, time
    of censored ones (each frame for the time it stands for: Tracker._ld_frame)."""

    __slots__ = ("time", "t_unc", "t_e", "t_le", "t_cens")

    def __init__(self):
        self.time = 0.0
        self.t_unc = np.zeros(CELLS)
        self.t_e = np.zeros(CELLS)
        self.t_le = np.zeros(CELLS)
        self.t_cens = np.zeros(CELLS)

    def add(self, e: np.ndarray, dt: float):
        cens = e >= CAP
        ee = np.maximum(e, 0.5)  # integer energies: 0 as half a unit
        self.time += dt
        self.t_unc += dt * ~cens
        self.t_e += dt * np.where(cens, 0.0, ee)
        self.t_le += dt * np.where(cens, 0.0, np.log(ee))
        self.t_cens += dt * cens

    def _mu_part(self, mu: np.ndarray, lik: "Likelihood") -> np.ndarray:
        """The part of the tempered log-likelihood that depends on the means mu (k, 16), with the
        block's common gain of each kind integrated out (Likelihood), (k,)."""
        a, tau = lik.alpha, lik.tau
        look = a * self.t_unc / tau  # alpha x looks per cell
        out = -(np.log(mu) @ look)
        inv = 1.0 / mu
        ae = a * self.t_e / tau
        for cells, comps in lik.kinds:
            N = float(look[cells].sum())
            if N <= 0:
                continue
            A = inv[:, cells] @ ae[cells]
            terms = np.array([c + math.lgamma(N + k) - (N + k) * np.log(r + A) for c, k, r in comps])
            top = terms.max(axis=0)
            out += top + np.log(np.exp(terms - top).sum(axis=0))
        c = np.flatnonzero(self.t_cens > 0)
        if len(c):  # log_censored compiled (kernels.ld_censored)
            rows, tables = _censored_tables(a)
            m = np.ascontiguousarray(mu)
            out += kernels.ld_censored(m, np.log(m), c, rows[c], tables, _GRID) @ (self.t_cens[c] / tau[c])
        return out

    def loglik(self, mu: np.ndarray, lik: "Likelihood") -> float:
        """Tempered log-likelihood of the frames given the mean energies mu (16,)."""
        a, tau = lik.alpha, lik.tau
        const = ((self.t_unc * (a * np.log(a) - _lgamma(a)) + (a - 1) * self.t_le) / tau).sum()
        return float(const + self._mu_part(mu[None, :], lik)[0])

    def log_ratio(self, mu1: np.ndarray, mu0: np.ndarray, lik: "Likelihood") -> np.ndarray:
        """Tempered log-likelihood ratio of the means mu1 (k, 16) against mu0 (16,), (k,)."""
        both = self._mu_part(np.vstack([mu1, mu0[None, :]]), lik)
        return both[:-1] - both[-1]


class Likelihood:
    """The numbers of the energies' likelihood (Model): per cell the Gamma shape alpha and the
    correlation time tau; per kind (moving, still) the prior of the block's common gain u: 1/u ~
    Gamma(kappa, rate kappa) (mean 1), mixed with bursts, 1/u ~ Gamma(shape, rate shape x their gain)
    (conjugate: integrated exactly)."""

    def __init__(self, m):
        self.alpha = cell_values(m, *m.ld_shape)
        self.tau = cell_values(m, *m.ld_tau)
        bk, bu, bw = m.ld_burst
        br = bk * bu
        self.kinds = []
        for cells, k in ((MOVING, m.ld_gain[0]), (STILL_CELLS, m.ld_gain[1])):
            comps = [(math.log(1 - bw) + k * math.log(k) - math.lgamma(k), k, k),
                     (math.log(bw) + bk * math.log(br) - math.lgamma(bk), bk, br)]
            self.kinds.append((cells, comps))


class Echoes:
    """The LD2410C's echo sources (MODEL.md 4.3): energy that is not a person's (on 7.10. at 01:08 in
    the empty kitchen 40 s of near moving bursts and still energies up to 90 in gates 2-4). Per sensor
    off, or one source on - the profile of a standing person on the axis at slant r, times an
    amplitude - in one of K stages: it begins at rate ld_echo_rate (uniform over the sources), lives
    an Erlang(K) time with mean ld_echo_life and ends. Like the LD2450's ghosts (4.2), what tells it
    from a person is how it begins and ends: a person must come in, and stays."""

    def __init__(self, m):
        lo, hi, step = m.ld_echo_ranges
        rs = np.arange(lo, hi + 1e-9, step)
        prof = expected(m, np.zeros(len(rs)), rs, np.ones(len(rs), bool))[STILL]
        self.src = np.concatenate([amp * prof for amp in m.ld_echo_amps])  # (J, 16)
        self.K = m.ld_echo_stages
        self.S = np.tile(self.src, (self.K, 1))  # (K J, 16): the states that are on
        self.p = np.zeros(1 + len(self.S))  # [off, (stage, source)...]
        self.p[0] = 1.0

    def predict(self, dt: float, m, rate: float):
        """Move on by dt: sources begin with rate (per s), age, end."""
        J = len(self.src)
        p, q = self.p, np.zeros_like(self.p)
        on = p[0] * -math.expm1(-rate * dt)
        q[0] = p[0] - on
        q[1:1 + J] += on / J
        self._fresh = on / J  # per source: what began just now (for learning the rate)
        go = -math.expm1(-self.K / m.ld_echo_life * dt)
        for k in range(self.K):
            part = p[1 + k * J:1 + (k + 1) * J]
            q[1 + k * J:1 + (k + 1) * J] += part * (1 - go)
            if k + 1 < self.K:
                q[1 + (k + 1) * J:1 + (k + 2) * J] += part * go
            else:
                q[0] += float(part.sum()) * go
        self.p = q
        self._first = q[1:1 + J].copy()

    def began(self) -> float:
        """After weigh: the expected number of sources that began in the last step."""
        first = self._first
        share = np.divide(self._fresh, first, out=np.zeros_like(first), where=first > 0)
        return float(self.p[1:1 + len(self.src)] @ share)

    def points(self, scale: np.ndarray) -> tuple:
        """(masses of the states that are on, what each puts into the cells x scale)."""
        return self.p[1:], self.S * scale

    def weigh(self, logf: np.ndarray):
        """Multiply the states that are on by exp(logf) (off by 1) and normalize."""
        top = max(float(logf.max()), 0.0)
        on = self.p[1:] * np.exp(logf - top)
        z = self.p[0] * math.exp(-top) + float(on.sum())
        self.p = np.concatenate([[self.p[0] * math.exp(-top)], on]) / z

    def on(self) -> float:
        return float(1.0 - self.p[0])


def prior(m) -> np.ndarray:
    """(16,) the background before anything is learned (Model.ld_background)."""
    g0, g1, rest, still = m.ld_background
    return np.concatenate([[g0, g1], np.full(7, rest), np.full(7, still)])


def pose_of(s) -> tuple:
    """What of a sensor's setup decides what its LD2410C sees."""
    return (s.x, s.y, s.heading, s.height)


class Background:
    """The energy each sensor's LD2410C sees without anybody (MODEL.md 4.3), per cell, learned by the
    app: online EM of the superposition - the background's share of an energy is b / mu on average
    (Richardson 1972, Lucy 1974; Shepp & Vardi 1982), with mu from the people as the filter saw them
    before these frames (the energies' own judgement is not learned back, Park et al. 2020). A prior
    of prior_time s of the measured means; forgotten with forget s. Per sensor it holds only while
    the sensor is where it was (pose_of): moved, it starts over."""

    def __init__(self, prior: np.ndarray, prior_time: float, forget: float, echo_rate: float = 1 / 10800,
                 echo_time: float = 10800.0):
        self.prior = np.asarray(prior, dtype=float)
        self.prior_time = prior_time
        self.forget = forget
        self.num = {}  # sensor id -> (16,) s x the background's share of the energy
        self.den = {}  # sensor id -> s watched
        self.echoes = {}  # sensor id -> expected number of echo sources begun (forgotten like den)
        self.poses = {}
        self.echo_rate, self.echo_time = echo_rate, echo_time  # prior of the echo rate: per s, weight s
        self.held = {}  # sensor id -> s of its frames still not learned from (pause)
        self._skipped = set()  # the sensors whose last frames were not learned from (their echoes neither)

    def pause(self, seconds: float):
        """Learn nothing from the next `seconds` s of each sensor's frames: after "Tracks zurücksetzen"
        the filter knows nothing about the people for a while, and whom it does not see yet would be
        learned as background (and could make somebody sitting there invisible later)."""
        self.held = {sid: float(seconds) for sid in self.poses}

    def b(self, sid: str) -> np.ndarray:
        n = self.num.get(sid)
        if n is None:
            return self.prior.copy()
        return (self.prior * self.prior_time + n) / (self.prior_time + self.den[sid])

    def rate(self, sid: str) -> float:
        """Echo sources begun per s at this sensor: the prior (Model) with what was counted."""
        T = self.den.get(sid, 0.0)
        n = self.echoes.get(sid, 0.0)
        return (self.echo_rate * self.echo_time + n) / (self.echo_time + T)

    def learn_echoes(self, sid: str, began: float):
        if sid in self._skipped:
            self._skipped.discard(sid)
            return
        self.echoes[sid] = self.echoes.get(sid, 0.0) + began

    def learn(self, sid: str, share: np.ndarray, st: Stats):
        if self.held.get(sid, 0.0) > 0:
            self.held[sid] -= st.time
            self._skipped.add(sid)
            return
        self._skipped.discard(sid)
        k = math.exp(-st.time / self.forget)
        if sid in self.echoes:
            self.echoes[sid] *= k
        energy = st.t_e + 100.0 * st.t_cens
        self.num[sid] = self.num.get(sid, np.zeros(CELLS)) * k + share * energy
        self.den[sid] = self.den.get(sid, 0.0) * k + st.time

    def use(self, config):
        """Keep what was learned for the sensors that are where they were; the others start over."""
        for s in config.sensors:
            pose = pose_of(s)
            if self.poses.get(s.id) != pose:
                self.num.pop(s.id, None)
                self.den.pop(s.id, None)
                self.echoes.pop(s.id, None)
                self.poses[s.id] = pose

    def to_dict(self) -> dict:
        held = {k: round(float(v), 3) for k, v in self.held.items() if v > 0}
        return {"poses": {k: list(v) for k, v in self.poses.items()},
                "num": {k: [round(float(x), 4) for x in v] for k, v in self.num.items()},
                "den": {k: round(float(v), 3) for k, v in self.den.items()},
                "echoes": {k: round(float(v), 4) for k, v in self.echoes.items()},
                **({"held": held} if held else {})}

    def load_dict(self, d: dict):
        self.poses = {k: tuple(v) for k, v in d.get("poses", {}).items()}
        self.num = {k: np.array(v, dtype=float) for k, v in d.get("num", {}).items() if len(v) == CELLS}
        self.den = {k: float(v) for k, v in d.get("den", {}).items() if k in self.num}
        self.echoes = {k: float(v) for k, v in d.get("echoes", {}).items() if k in self.num}
        self.held = {k: float(v) for k, v in d.get("held", {}).items()}
        self._skipped = set()

    def save(self, path):
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f)
        os.replace(tmp, path)

    def load(self, path):
        with open(path) as f:
            self.load_dict(json.load(f))
