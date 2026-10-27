"""The compiled kernels (kernels.py): the tracker calls each with the types warm() compiles, so the
image's cache covers everything and the app never compiles while it runs (on Home Assistant that
would stop it for many seconds)."""

import math

import numpy as np

from presence_tracker import gauss, kernels, ld2410
from presence_tracker.filtermodel import Model, Shapes
from presence_tracker.sim import Person
from presence_tracker.world import CELL, World

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


def _sigma_numpy(world, pos, mean, var):
    """Gauss.sigma with the world before kernels.sigma_points (0.28.0)."""
    sd = np.sqrt(np.maximum(var, 0.0))
    pts = mean[:, None, :] + gauss.UNIT[None, :, :] * sd[:, None, :]
    near = []
    for k, ((x, y), (mx, my), (sx, sy)) in enumerate(zip(pos.tolist(), mean.tolist(), sd.tolist())):
        if not world.clear(x, y, abs(mx - x) + abs(my - y) + gauss.UNIT_REACH * max(sx, sy)):
            near.append(k)
    if not near:
        return pts
    n = pts.shape[1]
    p0 = np.repeat(pos[near], n, axis=0)
    pts[near] = world.reflect(p0, pts[near].reshape(-1, 2))[0].reshape(len(near), n, 2)
    return pts


def test_the_sigma_points_kernel_is_the_numpy_bit_for_bit():
    world = World(flat_config())
    rng = np.random.default_rng(7)
    mirrored = 0
    for _ in range(400):
        pos = rng.uniform(-0.5, 7.0, (2, 2))
        mean = pos + rng.normal(0, 0.3, (2, 2)) * (rng.random() < 0.5)
        var = rng.uniform(0, 1.5, (2, 2)) ** 2
        var[rng.random((2, 2)) < 0.05] = -1e-9  # rounding below 0
        ref = _sigma_numpy(world, pos, mean, var)
        got = kernels.sigma_points(pos, mean, var, gauss.UNIT, gauss.UNIT_REACH, world.wall_grid, world.x0, world.y0,
                                   CELL, world.walls, world.wall_box)
        assert np.array_equal(got, ref)
        plain = mean[:, None, :] + gauss.UNIT[None, :, :] * np.sqrt(np.maximum(var, 0.0))[:, None, :]
        mirrored += not np.array_equal(ref, plain)
    assert mirrored > 20  # walls were met


def _mu_part_numpy(st, mu, lik):
    """ld2410.Stats._mu_part before the kernels.ld_gain_* (0.28.0)."""
    a, tau = lik.alpha, lik.tau
    look = a * st.t_unc / tau
    out = -(np.log(mu) @ look)
    inv = 1.0 / mu
    ae = a * st.t_e / tau
    for cells, comps in lik.kinds:
        N = float(look[cells].sum())
        if N <= 0:
            continue
        A = inv[:, cells] @ ae[cells]
        terms = np.array([c + math.lgamma(N + k) - (N + k) * np.log(r + A) for c, k, r in comps])
        top = terms.max(axis=0)
        out += top + np.log(np.exp(terms - top).sum(axis=0))
    c = np.flatnonzero(st.t_cens > 0)
    if len(c):
        rows, tables = ld2410._censored_tables(a)
        m = np.ascontiguousarray(mu)
        out += kernels.ld_censored(m, np.log(m), c, rows[c], tables, ld2410._GRID) @ (st.t_cens[c] / tau[c])
    return out


def test_the_ld2410_likelihood_is_the_numpy():
    rng = np.random.default_rng(8)
    lik = ld2410.Likelihood(Model())
    for trial in range(200):
        st = ld2410.Stats()
        for _ in range(rng.integers(0 if trial % 10 == 0 else 1, 20)):
            e = rng.integers(0, 101, ld2410.CELLS).astype(float)
            e[rng.random(ld2410.CELLS) < 0.1] = 100.0
            if trial % 7 == 0:
                e[9:] = 100.0  # the still cells all censored: no looks there
            st.add(e, float(rng.uniform(0.05, 1.0)))
        mu = np.exp(rng.uniform(-3, 6, (int(rng.integers(1, 300)), ld2410.CELLS)))
        np.testing.assert_allclose(st._mu_part(mu, lik), _mu_part_numpy(st, mu, lik), rtol=1e-12, atol=1e-9)


def _predict_numpy(g, dt, m, sh):
    """Gauss.predict before kernels.gauss_predict (0.28.0), without walls and doors."""
    w = g.weights()
    stayed = g.gow * np.exp(-sh.go * dt)
    p_stay = float(stayed.sum())
    p_stop = -math.expm1(-m.speed / m.walk_length * dt)
    s2 = 0.5 * (m.speed ** 2 + m.speed_spread ** 2)
    ao = math.exp(-dt / m.tau)
    F, Q = kernels.motion(g.mean.shape[2], float(dt), float(m.still_noise ** 2), 1.0 / m.turn_rate, float(s2), ao,
                          np.array(list(g.slots.values()), dtype=np.int64),
                          np.array([g.var[seg] * (1 - m.const_share) * (1 - ao * ao) for seg in g.slots]))
    tot_s, tot_w, w_ss, w_ws, m0, P0, g.mean, g.cov = kernels.imm_predict(
        w, np.ascontiguousarray(g.mean), np.ascontiguousarray(g.cov), p_stay, p_stop, s2, F, Q)
    if tot_s > 0:
        fresh = sh.go_w
        kept = stayed / p_stay if p_stay > 0 else fresh
        g.gow = (w_ss * kept + w_ws * fresh) / tot_s
        q = -math.expm1(-m.kappa_switch * dt)
        ka = (1 - q) * g.ka + q * sh.ka_w
        g.ka = (w_ss * ka + w_ws * sh.ka_w) / tot_s
    g.logw = gauss._log(np.array([tot_s, tot_w]))
    top = g.logw.max()
    g.logw -= top + math.log(float(np.exp(g.logw - top).sum()))


def _random_gauss(rng, sh, tracks):
    d = 2 + 2 * tracks
    mean = rng.normal(0, 1, (2, 2, d)) + 3.0
    A = rng.normal(0, 0.3, (2, 2, d, d))
    cov = A @ np.swapaxes(A, 2, 3) + 1e-3 * np.eye(d)
    gow = rng.uniform(0, 1, len(sh.go_w))
    ka = rng.uniform(0, 1, sh.ka_w.shape)
    logw = np.log(rng.dirichlet([1.0, 1.0]))
    slots = {f"s{k}": 2 + 2 * k for k in range(tracks)}
    var = {f"s{k}": float(rng.uniform(0.01, 0.3)) for k in range(tracks)}
    return gauss.Gauss(logw, mean, cov, gow / gow.sum(), ka / ka.sum(), slots, var)


def test_the_predict_kernel_is_the_numpy():
    rng = np.random.default_rng(10)
    m, sh = Model(), Shapes(Model())
    for trial in range(200):
        g = _random_gauss(rng, sh, trial % 3)
        if trial % 11 == 0:
            g.logw = np.array([-np.inf, 0.0])  # nobody stands
        ref = g.copy()
        dt = float(rng.uniform(0.01, 0.4))
        g.predict(dt, m, sh)
        _predict_numpy(ref, dt, m, sh)
        for a, b in ((g.mean, ref.mean), (g.cov, ref.cov), (g.gow, ref.gow), (g.ka, ref.ka), (g.logw, ref.logw)):
            np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-12)


def _near_sigma_numpy(g, z, radius, world):
    """Gauss.near_sigma before kernels.near_parts (0.28.0)."""
    P, r2 = g.pos_var(), radius ** 2
    z = np.asarray(z)[None, :]
    mean = (g.pos * r2 + z * P) / (P + r2)
    v = radius ** 2 + g.pos_var()
    d2 = (g.pos - z[0][None, :]) ** 2
    k = np.prod(np.sqrt(radius ** 2 / v) * np.exp(-0.5 * d2 / v), axis=1)
    return k, _sigma_numpy(world, g.pos, mean, P * r2 / (P + r2))


def test_the_near_sigma_kernel_is_the_numpy_bit_for_bit():
    rng = np.random.default_rng(11)
    world = World(flat_config())
    sh = Shapes(Model())
    for _ in range(300):
        g = _random_gauss(rng, sh, 1)
        g.mean[:, :, 0] = rng.uniform(-0.5, 7.0, (2, 2))
        z = rng.uniform(-0.5, 7.0, 2)
        radius = float(rng.uniform(0.2, 1.5))
        k, pts = g.near_sigma(z, radius, world)
        k_ref, pts_ref = _near_sigma_numpy(g, z, radius, world)
        assert np.array_equal(k, k_ref) and np.array_equal(pts, pts_ref)


def test_the_ld2410_profile_rows_are_expected_bit_for_bit():
    rng = np.random.default_rng(12)
    m = Model()
    for _ in range(200):
        k = int(rng.integers(1, 6))
        angle = rng.uniform(0, 90, k)
        slant = rng.uniform(0.0, 8.0, k)
        sight = rng.uniform(0, 1, k) * (rng.random(k) < 0.8)
        modes = tuple(int(x) for x in rng.integers(0, 2, k))
        rows = ld2410.expected_rows(m, angle, slant, sight, modes)
        ref = ld2410.expected(m, angle, slant, sight)
        for j, mode in enumerate(modes):
            assert np.array_equal(rows[j], ref[mode][j])


def test_the_ld2410_ratio_is_the_numpy():
    rng = np.random.default_rng(13)
    lik = ld2410.Likelihood(Model())
    for trial in range(100):
        st = ld2410.Stats()
        for _ in range(rng.integers(1, 20)):
            st.add(rng.integers(0, 101, ld2410.CELLS).astype(float), float(rng.uniform(0.05, 1.0)))
        mu0 = np.exp(rng.uniform(0, 3, ld2410.CELLS))
        S = np.exp(rng.uniform(-3, 6, (int(rng.integers(1, 400)), ld2410.CELLS))) * (rng.random() < 0.9)
        m = rng.uniform(0, 1, len(S)) / len(S) * (rng.random(len(S)) < 0.7)
        intensity = trial % 3 == 0
        lr, norm, post = st.ratio(m, S, mu0, lik, intensity)
        both = _mu_part_numpy(st, np.vstack([mu0[None, :] + S, mu0[None, :]]), lik)
        ref = np.where(m > 0, np.minimum(both[:-1] - both[-1], ld2410.LOG_CAP), -ld2410.LOG_CAP)  # no mass: not computed
        top = max(float(ref.max()), 0.0)
        inside = float(m @ np.exp(ref - top))
        ref_norm = (top + math.log(math.exp(-top) + inside) - math.log1p(m.sum()) if intensity
                    else top + math.log(max(1.0 - m.sum(), 0.0) * math.exp(-top) + inside))
        np.testing.assert_allclose(lr, ref, rtol=1e-12, atol=1e-9)
        assert math.isclose(norm, ref_norm, rel_tol=1e-12, abs_tol=1e-9)
        np.testing.assert_allclose(post, (m * np.exp(ref - ref_norm)) @ S, rtol=1e-11, atol=1e-12)


def test_the_vectorized_log_and_exp_are_numpys_within_one_ulp():
    rng = np.random.default_rng(15)
    with np.errstate(all="ignore"):
        for f, ref, x in ((kernels.vlog, np.log, np.concatenate([np.exp(rng.uniform(-700, 700, 20000)), rng.uniform(0, 3, 20000),
                                                                   [0.0, -1.0, np.inf, np.nan, 5e-324, 1e-310, 1.0, 2.0, math.sqrt(2)]])),
                          (kernels.vexp, np.exp, np.concatenate([rng.uniform(-745, 709.8, 20000), rng.uniform(-1e-9, 1e-9, 1000),
                                                                   [0.0, np.inf, -np.inf, np.nan, 709.0, 710.0, -708.0, -746.0]]))):
            got, want = f(x, np.empty_like(x)), ref(x)
            assert np.array_equal(np.isnan(got), np.isnan(want))
            exact = ~np.isfinite(want) | (want == 0)
            assert np.array_equal(got[exact & ~np.isnan(want)], want[exact & ~np.isnan(want)])
            fin = ~exact
            assert (np.abs(got[fin] - want[fin]) <= np.spacing(np.abs(want[fin]))).all()


def test_the_tile_mass_and_distance_kernels_are_the_numpy_bit_for_bit():
    rng = np.random.default_rng(14)
    for _ in range(50):
        n = int(rng.integers(1, 800))
        walk, still = rng.uniform(0, 1, n) * 1e-3, rng.uniform(0, 1, (5, 3, n)) * 1e-4
        idx = np.sort(rng.choice(n, int(rng.integers(0, n + 1)), replace=False)).astype(np.int64)
        share = float(rng.uniform(0, 1))
        assert np.array_equal(kernels.ld_tile_masses(walk, still, idx, share),
                              share * np.concatenate([walk[idx], still.sum(axis=(0, 1))[idx]]))
        walk2, still2 = rng.uniform(0, 1, n) * 1e-3, rng.uniform(0, 1, (5, 3, n)) * 1e-4
        region, region2 = rng.uniform(0, 1, (2, 18)), rng.uniform(0, 1, (2, 18))
        a, b, out, out2 = (float(x) for x in rng.uniform(0, 1, 4))
        ref = float(np.abs(a * walk - b * walk2).sum() + np.abs(a * still - b * still2).sum()
                    + np.abs(a * region - b * region2).sum() + abs(a * out - b * out2))
        assert kernels.hidden_distance(a, walk, still, region, out, b, walk2, still2, region2, out2) == ref


def test_the_weights_of_a_mixture_are_the_numpy_bit_for_bit():
    rng = np.random.default_rng(15)
    sh = Shapes(Model())
    for trial in range(300):
        g = _random_gauss(rng, sh, 0)
        g.logw = rng.normal(0, 30, 2)
        if trial % 10 == 0:
            g.logw[rng.integers(0, 2)] = -np.inf
        if trial % 50 == 0:
            g.logw[:] = -np.inf
        top = g.logw.max()
        ref = np.array([0.5, 0.5]) if top == -np.inf else np.exp(g.logw - top) / np.exp(g.logw - top).sum()
        assert np.array_equal(g.weights(), ref)
        dlog = rng.normal(0, 5, 2)
        lw = g.logw + dlog
        top = lw.max()
        comps = top + math.log(float(np.exp(lw - top).sum())) if top > -math.inf else -math.inf
        h = g.copy()
        assert h.reweigh(dlog) == (comps if comps > -math.inf else -math.inf)
        if comps > -math.inf:
            assert np.array_equal(h.logw, lw - comps)


def test_the_measured_tile_points_kernel_is_the_numpy_bit_for_bit():
    rng = np.random.default_rng(16)
    amp = np.append(np.exp(rng.normal(0, 0.5, 9)), 1.0)
    for _ in range(100):
        n = int(rng.integers(1, 700))
        idx = np.sort(rng.choice(n, int(rng.integers(0, min(n, 300) + 1)), replace=False)).astype(np.int64)
        m = len(idx)
        walk = rng.uniform(0, 1, n)
        still = rng.uniform(0, 1, (len(amp), m)) * (rng.random((len(amp), m)) < 0.7)
        ms = still.sum(axis=0)
        big = rng.random(m) < 0.4
        ss, sw = rng.uniform(0, 50, (m, ld2410.CELLS)), rng.uniform(0, 50, (m, ld2410.CELLS))
        share = float(rng.uniform(0, 1))
        small = ~big
        gm = np.divide(amp @ still[:, small], ms[small], out=np.ones(int(small.sum())), where=ms[small] > 0)
        S = np.concatenate([sw, (amp[:, None, None] * ss[big][None, :, :]).reshape(-1, ld2410.CELLS), gm[:, None] * ss[small]])
        mass = share * np.concatenate([walk[idx], still[:, big].ravel(), ms[small]])
        got = kernels.ld_tile_points(walk, still, ms, big, amp @ still[:, small], idx, sw, ss, amp, share)
        assert np.array_equal(got[0], mass) and np.array_equal(got[1], S)


def test_weighing_at_some_tiles_is_weighing_with_ones_elsewhere_bit_for_bit():
    rng = np.random.default_rng(17)
    for trial in range(60):
        n = int(rng.integers(1, 800))
        idx = np.sort(rng.choice(n, int(rng.integers(0, n + 1)), replace=False)).astype(np.int64)
        walk = rng.uniform(0, 1, n) * 1e-3
        still = rng.uniform(0, 1, (5, 3, n)) * 1e-4
        region = rng.uniform(0, 1, (2, 18))
        out = float(rng.uniform(0, 1))
        fw = rng.uniform(0, 2, len(idx))
        fs = rng.uniform(0, 2, (3 if trial % 2 else 1, len(idx)))
        full_w, full_s = np.ones(n), np.ones((fs.shape[0], n))
        full_w[idx], full_s[:, idx] = fw, fs
        for intensity in (False, True):
            a = [x.copy() for x in (walk, still, region)]
            b = [x.copy() for x in (walk, still, region)]
            assert (kernels.hidden_weigh_at(*a, out, idx, fw, fs, intensity)
                    == kernels.hidden_weigh(*b, out, full_w, full_s, intensity))
            assert all(np.array_equal(x, y) for x, y in zip(a, b))
