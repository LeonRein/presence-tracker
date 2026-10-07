"""The Gaussian people (gauss.py, MODEL.md 5.2) and what makes the filter deterministic."""

import math

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.filtermodel import STILL, WALK, Model, Shapes
from presence_tracker.gauss import Gauss
from presence_tracker.sim import Person

from test_filter import FLUR_DOOR, flat_config, run
from test_frames import walk


def walker(v=(0.8, 0.0)) -> Gauss:
    m, sh = Model(), Shapes(Model())
    mean = np.zeros((2, 2, 2))
    mean[WALK, :, 1] = v
    cov = np.zeros((2, 2, 2, 2))
    cov[:, :, 0, 0] = 0.01
    cov[WALK, :, 1, 1] = 0.05
    return Gauss(np.log([1e-300, 1.0]), mean, cov, sh.go_w.copy(), sh.kappa_w.copy())


def test_a_walk_does_not_depend_on_how_time_is_cut():
    # the walk's prediction is an exact discretization (MODEL.md 3.2): one step of 0.2 s is two of
    # 0.1 s (no mode changes: walks of infinite length)
    m, sh = Model(), Shapes(Model())
    m.walk_length = 1e12
    one, two = walker(), walker()
    one.predict(0.2, m, sh)
    two.predict(0.1, m, sh)
    two.predict(0.1, m, sh)
    assert np.allclose(one.mean[WALK], two.mean[WALK], atol=1e-12)
    assert np.allclose(one.cov[WALK], two.cov[WALK], atol=1e-12)


def test_a_walk_forgets_its_direction_at_the_measured_rate():
    # E[v(t)] = v(0) exp(-turn_rate t) (MODEL.md 3.2)
    m, sh = Model(), Shapes(Model())
    m.walk_length = 1e12
    g = walker()
    g.predict(2.0, m, sh)
    assert math.isclose(g.mean[WALK, 0, 1], 0.8 * math.exp(-2.0 * m.turn_rate), rel_tol=1e-9)


def test_the_same_data_give_the_same_result():
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (3.1, 2.5), start=3, pauses={2: 30}))
    end = a.waypoints[-1][0]
    _, s1 = run(config, [a], end, None)
    _, s2 = run(config, [a], end, None)
    assert [x[1] for x in s1] == [x[1] for x in s2]


def test_getting_up_after_a_long_stay_is_followed():
    # somebody sits for ten minutes and then walks off: a component with a small weight, lifted by
    # the first measurements of the walk - not a ghost, no second person (the morning episode)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (3.05, 2.5), (5.0, 1.0), (5.05, 1.0), start=3,
                    pauses={2: 600, 4: 20}))
    tr, samples = run(config, [a], a.waypoints[-1][0], None)
    t_up = a.waypoints[3][0]
    after = [n for t, _, n in samples if t_up + 3 <= t <= a.waypoints[-1][0]]
    assert after and all(n == 1 for n in after), after


def test_walls_turn_a_walk_but_leave_who_stands_at_one():
    # walls reflect what moves across them (MODEL.md 5.2): a walker heading for the wall stays in
    # the room, while somebody standing right at a wall (as measured) is not pushed off it - only
    # the step's motion meets the walls, not the spread the Gaussian already has
    tr = Tracker(flat_config())
    m, sh = Model(), Shapes(Model())
    m.walk_length = 1e12
    g = walker((0.0, 0.8))
    g.mean[:, :, 0] = (3.0, 4.5)
    free = g.copy()
    for _ in range(10):
        g.predict(0.2, m, sh, tr.tiles)
        free.predict(0.2, m, sh)
    assert free.pos[WALK, 1] > 5.2
    assert g.pos[WALK, 1] < 4.9 and g.mean[WALK, 1, 1] < 0.0  # turned at the wall at y = 5
    s = walker((0.0, 0.0))
    s.logw = np.log([1.0, 1e-300])
    s.mean[:, :, 0] = (3.0, 4.95)
    for _ in range(25):
        s.predict(0.2, Model(), sh, tr.tiles)
    assert abs(s.pos[STILL, 1] - 4.95) < 0.06  # only who got up meanwhile and walked into it turned


def test_rates_are_expected_over_the_component():
    # the rates of a wide component are the mean over its sigma points, not the value at its mean
    # (MODEL.md 5.2), and points across a wall are mirrored into the room (walls reflect)
    tr = Tracker(flat_config())
    tr._live_by_sensor = {}
    g = walker()
    g.mean[:, :, 0] = (4.5, 3.5)  # far from sensor a, where its rate falls with the distance
    g.cov[:, :, 0, 0] = 2.25  # 1.5 m: the sigma points reach 2.6 m, beyond the walls
    assert (tr.world.place_of(g.sigma(tr.world).reshape(-1, 2)) == 0).all()
    r = tr._gauss_rates(0, g)
    rw, _ = tr._rates(0, g.pos, masked=False)
    assert r.shape == (2, 5) and not np.isclose(tr._expect(r)[WALK], rw[WALK], rtol=0.05)
