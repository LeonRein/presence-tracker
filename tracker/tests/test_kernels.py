"""The compiled kernels (kernels.py): the tracker calls each with the types warm() compiles, so the
image's cache covers everything and the app never compiles while it runs (on Home Assistant that
would stop it for many seconds)."""

import math

import numpy as np

from presence_tracker import kernels, ld2410
from presence_tracker.filtermodel import Model
from presence_tracker.sim import Person

from test_filter import BALCONY_DOOR, FLUR_DOOR, flat_config, run
from test_frames import walk


def _kernels():
    return {n: k for n, k in vars(kernels).items() if hasattr(k, "signatures")}


def test_the_tracker_calls_the_kernels_only_as_warm_compiles_them():
    kernels.warm()
    before = {n: list(k.signatures) for n, k in _kernels().items()}
    assert all(len(s) == 1 for n, s in before.items() if not n.startswith("_")), before  # helpers: from the cache none
    config = flat_config()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (1.0, 1.0), (5.0, 4.0), (5.1, 4.0), start=1, pauses={3: 5}))
    b = Person(walk((7.0, 2.0), BALCONY_DOOR, (5.0, 1.0), (1.0, 4.0), (1.1, 4.0), start=1, pauses={3: 5}))
    crowd, _ = run(config, [a, b], 25, ["flur", "balkon"], bias=0.2, bias_time=3.5, resolution=0.5, ghost_rate=20)
    crowd.zone_states()
    after = {n: list(k.signatures) for n, k in _kernels().items()}
    assert after == before


def _censored_numpy(stats, mu, lik):
    """What Stats._mu_part added for the censored cells before kernels.ld_censored (0.19.0)."""
    a, tau = lik.alpha, lik.tau
    c = stats.t_cens > 0
    if not c.any():
        return np.zeros(len(mu))
    return ld2410.log_censored(a[c][None, :], mu[:, c]) @ (stats.t_cens[c] / tau[c])


def _censored_kernel(stats, mu, lik):
    c = np.flatnonzero(stats.t_cens > 0)
    rows, tables = ld2410._censored_tables(lik.alpha)
    m = np.ascontiguousarray(mu)
    with np.errstate(divide="ignore", invalid="ignore"):
        lm = np.log(m)
    return kernels.ld_censored(m, lm, c, rows[c], tables, ld2410._GRID) @ (stats.t_cens[c] / lik.tau[c])


def test_the_censored_cells_kernel_is_log_censored():
    rng = np.random.default_rng(1)
    lik = ld2410.Likelihood(Model())
    grid = ld2410._GRID
    for k in range(20):
        st = ld2410.Stats()
        for _ in range(rng.integers(1, 30)):  # energies at 100 (censored) in some cells
            e = rng.integers(0, 101, ld2410.CELLS).astype(float)
            e[rng.random(ld2410.CELLS) < 0.3] = 100.0
            st.add(e, 0.1)
        # means from below the grid to above it, some exactly on grid points and at its edges
        mu = np.exp(rng.uniform(grid[0] - 2, grid[-1] + 2, (int(rng.integers(1, 40)), ld2410.CELLS)))
        mu[0, :5] = np.exp(grid[[0, 1, 2000, 3999, 4000]])
        mu[-1, rng.integers(0, ld2410.CELLS)] = 0.0  # no energy expected: the lowest
        with np.errstate(divide="ignore"):
            assert np.array_equal(_censored_kernel(st, mu, lik), _censored_numpy(st, mu, lik))  # bit for bit
    st = ld2410.Stats()
    st.add(np.full(ld2410.CELLS, 100.0), 0.1)
    mu = np.ones((2, ld2410.CELLS))
    mu[1, 3] = np.nan  # NaN stays NaN (the app restarts the model on it)
    assert np.isnan(_censored_kernel(st, mu, lik)[1]) and np.isfinite(_censored_kernel(st, mu, lik)[0])
    assert len(_censored_kernel(st, np.ones((0, ld2410.CELLS)), lik)) == 0  # no rows
    st = ld2410.Stats()
    st.add(np.full(ld2410.CELLS, 50.0), 0.1)  # nothing censored: no cells
    assert np.array_equal(_censored_kernel(st, np.ones((3, ld2410.CELLS)), lik), np.zeros(3))


def test_the_censored_cells_kernel_interpolates_as_np_interp_on_the_grid():
    grid = ld2410._GRID
    tab = ld2410.log_gammaq(2.0, ld2410.CAP * 2.0 / np.exp(grid))
    x = np.concatenate([grid[[0, 1, 17, 2000, 3999, 4000]], np.nextafter(grid[[0, 17, 4000]], -np.inf),
                        np.nextafter(grid[[0, 17, 4000]], np.inf), [grid[0] - 5, grid[-1] + 5],
                        np.random.default_rng(2).uniform(grid[0], grid[-1], 500)])
    lm = np.ascontiguousarray(x[:, None])
    got = kernels.ld_censored(np.exp(lm), lm, np.zeros(1, dtype=np.int64), np.zeros(1, dtype=np.int64),
                              tab[None, :], grid)
    assert np.array_equal(got[:, 0], np.interp(x, grid, tab))
    assert kernels.ld_censored(np.exp(lm), lm, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64),
                               tab[None, :], grid).shape == (len(x), 0)  # no censored cells


def test_the_gauss_points_kernel_is_the_numpy_formula_bit_for_bit():
    rng = np.random.default_rng(3)
    for _ in range(20):
        pts = rng.uniform(-5, 15, (int(rng.integers(0, 3000)), 2))
        mean = rng.uniform(-2, 12, 2)
        var = rng.uniform(1e-4, 10, 2)
        ref = np.exp(-0.5 * (((pts - mean[None, :]) ** 2) / var[None, :]).sum(axis=1))
        got = np.exp(kernels.gauss_exponents(pts, float(mean[0]), float(mean[1]), float(var[0]), float(var[1])))
        assert np.array_equal(got, ref)
    assert kernels.gauss_exponents(np.array([[1.0, 2.0]]), 1.0, 2.0, 0.5, 0.5)[0] == 0.0  # at the mean
    assert len(kernels.gauss_exponents(np.zeros((0, 2)), 0.0, 0.0, 1.0, 1.0)) == 0


def test_the_sum_kernel_is_numpys_pairwise_sum_bit_for_bit():
    rng = np.random.default_rng(4)
    for n in list(range(0, 300)) + [687, 1000, 4097, 17175, 100003]:
        a = rng.uniform(0, 1, n) * 10.0 ** rng.uniform(-12, 0, n)
        assert kernels._npsum(a) == a.sum()
        b = rng.uniform(0, 1, (5, 5, max(n, 1)))
        assert kernels._npsum(b) == b.sum()


def _weigh_numpy(walk, still, region, out, f_walk, f_still, intensity):
    """Hidden.weigh / Undetected.weigh before kernels.hidden_weigh (0.28.0)."""
    if intensity:
        before = float(walk.sum() + still.sum())
        walk *= f_walk
        still *= f_still
        return -(before - float(walk.sum() + still.sum())), out
    walk *= f_walk
    still *= f_still
    tot = float(walk.sum() + still.sum() + region.sum() + out)
    if tot <= 0:
        return -np.inf, out
    k = 1.0 / tot
    walk *= k
    still *= k
    region *= k
    return math.log(tot), out * k


def test_the_weigh_kernel_is_the_numpy_arithmetic_bit_for_bit():
    rng = np.random.default_rng(5)
    for trial in range(60):
        n = int(rng.integers(1, 900))
        walk = rng.uniform(0, 1, n) * 10.0 ** rng.uniform(-9, 0, n)
        still = rng.uniform(0, 1, (5, 3, n)) * 10.0 ** rng.uniform(-9, 0, (5, 3, n))
        region = rng.uniform(0, 1, (2, 18))
        out = float(rng.uniform(0, 1))
        f_walk = rng.uniform(0, 1, n)
        f_still = rng.uniform(0, 1, (3, n)) if trial % 2 else rng.uniform(0, 1, (1, n))
        if trial == 7:  # nothing left
            f_walk[:] = 0.0
            f_still[:] = 0.0
            region[:] = 0.0
            out = 0.0
        for intensity in (False, True):
            a = [x.copy() for x in (walk, still, region)]
            b = [x.copy() for x in (walk, still, region)]
            got = kernels.hidden_weigh(*a, out, f_walk, f_still, intensity)
            ref = _weigh_numpy(*b, out, f_walk, f_still, intensity)
            assert got == ref
            assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_the_gauss_rates_kernel_is_the_sum_of_sensor_rates_bit_for_bit():
    rng = np.random.default_rng(6)
    acq = np.array([0.9, 4.0, 0.5, 3.0])
    res = np.array([0.4, 0.6])
    for _ in range(30):
        S = int(rng.integers(1, 4))
        grids = rng.uniform(0, 1, (S, 40, 30))
        sxy = rng.uniform(0, 4, (S, 2))
        lives = [np.column_stack([rng.integers(0, 2, m).astype(float), rng.uniform(0, 4, (m, 2))])
                 for m in rng.integers(0, 4, S)]
        ptr = np.cumsum([0] + [len(x) for x in lives]).astype(np.int64)
        live = np.concatenate(lives).reshape(-1, 3)
        use = rng.random(S) < 0.8
        pts = rng.uniform(-0.5, 4.5, (2, 5, 2))
        ref = 0
        for s in range(S):  # as Tracker._sync summed _gauss_rates before (0.28.0)
            if not use[s]:
                ref = ref + np.zeros((2, 5))
                continue
            r = kernels.sensor_rates(pts.reshape(-1, 2), np.zeros((2, 0)), sxy[s, 0], sxy[s, 1], grids[s], 0.0, 0.0, 0.1,
                                     acq, 1e-6, 1.0, lives[s], res, 0.7)
            ref = ref + np.stack([r[1, :5], r[0, 5:]])
        got = kernels.gauss_rates(pts, use, sxy, grids, live, ptr, 0.0, 0.0, 0.1, acq, 1e-6, 1.0, res, 0.7)
        assert np.array_equal(got, ref)
