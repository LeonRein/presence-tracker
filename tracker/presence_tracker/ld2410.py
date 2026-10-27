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

from .filtermodel import STILL, WALK
from .model import remounted

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


def _lgamma(a: np.ndarray) -> np.ndarray:
    return np.vectorize(math.lgamma, otypes=[float])(a) if a.size else a


class Stats:
    """The frames of one sensor since the last evaluation, as the sufficient statistics of the
    Gamma likelihood per cell: time (s) of uncensored frames, time x energy, time x log energy, time
    of censored ones (each frame for the time it stands for: Tracker._ld_frame); and the time in which
    the LD2450 in the same housing had a target in sight (for what the background learns)."""

    __slots__ = ("time", "t_unc", "t_e", "t_le", "t_cens", "t_target", "_prep")

    def __init__(self):
        self.time = 0.0
        self.t_target = 0.0
        self.t_unc = np.zeros(CELLS)
        self.t_e = np.zeros(CELLS)
        self.t_le = np.zeros(CELLS)
        self.t_cens = np.zeros(CELLS)
        self._prep = None

    def add(self, e: np.ndarray, dt: float, target: bool = False):
        self._prep = None
        cens = e >= CAP
        ee = np.maximum(e, 0.5)  # integer energies: 0 as half a unit
        self.time += dt
        self.t_target += dt * target
        self.t_unc += dt * ~cens
        self.t_e += dt * np.where(cens, 0.0, ee)
        self.t_le += dt * np.where(cens, 0.0, np.log(ee))
        self.t_cens += dt * cens


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
    before these frames (the energies' own judgement is not learned back, Park et al. 2020), and
    only as far as no echo source holds the energy (learn). Forgotten with forget s towards an anchor
    of prior_time s: the sensor's own long-run level (the same online EM, forgotten with anchor_forget
    s, from the measured means of the prior with prior_time s, and only from frames after anchor_quiet
    s without anything in sight of the housing's LD2450) - a level that wanders around its sensor's
    mean (a stationary AR(1) level, Harvey 1989; the anchor learned like an empirical Bayes prior). With
    it the rate at which echo sources begin (learn_echoes). Per sensor it holds while the sensor hangs
    where it hung: its cells are distances from the sensor, which a new calibration (heading, scale,
    mirror, a drawing put right) does not change; mounted elsewhere (model.remounted), it starts over.
    Until 0.27 any change of its pose started it over."""

    def __init__(self, prior: np.ndarray, prior_time: float, forget: float, echo_rate: float = 1 / 10800,
                 echo_time: float = 10800.0, anchor_forget: float = 86400.0, anchor_quiet: float = 600.0):
        self.prior = np.asarray(prior, dtype=float)
        self.prior_time = prior_time
        self.forget = forget
        self.anchor_forget = anchor_forget
        self.anchor_quiet = anchor_quiet
        self.num = {}  # sensor id -> (16,) s x the background's share of the energy
        self.den = {}  # sensor id -> s watched
        self.num_a = {}  # sensor id -> as num, forgotten with anchor_forget: the sensor's own level ...
        self.den_a = {}  # ... and its s watched
        self.echoes = {}  # sensor id -> expected number of echo sources begun (forgotten like den) ...
        self.quiet = {}  # sensor id -> ... in this many s with nobody in view
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
        return (self.anchor(sid) * self.prior_time + n) / (self.prior_time + self.den[sid])

    def anchor(self, sid: str) -> np.ndarray:
        """The level the background goes back to while nothing is learned: the sensor's own, learned
        over days, from the measured prior. Until 0.27 the prior itself (5 in the still cells), where
        the bedroom's sensor gives 9 without anybody and the kitchen's 2.5 (MODEL.md 10)."""
        n = self.num_a.get(sid)
        if n is None:
            return self.prior.copy()
        return (self.prior * self.prior_time + n) / (self.prior_time + self.den_a[sid])

    def rate(self, sid: str) -> float:
        """Echo sources begun per s at this sensor: the prior (Model) with what was counted."""
        T = self.quiet.get(sid, 0.0)
        n = self.echoes.get(sid, 0.0)
        return (self.echo_rate * self.echo_time + n) / (self.echo_time + T)

    def learn_echoes(self, sid: str, began: float, rate: float, nobody: float = 1.0, seconds: float = 0.0):
        """Online EM of the echo sources' rate (MODEL.md 4.3): the expected number of sources that
        began in the frames just learned from (`seconds` long), judged with the rate `rate`, and the
        probability that nobody was in view before them. Echo sources are energy from nobody (as
        measured: the empty kitchen at night), and where somebody is, what the profile does not
        explain of them would be counted as sources: counts and time only where nobody is. And a
        count is judged without the learned rate, with the prior's in its place (Park et al. 2020,
        eq. 37-41, as for the ghost map): the rate enters the odds of "a source began" as a factor,
        else what the rate says would be evidence for itself."""
        if sid in self._skipped:
            self._skipped.discard(sid)
            return
        if 0.0 < began < 1.0:
            c = self.echo_rate / rate
            began = began * c / (began * c + 1.0 - began)
        self.echoes[sid] = self.echoes.get(sid, 0.0) + nobody * began
        self.quiet[sid] = self.quiet.get(sid, 0.0) + nobody * seconds

    def learn(self, sid: str, b: np.ndarray, mus: np.ndarray, w: np.ndarray, st: Stats, alpha: np.ndarray,
              off: float = 1.0, quiet: float = math.inf):
        """One step of online EM (MODEL.md 4.3): the frames st, and per hypothesis (weight w) the
        mean energy mus (H, 16) the people, the echo sources and the background b put in. The
        background's share of an energy is b / mu (Richardson 1972; Shepp & Vardi 1982); of a capped
        one, of what it was: E[e | e >= CAP] under Gamma(alpha, mu), b Q(alpha + 1, x) / Q(alpha, x)
        with x = alpha CAP / mu (the E-step of censored data, Dempster, Laird & Rubin 1977). Until
        0.20 a capped energy counted as 100: next to a person who puts in more than that, the
        background learned too little. Weighed with off, the probability that no echo source is on
        (judged with these frames): an echo source on for longer than echoes live is somebody the
        filter lost (MODEL.md 10, "Lernschleifen"), and its profile, on the axis at a few levels,
        fits them only roughly - what it does not explain was learned as background."""
        if self.held.get(sid, 0.0) > 0:
            self.held[sid] -= st.time
            self._skipped.add(sid)
            return
        self._skipped.discard(sid)
        k = math.exp(-st.time / self.forget)
        if sid in self.echoes:
            self.echoes[sid] *= k
            self.quiet[sid] *= k
        gained = w @ (b / mus) * st.t_e
        if st.t_cens.any():
            x = alpha * CAP / mus
            tail = np.exp(log_gammaq(alpha + 1.0, x) - log_gammaq(alpha, x))
            gained = gained + b * (w @ tail) * st.t_cens
        w_bg = off * (1.0 - st.t_target / st.time) if st.time > 0 else 0.0
        self.num[sid] = self.num.get(sid, np.zeros(CELLS)) * k + w_bg * gained
        self.den[sid] = self.den.get(sid, 0.0) * k + w_bg * st.time
        # the anchor: the same over days, but only from frames after anchor_quiet s in which the LD2450
        # of the housing saw nothing (as the empty level was measured, MODEL.md 4.3): sleepers and
        # sitters leave it gaps of seconds to minutes, an empty room hours. Until 0.27 the global prior:
        # in hours without learning (the LD2450 sees somebody all night) the bedroom's level went from 9
        # back to 5, and the energies of the empty bedroom held an unknown person (MODEL.md 10, "Der
        # Hintergrund kehrt zu seinem eigenen Niveau zurück")
        ka = math.exp(-st.time / self.anchor_forget)
        w_a = w_bg if quiet >= self.anchor_quiet else 0.0
        self.num_a[sid] = self.num_a.get(sid, np.zeros(CELLS)) * ka + w_a * gained
        self.den_a[sid] = self.den_a.get(sid, 0.0) * ka + w_a * st.time

    def use(self, config):
        """Keep what was learned for the sensors that hang where they hung (recalibrated or not); the
        ones mounted elsewhere and new ones start over."""
        for s in config.sensors:
            pose = pose_of(s)
            old = self.poses.get(s.id)
            if old is None or remounted(old, s):
                self.num.pop(s.id, None)
                self.den.pop(s.id, None)
                self.num_a.pop(s.id, None)
                self.den_a.pop(s.id, None)
                self.echoes.pop(s.id, None)
                self.quiet.pop(s.id, None)
            self.poses[s.id] = pose

    def to_dict(self) -> dict:
        held = {k: round(float(v), 3) for k, v in self.held.items() if v > 0}
        return {"poses": {k: list(v) for k, v in self.poses.items()},
                "num": {k: [round(float(x), 4) for x in v] for k, v in self.num.items()},
                "den": {k: round(float(v), 3) for k, v in self.den.items()},
                "num_a": {k: [round(float(x), 4) for x in v] for k, v in self.num_a.items()},
                "den_a": {k: round(float(v), 3) for k, v in self.den_a.items()},
                "echoes": {k: round(float(v), 4) for k, v in self.echoes.items()},
                "quiet": {k: round(float(v), 3) for k, v in self.quiet.items()},
                **({"held": held} if held else {})}

    def load_dict(self, d: dict):
        self.poses = {k: tuple(v) for k, v in d.get("poses", {}).items()}
        # a part without its other half (num without den, a file broken by hand) is dropped: kept, b()
        # failed at every frame of the sensor until 0.28
        self.den = {k: float(v) for k, v in d.get("den", {}).items()}
        self.num = {k: np.array(v, dtype=float) for k, v in d.get("num", {}).items() if len(v) == CELLS and k in self.den}
        self.den = {k: v for k, v in self.den.items() if k in self.num}
        self.den_a = {k: float(v) for k, v in d.get("den_a", {}).items()}
        self.num_a = {k: np.array(v, dtype=float) for k, v in d.get("num_a", {}).items()
                      if len(v) == CELLS and k in self.den_a}
        self.den_a = {k: v for k, v in self.den_a.items() if k in self.num_a}
        self.quiet = {k: float(v) for k, v in d.get("quiet", {}).items() if k in self.num}
        self.echoes = {k: float(v) for k, v in d.get("echoes", {}).items() if k in self.quiet}
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
