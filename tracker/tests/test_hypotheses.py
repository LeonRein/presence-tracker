"""The hypotheses (MODEL.md 5.1, 5.6) and the posterior grids of a stay's detectability and
amplitude (4.1, 4.3): alternatives alike are merged before they are cut, the cut drops at most
its mass and the evidence counts it, the grid keeps long unseen sitting as likely as the Gamma prior
says, and the joint prior of detectability and amplitude has the measured marginals."""

import math

import numpy as np

from presence_tracker.filter import Tracker, _odds_shifted
from presence_tracker.filtermodel import Model, Shapes, copula_cells, gamma_log_grid
from presence_tracker.hidden import Hidden
from presence_tracker.sim import Person, simulate

from test_existence import known
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def _children(crowd, logws, person_in):
    """Children of the one hypothesis that say the same about the live tracks (one key), with these
    log weights; those with person_in[i] hold a known person without a track."""
    parent = crowd.hyps[0]
    u = Hidden.anywhere(crowd.tiles)
    out = []
    for lw, has in zip(logws, person_in):
        ch = parent.child(lw)
        ch.hidden = [u] if has else []
        out.append(ch)
    return out


def test_alternatives_alike_are_merged_before_the_cut():
    # one strong child without the person, thirty weak ones (5e-8 each) with them: alone each is
    # below the cut (1e-7), together they are 1.5e-6 - the person stays with r = their share
    crowd = Tracker(flat_config(), start=0.0)
    n = 30
    crowd._take(_children(crowd, [0.0] + [math.log(5e-8)] * n, [False] + [True] * n))
    assert len(crowd.hyps) == 1
    (u,) = crowd.hyps[0].hidden
    assert abs(u.r - n * 5e-8 / (1 + n * 5e-8)) < 1e-12, u.r


def test_the_cut_drops_at_most_its_mass_and_the_evidence_counts_it():
    crowd = Tracker(flat_config(), start=0.0)
    parent = crowd.hyps[0]
    w = [1.0, 6e-8, 6e-8, 6e-8, 1e-9]
    crowd.hyps = []
    for k, wk in enumerate(w):
        ch = parent.child(math.log(wk))
        ch.kind = {f"s{k}": "g"}  # each says something else about the tracks: nothing to merge
        crowd.hyps.append(ch)
    before = crowd.loglik
    crowd._prune()
    # from the weakest up while the dropped ones weigh at most 1e-7: 1e-9 and one 6e-8
    assert sorted(round(math.exp(h.logw) / math.exp(crowd.hyps[0].logw), 12) for h in crowd.hyps) == [6e-8, 6e-8, 1.0]
    assert abs(sum(math.exp(h.logw) for h in crowd.hyps) - 1) < 1e-12
    # the evidence of this step is all the mass, of which the dropped part is reported
    assert abs(crowd.loglik - before - math.log(sum(w))) < 1e-12
    assert abs(crowd.loglik_cut - (-math.log1p(-6.1e-8 / sum(w)))) < 1e-15


def test_the_detectability_grid_keeps_long_unseen_sitting_as_the_prior_says():
    # E[exp(-kappa s)] of the grid against the Gamma(1) prior's 1 / (1 + s), s = rate x time unseen up
    # to 300 (a still person near one sensor, 0.034 /s, for 2.5 h): within 25 % everywhere (the
    # three Gauss-Laguerre nodes until 0.21: 0.40 at s = 20, 10 min, and 1e-7 at s = 70)
    s = np.exp(np.linspace(math.log(0.1), math.log(300.0), 40))
    sh = Shapes(Model())
    ratio = (sh.kappa_w[None, :] * np.exp(-np.outer(s, sh.kappa))).sum(axis=1) * (1 + s)
    assert ratio.min() > 0.75 and ratio.max() < 1.01, ratio


def test_detectability_and_amplitude_are_drawn_together():
    sh = Shapes(Model())
    assert np.allclose(sh.ka_w.sum(axis=1), sh.kappa_w) and np.allclose(sh.ka_w.sum(axis=0), sh.amp_w)
    eg = sh.amp_given_kappa @ sh.amp
    assert np.all(np.diff(eg) > 0), eg  # who is seen less reflects less
    assert np.allclose(copula_cells(sh.kappa_w, sh.amp_w, 0.0), np.outer(sh.kappa_w, sh.amp_w))
    k, w = gamma_log_grid(6.0, 9)
    assert abs(float(w @ k) - 1) < 1e-9


def test_a_sitter_no_ld2450_sees_for_a_quarter_of_an_hour_stays():
    # nothing known; somebody comes in and sits; then neither LD2450 sees them for 15 min, only the
    # LD2410C's energies (simulated, they fluctuate) say somebody is there: the light is on most of
    # the time and at the end, and nobody else is made up (the grid 0.89 of the time, end 0.98; the
    # quadrature until 0.21 0.86, 0.97, log evidence -14)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 3.0), (3.05, 3.0), start=5, pauses={3: 1200}))
    sensors = sim_sensors(config)
    sit = a.waypoints[3][0]
    for s in sensors:
        s.blind_to, s.blind_after, s.blind_until = (0,), sit + 10, sit + 910
    crowd = Tracker(config, start=0.0)
    on = []
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    for t, sid, frame in simulate([a], sensors, sit + 900, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if t > sit + 10:
            on.append(1 - crowd.count_distribution()["wohn"][0] > c)
    assert sum(on) / len(on) > 0.85 and on[-1], sum(on) / len(on)
    assert known(crowd) < 1.1, known(crowd)


def test_a_track_whose_ghost_alternative_is_all_but_gone_ends():
    # no live track loses an alternative (Tracker._cut), so "ghost" can weigh 1e-310 next to
    # "person" when the track ends; taking the map's factor out of those odds (_end) overflowed
    # (OverflowError in the ablation's replay of 8.10.)
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside"])
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.05, 2.5), start=1, pauses={2: 20}))
    for t, sid, frame in simulate([a], sim_sensors(config), 12.0, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    both = [s for s in crowd.segs if {hy.kind[s] == "g" for hy in crowd.hyps} == {True, False}]
    assert both, "a live track with both alternatives"
    seg = both[0]
    top = max(hy.logw for hy in crowd.hyps)
    for hy in crowd.hyps:
        if hy.kind[seg] == "g":
            hy.logw = top - 712.0  # P(ghost) ~ 1e-309 (subnormal)
    crowd._normalize()
    p = sum(w for w, hy in zip(crowd.hyp_weights(), crowd.hyps) if hy.kind[seg] == "g")
    assert 0 < p < 1e-300
    crowd.segs[seg]["map_odds"] = 5.0
    learned = []
    crowd.listeners.append(lambda kind, d: learned.append(d[2]) if kind == "track_end" else None)
    crowd._end(crowd.segs[seg]["si"], seg, crowd.now, False)
    assert seg not in crowd.segs and all(seg not in hy.kind for hy in crowd.hyps)
    assert len(learned) == 1 and 0 <= learned[0] < 1e-300


def test_shifting_odds_stays_finite():
    assert _odds_shifted(0.5, 0.0) == 0.5
    assert abs(_odds_shifted(0.2, math.log(4.0)) - 0.5) < 1e-12
    assert 0 < _odds_shifted(1e-310, -5.0) < 1e-310
    assert _odds_shifted(1 - 1e-16, 800.0) == 1.0 and _odds_shifted(0.0, 3.0) == 0.0
    # a known person's existence weighed by a factor beyond e^709 (Hidden.weigh): r = 1 - tiny, no overflow
    u = Hidden.anywhere(Tracker(flat_config(), start=0.0).tiles)
    u.r = 0.5
    n = u.tiles.n
    assert u.weigh(np.full(n, math.exp(700.0)), np.full(n, math.exp(700.0))) > 690 and u.r == 1.0
