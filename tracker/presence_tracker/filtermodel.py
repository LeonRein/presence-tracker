"""The numbers and the fixed shapes of the model (MODEL.md). Measured ones say so; the rest are
assumptions. Every shape comes from the literature; the data only set the scales."""

import math

import numpy as np

WALK, STILL = 1, 0
FRAME = 0.089  # s, LD2450 frame period (measured)
IDLE = 6.0  # s: frames further apart were lost on the way (the idle heartbeat is 5 s)
P_FA = 1e-6  # false alarm probability per detection cell of a CFAR radar (a usual design value, Skolnik)


class Model:
    # 3.1 standing (measured 5.10. on LD2450 tracks)
    still_noise = 0.02  # m per sqrt(s): a standing person sways
    # how long one stands: survival (go_time / (t + go_time))^go_share (measured, Lomax), as a
    # mixture of exponentials (Feldmann & Whitt 1998): every stay has its own rate of getting up
    go_share, go_time = 0.58, 5.6
    # 4.1 detectability of a still person in this stay (posture, place): kappa ~ Gamma(shape, shape),
    # mean 1, in equally probable levels; drawn anew at each stop and now and then within a stay
    # (Mahler, Vo & Vo 2011; Wilthil et al. 2019: detectability modes as a Markov chain)
    kappa_shape = 1.0  # assumed until estimated over the evidence (MODEL.md 7)
    kappa_levels = 5
    kappa_switch = 1 / 600  # 1/s (assumed, as in the 0.7.0 draft)
    # 3.2 walking: a velocity-jump process (measured 6.10. on 8.7 h of LD2450 tracks, walks of >= 2 s)
    speed = 0.85  # m/s
    speed_spread = 0.36  # m/s: spread of the speed between walks (10-90 % 0.44-1.37 m/s)
    turn_rate = 0.85  # 1/s: a walker's direction is forgotten at this rate (velocity autocorrelation)
    walk_length = 2.0  # m: mean length of a walk (measured, median 2.1 m), walkers stop at speed / this
    # 3.3 into the house and out of it (assumed)
    arrive_rate = 1 / (4 * 3600)  # 1/s per way in
    leave_rate = 1 / (2 * 3600)  # 1/s
    # 3.4 people nobody knows of (assumed): newcomers per way in, and how soon somebody out of the
    # house is forgotten (whoever comes back after that is a newcomer)
    guest_rate = 1 / (2 * 86400)  # 1/s per way in
    forget_rate = 1 / 86400  # 1/s
    # 4.1 the sensor's tracks. A person in view gets a track at a rate rho * P_D(r): P_D of a
    # fluctuating target (Swerling I) at the signal-to-noise ratio of the radar equation, falling
    # with r^-4 (Skolnik), half at r50 [STILL, WALK]. Fitted by maximum likelihood (Poisson) to what
    # was measured on 4./5.10. (17 h; one sensor tracks a person who also walked, where the other
    # sees well: how often does the other start a track there, 65 starts on still people in 81 min,
    # 66 on walkers in 5 min)
    acquire = ((0.034, 5.0), (0.51, 4.55))
    # a lost track found again (measured 5.10., 16 h): median s, log-normal spread, share never
    refind = {"coast": (0.13, 1.0, 0.03), "frozen": (0.2, 2.0, 0.02)}
    find_radius = 0.7  # m: the sensor finds its lost target only on somebody near where it holds it
    find_clutter = 0.05  # ... or on a reflection near the spot, as likely as on this many people there
    track_life = (60.0, 30.0)  # s [STILL, WALK]: how long a track lasts (measured median), for tracks there at the start
    res_range, res_cross = 0.6, 0.9  # m: two people this far apart are resolved half of the time (Svensson 2012)
    const_share = 0.57  # share of the offset's variance that is constant over a track (measured)
    tau = 3.5  # s: correlation time of the rest (measured)
    white = 0.05  # m: frame jitter
    pos_every = 0.25  # s: a track's positions are used this often (dropping data, exact)
    # 4.2 ghosts: tracks not from a person. Their source moves like a person (false tracks of a
    # local tracker follow its motion model); what tells them apart is how they begin and end
    ghost_echo = 3e-4  # per m^2 and s and walking person in view: echoes (short ghosts)
    # kinds of ghosts: born per m^2 and s, mean life s - where no ghost map is learned (ghostmap.py).
    # Estimated by EM on 21 h of recordings without truth (6.10., tools/ghostmap.py): 1.8e-5 per m^2
    # and s over all setups, 40 % living 3 s on average, 60 % 39 s (setup of 4.10. 18:06, 10.8 h)
    ghost_types = ((0.4 * 1.8e-5, 3.0), (0.6 * 1.8e-5, 39.0))
    ghost_prior_time = 4 * 3600.0  # s: weight of that rate in each cell of a ghost map, as watching time
    # 5 inference: hypotheses over the tracks' owners, cut by weight (Vo et al. 2017)
    max_hyps = 12
    hyp_floor = 1e-7  # hypotheses with less weight are dropped


def stay_rates(share: float, scale: float, points=(86400, 14400, 2400, 400, 60, 10, 2), ratio=2.0) -> tuple:
    """A mixture of exponentials (weights, rates) for the heavy-tailed standing durations with
    survival (scale / (t + scale))^share: Feldmann & Whitt (1998), matching at the points."""
    def surv(t):
        return (scale / (t + scale)) ** share
    p, lam = [], []
    for c in points[:-1]:
        def rest(t):
            return surv(t) - sum(pj * math.exp(-lj * t) for pj, lj in zip(p, lam))
        rate = math.log(rest(c) / rest(ratio * c)) / ((ratio - 1) * c)
        p.append(rest(c) * math.exp(rate * c))
        lam.append(rate)
    last = 1 - sum(p)
    rest = surv(points[-1]) - sum(pj * math.exp(-lj * points[-1]) for pj, lj in zip(p, lam))
    lam.append(math.log(last / rest) / points[-1])
    p.append(last)
    return np.array(p), np.array(lam)


def radar_pd(r, r50: float):
    """Swerling I detection probability at distance r, half at r50 (radar equation, SNR ~ r^-4)."""
    s50 = math.log(P_FA) / math.log(0.5) - 1
    r = np.maximum(r, 0.3)
    return P_FA ** (1.0 / (1.0 + s50 * (r50 / r) ** 4))


class Shapes:
    """How long a stay lasts, as a finite mixture shared by the Gaussians and the tiles: every stay
    draws its rate of getting up go_l with weight go_w_l."""

    def __init__(self, m: Model):
        self.go_w, self.go = stay_rates(m.go_share, m.go_time)
        # a stay seen at a random moment is longer than a fresh one (renewal theory): its rate of
        # getting up weighted by its mean duration
        w = self.go_w / self.go
        self.go_w_ongoing = w / w.sum()
        self.kappa, self.kappa_w = gamma_levels(m.kappa_shape, m.kappa_levels)

    def stay_prior(self, ongoing: bool = False) -> np.ndarray:
        """(L, K) prior of a stay's kind and detectability (independent a priori)."""
        return np.outer(self.go_w_ongoing if ongoing else self.go_w, self.kappa_w)


def gamma_levels(shape: float, k: int) -> tuple:
    """Gamma(shape, rate shape) (mean 1) in k equally probable levels: (the mean of each level,
    their weights)."""
    x = np.exp(np.linspace(math.log(1e-8), math.log(60.0 / shape + 60.0), 200001))
    pdf = np.exp((shape - 1) * np.log(x) - shape * x + shape * math.log(shape) - math.lgamma(shape))
    dx = np.diff(x)
    mass = np.concatenate([[0.0], np.cumsum(0.5 * (pdf[1:] + pdf[:-1]) * dx)])
    first = np.concatenate([[0.0], np.cumsum(0.5 * (pdf[1:] * x[1:] + pdf[:-1] * x[:-1]) * dx)])
    idx = np.concatenate([[0], np.searchsorted(mass / mass[-1], np.arange(1, k) / k), [len(x) - 1]])
    vals = np.array([(first[b] - first[a]) / (mass[b] - mass[a]) for a, b in zip(idx[:-1], idx[1:])])
    return vals / vals.mean(), np.full(k, 1.0 / k)
