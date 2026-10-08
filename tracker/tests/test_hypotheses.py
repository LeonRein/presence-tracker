"""The hypotheses (MODEL.md 5.1, 5.6) and the posterior grids of a stay's detectability and
amplitude (4.1, 4.3): alternatives alike are merged before they are cut, the cut drops at most
its mass and the evidence counts it, the grid keeps long unseen sitting as likely as the Gamma prior
says, and the joint prior of detectability and amplitude has the measured marginals."""

import math

import numpy as np

from presence_tracker.filter import Hyp, Tracker
from presence_tracker.filtermodel import Model, Shapes, copula_cells, gamma_log_grid, gamma_quadrature
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
    # as until 0.21: each child cut alone first, the person is gone
    crowd = Tracker(flat_config(), start=0.0)
    crowd.m.hyp_merge_first, crowd.m.hyp_cut = False, "relative"
    crowd._take(_children(crowd, [0.0] + [math.log(5e-8)] * n, [False] + [True] * n))
    assert crowd.hyps[0].hidden == []


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
    # to 300 (a still person near one sensor, 0.034 /s, for 2.5 h): within 25 % everywhere. The
    # quadrature (until 0.21) was 0.40 at s = 20 (10 min) and 1e-7 at s = 70
    s = np.exp(np.linspace(math.log(0.1), math.log(300.0), 40))
    sh = Shapes(Model())
    ratio = (sh.kappa_w[None, :] * np.exp(-np.outer(s, sh.kappa))).sum(axis=1) * (1 + s)
    assert ratio.min() > 0.75 and ratio.max() < 1.01, ratio
    k, w = gamma_quadrature(1.0, 3)
    old = (w[None, :] * np.exp(-np.outer(s, k))).sum(axis=1) * (1 + s)
    assert old.min() < 1e-6


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
