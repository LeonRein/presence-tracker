"""Compiled inner loops (Numba, MODEL.md 10 "Rechenzeit"): the same arithmetic as the numpy they
replace, written as loops - most of the computing time was numpy's cost per call on small arrays,
not the arithmetic. No fastmath: IEEE arithmetic as in numpy; sums may run in another order (results
equal to rounding). Compiled on first use and cached (NUMBA_CACHE_DIR)."""

import math

import numpy as np
from numba import njit


@njit(cache=True)
def _collapse2(w1, m1, P1, w2, m2, P2):
    """gauss._collapse of two weighted Gaussians per axis (mean (2, d), cov (2, d, d))."""
    tot = w1 + w2
    if tot <= 0:
        return 0.0, m1.copy(), P1.copy()
    m = (w1 * m1 + w2 * m2) / tot
    P = np.zeros_like(P1)
    A, d = m.shape
    if w1 > 0:
        for a in range(A):
            for i in range(d):
                for j in range(d):
                    P[a, i, j] += w1 * (P1[a, i, j] + (m1[a, i] - m[a, i]) * (m1[a, j] - m[a, j]))
    if w2 > 0:
        for a in range(A):
            for i in range(d):
                for j in range(d):
                    P[a, i, j] += w2 * (P2[a, i, j] + (m2[a, i] - m[a, i]) * (m2[a, j] - m[a, j]))
    return tot, m, P / tot


@njit(cache=True)
def imm_predict(w, mean, cov, p_stay, p_stop, v_walk, F, Q):
    """gauss.Gauss.predict: the mode changes matched to one Gaussian per mode (IMM; standing ->
    walking with velocity variance v_walk), then the linear prediction F, Q (2, d, d). Returns the
    modes' weights before the prediction, the means and covariances before (for the walls) and
    after."""
    S, W = 0, 1
    w_ss, w_sw = w[S] * p_stay, w[S] * (1 - p_stay)
    w_ww, w_ws = w[W] * (1 - p_stop), w[W] * p_stop
    mS, PS, mW, PW = mean[S], cov[S], mean[W], cov[W]
    mWS, PWS = mW.copy(), PW.copy()
    mSW, PSW = mS.copy(), PS.copy()
    mWS[:, 1] = 0.0
    PWS[:, 1, :] = 0.0
    PWS[:, :, 1] = 0.0
    mSW[:, 1] = 0.0
    PSW[:, 1, :] = 0.0
    PSW[:, :, 1] = 0.0
    PSW[:, 1, 1] = v_walk
    tot_s, mS, PS = _collapse2(w_ss, mS, PS, w_ws, mWS, PWS)
    tot_w, mW, PW = _collapse2(w_ww, mW, PW, w_sw, mSW, PSW)
    A, d = mS.shape
    m0 = np.empty((2, A, d))
    P0 = np.empty((2, A, d, d))
    m0[S], m0[W], P0[S], P0[W] = mS, mW, PS, PW
    m1 = np.zeros((2, A, d))
    P1 = np.empty((2, A, d, d))
    FP = np.empty((d, d))
    for k in range(2):
        for a in range(A):
            for i in range(d):
                s = 0.0
                for j in range(d):
                    s += F[k, i, j] * m0[k, a, j]
                m1[k, a, i] = s
            for i in range(d):
                for l in range(d):
                    s = 0.0
                    for j in range(d):
                        s += F[k, i, j] * P0[k, a, j, l]
                    FP[i, l] = s
            for i in range(d):
                for n in range(d):
                    s = 0.0
                    for l in range(d):
                        s += FP[i, l] * F[k, n, l]
                    P1[k, a, i, n] = s + Q[k, i, n]
    return tot_s, tot_w, w_ss, w_ws, m0, P0, m1, P1


@njit(cache=True)
def gauss_predict(w, mean, cov, gow, ka, e_go, go_w, ka_w, p_stop, s2, dt, still_q, tau, ao, slot_k, slot_q, q):
    """gauss.Gauss.predict up to the weights' logarithm: the kinds of stay (gow times e_go = exp(-go dt)
    stayed, p_stay of them), motion and imm_predict, then the kinds of stay and the detectability with
    the LD2410C amplitude (ka, drawn anew with q) after the mode changes. The same arithmetic as the
    numpy it replaces: results equal bit for bit. Returns (tot_s, tot_w, m0, P0, mean, cov, F, Q, gow,
    ka) - gow and ka as they were if nobody stands."""
    stayed = gow * e_go
    p_stay = _pairwise(stayed)
    F, Q = motion(mean.shape[2], dt, still_q, tau, s2, ao, slot_k, slot_q)
    tot_s, tot_w, w_ss, w_ws, m0, P0, m1, P1 = imm_predict(w, mean, cov, p_stay, p_stop, s2, F, Q)
    if tot_s > 0:
        kept = stayed / p_stay if p_stay > 0 else go_w
        gow = (w_ss * kept + w_ws * go_w) / tot_s
        ka = (w_ss * ((1 - q) * ka + q * ka_w) + w_ws * ka_w) / tot_s
    return tot_s, tot_w, m0, P0, m1, P1, F, Q, gow, ka


@njit(cache=True)
def motion(d, dt, still_q, tau, s2, ao, slot_k, slot_q):
    """gauss.Gauss.predict: F and Q (2 modes, d, d) of one step dt - standing: the position
    diffuses (still_q per s); walking: the OU velocity (correlation time tau, stationary variance
    s2 per axis), exactly discretized; the tracks' wandering offsets o (index slot_k + 1) decay
    with ao and get slot_q."""
    F = np.zeros((2, d, d))
    Q = np.zeros((2, d, d))
    for k in range(2):
        for i in range(d):
            F[k, i, i] = 1.0
    F[0, 1, 1] = 0.0
    Q[0, 0, 0] = still_q * dt
    a = math.exp(-dt / tau)
    F[1, 0, 1] = tau * (1 - a)
    F[1, 1, 1] = a
    Q[1, 1, 1] = s2 * (1 - a * a)
    Q[1, 0, 1] = s2 * tau * (1 - a) ** 2
    Q[1, 1, 0] = Q[1, 0, 1]
    Q[1, 0, 0] = 2 * s2 * tau * (dt - 2 * tau * (1 - a) + 0.5 * tau * (1 - a * a))
    for n in range(len(slot_k)):
        k = slot_k[n] + 1
        for mode in range(2):
            F[mode, k, k] = ao
            Q[mode, k, k] = slot_q[n]
    return F, Q


@njit(cache=True)
def reflect(p0, p1, walls, box):
    """world.World.reflect: per move p0 -> p1 (n, 2) mirrored at the first wall (walls (W, 4),
    their bounding boxes box (W, 4)) it crosses, and that wall's unit normal (0: none)."""
    n = len(p1)
    out = p1.copy()
    normal = np.zeros_like(out)
    if n == 0 or len(walls) == 0:
        return out, normal
    lo0, lo1, hi0, hi1 = np.inf, np.inf, -np.inf, -np.inf
    for i in range(n):
        lo0 = min(lo0, p0[i, 0], p1[i, 0])
        hi0 = max(hi0, p0[i, 0], p1[i, 0])
        lo1 = min(lo1, p0[i, 1], p1[i, 1])
        hi1 = max(hi1, p0[i, 1], p1[i, 1])
    for i in range(n):
        dx, dy = p1[i, 0] - p0[i, 0], p1[i, 1] - p0[i, 1]
        best, k = np.inf, -1
        for w in range(len(walls)):
            if not (box[w, 0] <= hi0 and box[w, 2] >= lo0 and box[w, 1] <= hi1 and box[w, 3] >= lo1):
                continue
            ax, ay = walls[w, 0], walls[w, 1]
            ex, ey = walls[w, 2] - ax, walls[w, 3] - ay
            wx, wy = ax - p0[i, 0], ay - p0[i, 1]
            den = dx * ey - dy * ex
            if den == 0:
                continue
            t = (wx * ey - wy * ex) / den
            u = (wx * dy - wy * dx) / den
            if t > 0 and t < 1 and u > 0 and u < 1 and t < best:
                best, k = t, w
        if k < 0:
            continue
        ex, ey = walls[k, 2] - walls[k, 0], walls[k, 3] - walls[k, 1]
        h = math.hypot(ex, ey)
        nx, ny = -ey / h, ex / h
        off = (p1[i, 0] - walls[k, 0]) * nx + (p1[i, 1] - walls[k, 1]) * ny
        out[i, 0] -= 2 * off * nx
        out[i, 1] -= 2 * off * ny
        normal[i, 0], normal[i, 1] = nx, ny
    return out, normal


@njit(cache=True)
def sigma_points(pos, mean, var, unit, reach, wall_grid, x0, y0, cell, walls, box):
    """gauss.Gauss.sigma with the world: per mode k the points mean[k] + unit * sd (P, 2), those of a
    mode with a wall within reach of them (seen from where the component is, pos[k]: world.World.clear)
    mirrored at the first wall their move from pos[k] crosses (reflect, with the same moves). The same
    arithmetic as the numpy it replaces: results equal bit for bit. (K, P, 2)."""
    K = pos.shape[0]
    P = unit.shape[0]
    nx, ny = wall_grid.shape
    pts = np.empty((K, P, 2))
    near = np.zeros(K, dtype=np.bool_)
    m = 0
    for k in range(K):
        sx = var[k, 0] if not var[k, 0] < 0.0 else 0.0
        sy = var[k, 1] if not var[k, 1] < 0.0 else 0.0
        sx, sy = math.sqrt(sx), math.sqrt(sy)
        for p in range(P):
            pts[k, p, 0] = mean[k, 0] + unit[p, 0] * sx
            pts[k, p, 1] = mean[k, 1] + unit[p, 1] * sy
        x, y = pos[k, 0], pos[k, 1]
        r = abs(mean[k, 0] - x) + abs(mean[k, 1] - y) + reach * max(sx, sy)
        i = min(max(int((x - x0) / cell), 0), nx - 1)
        j = min(max(int((y - y0) / cell), 0), ny - 1)
        if not wall_grid[i, j] > r:
            near[k] = True
            m += 1
    if m == 0:
        return pts
    p0 = np.empty((m * P, 2))
    p1 = np.empty((m * P, 2))
    q = 0
    for k in range(K):
        if near[k]:
            for p in range(P):
                p0[q, 0], p0[q, 1] = pos[k, 0], pos[k, 1]
                p1[q, 0], p1[q, 1] = pts[k, p, 0], pts[k, p, 1]
                q += 1
    moved, _ = reflect(p0, p1, walls, box)
    q = 0
    for k in range(K):
        if near[k]:
            for p in range(P):
                pts[k, p, 0], pts[k, p, 1] = moved[q, 0], moved[q, 1]
                q += 1
    return pts


@njit(cache=True)
def near_parts(pos, P, zx, zy, r2, unit, reach, wall_grid, x0, y0, cell, walls, box):
    """gauss.Gauss.near_sigma: per component (2 modes, 2 axes) the exponent -0.5 d^2 / v and the factor
    sqrt(r2 / v) of E[exp(-|x - z|^2 / (2 r2))] (v = r2 + P; numpy takes the exponential), and the sigma
    points (sigma_points) of the position weighted by that kernel. The same arithmetic as the numpy
    it replaces: results equal bit for bit."""
    K = pos.shape[0]
    expo = np.empty((K, 2))
    sq = np.empty((K, 2))
    mean = np.empty((K, 2))
    var = np.empty((K, 2))
    for k in range(K):
        for a in range(2):
            z = zx if a == 0 else zy
            p = P[k, a]
            v = r2 + p
            d = pos[k, a] - z
            sq[k, a] = math.sqrt(r2 / v)
            expo[k, a] = -0.5 * (d * d) / v
            mean[k, a] = (pos[k, a] * r2 + z * p) / (p + r2)
            var[k, a] = p * r2 / (p + r2)
    return expo, sq, sigma_points(pos, mean, var, unit, reach, wall_grid, x0, y0, cell, walls, box)


@njit(cache=True)
def near_weight(sq, e):
    """near_parts' factor times numpy's exponential, the product over the axes (2 modes,)."""
    K = sq.shape[0]
    out = np.empty(K)
    for k in range(K):
        out[k] = (sq[k, 0] * e[k, 0]) * (sq[k, 1] * e[k, 1])
    return out


@njit(cache=True)
def grid_at(grid, pts, x0, y0, cell):
    """grid's value in the 0.1 m cell of each point (n, 2), clipped to the raster (world.World.cell_of)."""
    nx, ny = grid.shape
    out = np.empty(len(pts))
    for p in range(len(pts)):
        i = min(max(int((pts[p, 0] - x0) / cell), 0), nx - 1)
        j = min(max(int((pts[p, 1] - y0) / cell), 0), ny - 1)
        out[p] = grid[i, j]
    return out


@njit(cache=True)
def crosses_wall(p0, p1, walls, box):
    """world.World.crosses_wall: per move p0 -> p1 (n, 2), does it cross a wall?"""
    n = len(p0)
    out = np.zeros(n, dtype=np.bool_)
    if n == 0 or len(walls) == 0:
        return out
    lo0, lo1, hi0, hi1 = np.inf, np.inf, -np.inf, -np.inf
    for i in range(n):
        lo0 = min(lo0, p0[i, 0], p1[i, 0])
        hi0 = max(hi0, p0[i, 0], p1[i, 0])
        lo1 = min(lo1, p0[i, 1], p1[i, 1])
        hi1 = max(hi1, p0[i, 1], p1[i, 1])
    for w in range(len(walls)):
        if not (box[w, 0] <= hi0 and box[w, 2] >= lo0 and box[w, 1] <= hi1 and box[w, 3] >= lo1):
            continue
        ax, ay, bx, by = walls[w, 0], walls[w, 1], walls[w, 2], walls[w, 3]
        for i in range(n):
            if out[i]:
                continue
            px, py, qx, qy = p0[i, 0], p0[i, 1], p1[i, 0], p1[i, 1]
            d1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            d2 = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax)
            d3 = (qx - px) * (ay - py) - (qy - py) * (ax - px)
            d4 = (qx - px) * (by - py) - (qy - py) * (bx - px)
            out[i] = d1 * d2 < 0 and d3 * d4 < 0
    return out


@njit(cache=True)
def _pd(r, r50, p_fa, s50):
    """Swerling I detection probability at distance r, half at r50 (radar equation, SNR ~ r^-4;
    false alarms p_fa, s50 the SNR at half, MODEL.md 4.1)."""
    r = max(r, 0.3)
    return p_fa ** (1.0 / (1.0 + s50 * (r50 / r) ** 4))


@njit(cache=True)
def sensor_rates(pts, base, sx, sy, grid, x0, y0, cell, acq, p_fa, s50, live, res, find_radius):
    """filter.Tracker._rates: the rates (walkers, still people) (2, n) at which the sensor at
    (sx, sy) starts a track on somebody at pts (n, 2): rho P_D(r) (acq: rho, r50 walking,
    standing) times its sight (grid, from x0, y0 in cells) - or base (2, n) if given (else (2, 0))
    -, less next to its live tracks live (m, 3: held 0/1, x, y): a measured target by the
    resolution res (range, cross), a held one by find_radius."""
    n = len(pts)
    nx, ny = grid.shape
    out = np.empty((2, n))
    have = base.shape[1] == n
    for p in range(n):
        dx, dy = pts[p, 0] - sx, pts[p, 1] - sy
        r = math.hypot(dx, dy)
        if have:
            bw, bs = base[0, p], base[1, p]
        else:
            i = min(max(int((pts[p, 0] - x0) / cell), 0), nx - 1)
            j = min(max(int((pts[p, 1] - y0) / cell), 0), ny - 1)
            g = grid[i, j]
            bw = acq[0] * _pd(r, acq[1], p_fa, s50) * g
            bs = acq[2] * _pd(r, acq[3], p_fa, s50) * g
        mask = 1.0
        if len(live):
            th = math.atan2(dy, dx)
            for k in range(len(live)):
                if live[k, 0] == 0:
                    zx, zy = live[k, 1] - sx, live[k, 2] - sy
                    rz, tz = math.hypot(zx, zy), math.atan2(zy, zx)
                    dr = r - rz
                    a = th - tz  # in (-2 pi, 2 pi): to (-pi, pi]
                    if a > math.pi:
                        a -= 2 * math.pi
                    elif a <= -math.pi:
                        a += 2 * math.pi
                    dc = 0.5 * (r + rz) * a
                    mask *= 1 - math.exp(-math.log(2.0) * ((dr / res[0]) ** 2 + (dc / res[1]) ** 2))
                else:
                    ex, ey = pts[p, 0] - live[k, 1], pts[p, 1] - live[k, 2]
                    mask *= 1 - math.exp(-0.5 * (ex * ex + ey * ey) / find_radius ** 2)
        out[0, p] = bw * mask
        out[1, p] = bs * mask
    return out


@njit(cache=True)
def _rate_at(px, py, kind, sx, sy, grid, x0, y0, cell, acq, p_fa, s50, live, lo, hi, res, find_radius):
    """sensor_rates for one point and one kind (0 walkers, 1 still people), its live tracks
    live[lo:hi]: the same arithmetic in the same order."""
    nx, ny = grid.shape
    dx, dy = px - sx, py - sy
    r = math.hypot(dx, dy)
    i = min(max(int((px - x0) / cell), 0), nx - 1)
    j = min(max(int((py - y0) / cell), 0), ny - 1)
    g = grid[i, j]
    b = acq[2 * kind] * _pd(r, acq[2 * kind + 1], p_fa, s50) * g
    mask = 1.0
    if hi > lo:
        th = math.atan2(dy, dx)
        for k in range(lo, hi):
            if live[k, 0] == 0:
                zx, zy = live[k, 1] - sx, live[k, 2] - sy
                rz, tz = math.hypot(zx, zy), math.atan2(zy, zx)
                dr = r - rz
                a = th - tz  # in (-2 pi, 2 pi): to (-pi, pi]
                if a > math.pi:
                    a -= 2 * math.pi
                elif a <= -math.pi:
                    a += 2 * math.pi
                dc = 0.5 * (r + rz) * a
                mask *= 1 - math.exp(-math.log(2.0) * ((dr / res[0]) ** 2 + (dc / res[1]) ** 2))
            else:
                ex, ey = px - live[k, 1], py - live[k, 2]
                mask *= 1 - math.exp(-0.5 * (ex * ex + ey * ey) / find_radius ** 2)
    return b * mask


@njit(cache=True)
def gauss_rates(pts, use, sxy, grids, live, ptr, x0, y0, cell, acq, p_fa, s50, res, find_radius):
    """filter.Tracker._sync: the rates (2, P) at which the sensors start a track on a person, summed
    over the sensors s with use[s] (the others have a measuring track of them: 0) - the still people's
    at the standing component's sigma points pts[0], the walkers' at the walking one's pts[1] (P, 2);
    sensor s at sxy[s], its sight grids[s], its live tracks live[ptr[s]:ptr[s + 1]] (sensor_rates).
    Summed in the order of the sensors as before: results equal bit for bit."""
    P = pts.shape[1]
    out = np.zeros((2, P))
    for s in range(len(use)):
        if not use[s]:
            continue
        for mode in range(2):
            kind = 1 - mode  # the standing component: the still people's rate
            for p in range(P):
                out[mode, p] += _rate_at(pts[mode, p, 0], pts[mode, p, 1], kind, sxy[s, 0], sxy[s, 1], grids[s], x0, y0,
                                         cell, acq, p_fa, s50, live, ptr[s], ptr[s + 1], res, find_radius)
    return out


@njit(cache=True)
def _chol2(c00, c10, c11):
    l00 = math.sqrt(max(c00, 1e-24))
    l10 = c10 / l00
    return l00, l10, math.sqrt(max(c11 - l10 ** 2, 0.0))


@njit(cache=True)
def wall_moves(m0, P0, F, Q, mean, cov, walls, box):
    """gauss.Gauss.walls for the walking component: m0, P0 (2 axes, d) before the step F, Q
    (d, d), mean, cov after it. The 16 cubature points of position, velocity and noise per axis,
    moved, reflected at the walls (velocity too), matched again; the offsets follow by
    regression. Returns (whether a point met a wall, mean, cov)."""
    A, d = mean.shape
    s = math.sqrt(8.0)
    pre = np.empty((16, A, 2))
    q = np.zeros((16, A, 2))
    for p in range(16):
        for a in range(A):
            pre[p, a, 0], pre[p, a, 1] = m0[a, 0], m0[a, 1]
    l00, l10, l11 = _chol2(Q[0, 0], Q[1, 0], Q[1, 1])
    i = 0
    for a in range(A):
        c00, c10, c11 = _chol2(P0[a, 0, 0], P0[a, 1, 0], P0[a, 1, 1])
        for j in range(2):
            for sign in (s, -s):
                if j == 0:
                    pre[i, a, 0] += sign * c00
                    pre[i, a, 1] += sign * c10
                    q[i + 4, a, 0] += sign * l00
                    q[i + 4, a, 1] += sign * l10
                else:
                    pre[i, a, 1] += sign * c11
                    q[i + 4, a, 1] += sign * l11
                i += 1
        i += 4
    post = np.empty((16, A, 2))
    for p in range(16):
        for a in range(A):
            for r in range(2):
                post[p, a, r] = F[r, 0] * pre[p, a, 0] + F[r, 1] * pre[p, a, 1] + q[p, a, r]
    p1, normal = reflect(pre[:, :, 0].copy(), post[:, :, 0].copy(), walls, box)
    if not normal.any():
        return False, mean, cov
    for p in range(16):
        vn = 0.0
        for a in range(A):
            post[p, a, 0] = p1[p, a]
            vn += post[p, a, 1] * normal[p, a]
        for a in range(A):
            post[p, a, 1] -= 2 * vn * normal[p, a]
    mean = mean.copy()
    cov = cov.copy()
    for a in range(A):
        mu = np.zeros(2)
        for p in range(16):
            mu += post[p, a]
        mu /= 16
        Cn = np.zeros((2, 2))
        for p in range(16):
            e0, e1 = post[p, a, 0] - mu[0], post[p, a, 1] - mu[1]
            Cn[0, 0] += e0 * e0
            Cn[0, 1] += e0 * e1
            Cn[1, 0] += e1 * e0
            Cn[1, 1] += e1 * e1
        Cn /= 16
        P = cov[a].copy()
        C = P[:2, :2].copy()
        # G = (C^-1 P[core, :])^T, (d, 2)
        a00, a01, a10, a11 = C[0, 0] + 1e-12, C[0, 1], C[1, 0], C[1, 1] + 1e-12
        det = a00 * a11 - a01 * a10
        G = np.empty((d, 2))
        for k in range(d):
            G[k, 0] = (a11 * P[0, k] - a01 * P[1, k]) / det
            G[k, 1] = (a00 * P[1, k] - a10 * P[0, k]) / det
        dm0, dm1 = mu[0] - mean[a, 0], mu[1] - mean[a, 1]
        for k in range(d):
            mean[a, k] += G[k, 0] * dm0 + G[k, 1] * dm1
        D = Cn - C
        for k in range(d):
            for l in range(d):
                cov[a, k, l] = P[k, l] + (G[k, 0] * (D[0, 0] * G[l, 0] + D[0, 1] * G[l, 1])
                                          + G[k, 1] * (D[1, 0] * G[l, 0] + D[1, 1] * G[l, 1]))
    return True, mean, cov


@njit(cache=True)
def hidden_stays(walk, still, keep, stop_p, up, q, kappa_w, go_w, measured):
    """hidden.Hidden.move, in place: walkers stop with probability stop_p, still people get up
    with up (L,), the detectability is drawn anew with q, who stops begins a fresh stay
    (still (L, K, n): kind of stay, detectability, tile). keep (K, n) is multiplied by the share of
    the still mass after the step that is no fresh draw (it keeps the amplitude of its stay; the
    rest draws it from the prior given the detectability, hidden.Hidden.amp); only if measured."""
    L, K, n = still.shape
    s = np.empty(L)
    for c in range(n):
        stop = walk[c] * stop_p
        rise = 0.0
        fresh_all = 0.0
        for l in range(L):
            t = 0.0
            for k in range(K):
                t += still[l, k, c]
            s[l] = t
        for l in range(L):
            rise += up[l] * s[l]
            fresh_all += q * (s[l] - up[l] * s[l]) + go_w[l] * stop
        walk[c] = walk[c] - stop + rise
        if measured:
            for k in range(K):
                kept = 0.0
                for l in range(L):
                    kept += still[l, k, c] * (1 - up[l]) * (1 - q)
                tot = kept + kappa_w[k] * fresh_all
                if tot > 0:
                    keep[k, c] *= kept / tot
        for l in range(L):
            f = (1 - up[l]) * (1 - q)
            fresh = q * (s[l] - up[l] * s[l]) + go_w[l] * stop
            for k in range(K):
                still[l, k, c] = still[l, k, c] * f + kappa_w[k] * fresh


@njit(cache=True)
def hidden_regions(region, walk, out, ends_p, door_ptr, door_tiles, exits, entries, arrive_p, older_p):
    """hidden.Hidden._regions, in place on region (R, A) and walk; returns out. ends_p (R, A):
    probability that a stay ends in the step; who comes out leaves through one of the region's doors
    alike: walking into view at a door into the observed area (door_tiles[door_ptr[r]:door_ptr[r + 1]])
    or out of the house through one of its exits[r] ways out (none at all: the stay goes on). Coming
    home with arrive_p, by every way in alike: the regions' ways out and the entries; the stays age
    with older_p (A - 1,)."""
    R, A = region.shape
    for r in range(R):
        o = 0.0
        for a in range(A):
            e = region[r, a] * ends_p[r, a]
            region[r, a] -= e
            o += e
        nd = door_ptr[r + 1] - door_ptr[r]
        ways = nd + exits[r]
        if ways == 0:
            region[r, 0] += o
        else:
            for i in range(door_ptr[r], door_ptr[r + 1]):
                walk[door_tiles[i]] += o / ways
            out += o * exits[r] / ways
    ways = len(entries)
    for r in range(R):
        ways += exits[r]
    if ways and out > 0:
        come = out * arrive_p
        out -= come
        for r in range(R):
            region[r, 0] += come * exits[r] / ways
        for c in entries:
            walk[c] += come / ways
    for r in range(R):
        prev = 0.0
        for a in range(A):
            older = region[r, a] * older_p[a] if a < A - 1 else 0.0
            region[r, a] = region[r, a] - older + prev
            prev = older
    return out


@njit(cache=True)
def gauss_exponents(points, mx, my, vx, vy):
    """tiling.Tiling.gauss_points' exponents -0.5 ((x - mx)^2 / vx + (y - my)^2 / vy) at the points
    (P, 2), without numpy's temporary arrays. The caller takes np.exp: numpy's vectorized exp is
    faster than the scalar one and gives the same bits as before (the scalar one differs in the last
    bit for some 5 % of the values here)."""
    P = points.shape[0]
    out = np.empty(P)
    for i in range(P):
        dx = points[i, 0] - mx
        dy = points[i, 1] - my
        out[i] = -0.5 * (dx * dx / vx + dy * dy / vy)
    return out


@njit(cache=True)
def _pairwise_leaf(a, lo, n):
    """numpy's pairwise_sum for n <= 128 elements a[lo:lo + n]: 8 accumulators (fewer than 8: in a row)."""
    if n < 8:
        res = 0.0
        for i in range(n):
            res += a[lo + i]
        return res
    r0, r1, r2, r3 = a[lo], a[lo + 1], a[lo + 2], a[lo + 3]
    r4, r5, r6, r7 = a[lo + 4], a[lo + 5], a[lo + 6], a[lo + 7]
    m = n - n % 8
    for i in range(lo + 8, lo + m, 8):
        r0 += a[i]
        r1 += a[i + 1]
        r2 += a[i + 2]
        r3 += a[i + 3]
        r4 += a[i + 4]
        r5 += a[i + 5]
        r6 += a[i + 6]
        r7 += a[i + 7]
    res = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7))
    for i in range(lo + m, lo + n):
        res += a[i]
    return res


@njit(cache=True)
def _pairwise(a):
    """numpy's sum of a contiguous 1-D array: its pairwise summation (numpy/_core/src/umath/
    loops_utils.h.src) - blocks of up to 128 with 8 accumulators, above that the halves (at multiples
    of 8) summed apart and added -, so the same sum bit for bit as a.sum(). The halving as a loop
    with its own stack (Numba's cache does not hold recursive functions)."""
    n = a.size
    if n <= 128:
        return _pairwise_leaf(a, 0, n)
    los = np.empty(64, dtype=np.int64)
    ns = np.empty(64, dtype=np.int64)
    stage = np.zeros(64, dtype=np.int64)
    left = np.empty(64)
    sp = 0
    los[0], ns[0] = 0, n
    ret = 0.0
    while True:
        lo, m = los[sp], ns[sp]
        half = m // 2
        half -= half % 8
        if stage[sp] == 0:
            if m <= 128:
                ret = _pairwise_leaf(a, lo, m)
                sp -= 1
                if sp < 0:
                    return ret
                continue
            stage[sp] = 1
            sp += 1
            los[sp], ns[sp], stage[sp] = lo, half, 0
        elif stage[sp] == 1:
            left[sp] = ret
            stage[sp] = 2
            sp += 1
            los[sp], ns[sp], stage[sp] = lo + half, m - half, 0
        else:
            ret = left[sp] + ret
            sp -= 1
            if sp < 0:
                return ret


@njit(cache=True)
def _npsum(a):
    """a.sum() of a contiguous array, bit for bit as numpy (_pairwise)."""
    return _pairwise(a.reshape(a.size))


@njit(cache=True)
def _abs_diff_sum(a, x, b, y):
    """numpy's np.abs(a * x - b * y).sum() of two contiguous arrays of one shape."""
    fx = x.reshape(x.size)
    fy = y.reshape(y.size)
    d = np.empty(fx.size)
    for i in range(fx.size):
        d[i] = abs(a * fx[i] - b * fy[i])
    return _pairwise(d)


@njit(cache=True)
def hidden_distance(a, walk, still, region, out, b, walk2, still2, region2, out2):
    """hidden.Hidden.distance: the L1 distance between a x one density and b x another, summed in the
    same order as the numpy it replaces (results equal bit for bit)."""
    return (_abs_diff_sum(a, walk, b, walk2) + _abs_diff_sum(a, still, b, still2) + _abs_diff_sum(a, region, b, region2)
            + abs(a * out - b * out2))


@njit(cache=True)
def hidden_weigh(walk, still, region, out, f_walk, f_still, intensity):
    """hidden.Hidden.weigh / Undetected.weigh in place: _hidden_weigh."""
    return _hidden_weigh(walk, still, region, out, f_walk, f_still, intensity)


@njit(cache=True)
def _hidden_weigh(walk, still, region, out, f_walk, f_still, intensity):
    """hidden.Hidden.weigh / Undetected.weigh in place: walk *= f_walk (n,), still (L, K, n) *= f_still
    (K, n) or (1, n) for every kind of stay. A person (intensity False): normalized, returns (log of
    the mass that was left, out scaled); -inf and nothing scaled if none was left. The unknown ones:
    returns (mass after - mass before in view, out). The same arithmetic and sums as the numpy it
    replaces (_npsum): results equal bit for bit."""
    L, K, n = still.shape
    before = 0.0
    if intensity:
        before = _npsum(walk) + _npsum(still)
    for c in range(n):
        walk[c] *= f_walk[c]
    one = f_still.shape[0] == 1
    for l in range(L):
        for k in range(K):
            kk = 0 if one else k
            for c in range(n):
                still[l, k, c] *= f_still[kk, c]
    if intensity:
        return -(before - (_npsum(walk) + _npsum(still))), out
    tot = _npsum(walk) + _npsum(still) + _npsum(region) + out
    if tot <= 0:
        return -np.inf, out
    s = 1.0 / tot
    for c in range(n):
        walk[c] *= s
    for l in range(L):
        for k in range(K):
            for c in range(n):
                still[l, k, c] *= s
    R, A = region.shape
    for r in range(R):
        for a in range(A):
            region[r, a] *= s
    return math.log(tot), out * s


@njit(cache=True)
def hidden_weigh_at(walk, still, region, out, idx, f_walk, f_still, intensity):
    """hidden_weigh with the factors given only at the tiles idx (f_walk (m,), f_still (K, m) or (1, m)),
    1 elsewhere: the same numbers (x times 1 is x), without the arrays of ones."""
    L, K, n = still.shape
    before = 0.0
    if intensity:
        before = _npsum(walk) + _npsum(still)
    one = f_still.shape[0] == 1
    for j in range(len(idx)):
        c = idx[j]
        walk[c] *= f_walk[j]
        for l in range(L):
            for k in range(K):
                still[l, k, c] *= f_still[0 if one else k, j]
    if intensity:
        return -(before - (_npsum(walk) + _npsum(still))), out
    tot = _npsum(walk) + _npsum(still) + _npsum(region) + out
    if tot <= 0:
        return -np.inf, out
    s = 1.0 / tot
    for c in range(n):
        walk[c] *= s
    for l in range(L):
        for k in range(K):
            for c in range(n):
                still[l, k, c] *= s
    R, A = region.shape
    for r in range(R):
        for a in range(A):
            region[r, a] *= s
    return math.log(tot), out * s


@njit(cache=True)
def sync_weigh(r, kappa, dt, unit_w, ka, logw, a, d_away):
    """filter.Tracker._sync's weighing of a Gaussian by no track started in dt: E[exp(-rate dt)] over
    each component's sigma points (r (2, P) [STILL, WALK], per level of detectability kappa for the
    standing one), then Gauss.kappa_weigh and Gauss.reweigh in one call. Returns (log factor of the
    whole person, ka, logw, a)."""
    K, P = len(kappa), r.shape[1]
    f_walk = 0.0
    for p in range(P):
        f_walk += unit_w[p] * math.exp(-r[1, p] * dt)
    ka2 = np.empty_like(ka)
    s = 0.0
    for k in range(K):
        f = 0.0
        for p in range(P):
            f += unit_w[p] * math.exp(-kappa[k] * r[0, p] * dt)
        for j in range(ka.shape[1]):
            ka2[k, j] = ka[k, j] * f
            s += ka2[k, j]
    if s > 0:
        ka = ka2 / s
    l0 = logw[0] + (math.log(s) if s > 0 else -np.inf)
    l1 = logw[1] + (math.log(f_walk) if f_walk > 0 else -np.inf)
    top = max(l0, l1)
    if top > -np.inf:
        comps = top + math.log(math.exp(l0 - top) + math.exp(l1 - top))
    else:
        comps = -np.inf
    lw = np.array([l0, l1])
    if a <= 0:
        if comps == -np.inf:
            return -np.inf, ka, logw, a
        return comps, ka, lw - comps, a
    p0 = math.log1p(-a) + comps if a < 1 else -np.inf
    p1 = math.log(a) + d_away
    hi = max(p0, p1)
    if hi == -np.inf:
        return -np.inf, ka, logw, a
    total = hi + math.log(math.exp(p0 - hi) + math.exp(p1 - hi))
    a = math.exp(p1 - total)
    if comps > -np.inf:
        logw = lw - comps
    return total, ka, logw, a


@njit(cache=True)
def _weights2(l0, l1):
    """gauss.Gauss.weights from the log weights (l0, l1)."""
    top = l0 if l0 >= l1 or l0 != l0 else l1
    if top == -np.inf:
        return 0.5, 0.5
    w0, w1 = math.exp(l0 - top), math.exp(l1 - top)
    s = w0 + w1
    return w0 / s, w1 / s


@njit(cache=True)
def _bernoulli(r, q):
    """hidden.Hidden._bernoulli: (the factor for the hypotheses, r after) from the log q of the mass left."""
    if r >= 1.0:
        return q, r
    if q == -np.inf:
        return math.log1p(-r), 0.0
    if q > 0:
        z = r + (1.0 - r) * math.exp(-q)
        return q + math.log(z), r / z
    z = 1.0 - r + r * math.exp(q)
    return math.log(z), r * math.exp(q) / z


@njit(cache=True)
def sync_step(mean, cov, logw, gow, ka, a, prm, slot_k, slot_var, go, go_w, ka_w, flags,
              walls, box, wall_grid, wx0, wy0, cell, doors_c, doors_v, doors_a, doors_r,
              aw_walk, aw_still, aw_region, aw_out, aw_r, f_walk, f_still,
              unit, unit_w, reach, kappa, use, sxy, grids, live, ptr, acq, p_fa, s50, res, find_radius):
    """filter.Tracker._sync for one part dt of one Gaussian in one call: Gauss.predict (gauss_predict,
    the weights' logarithm, Gauss.walls, Gauss._through_doors without moving the part gone through a
    door - the caller moves it first, Hidden.move_all), then the weighing by no track started
    (sigma_points, gauss_rates, the part gone through a door by f_walk, f_still: Hidden.weigh,
    sync_weigh). prm: gauss.step_params. flags: 1 walls and doors (tiles, not a ghost's source), 2 it
    has a part gone through a door (aw_*), 4 that part is dropped next (a measurement follows: only
    a changes), 8 weigh, 16 weigh the part gone through a door (f_walk, f_still). Without a part, what goes through a door is put into aw_region,
    aw_out (zeros: the caller's new Hidden). Returns (log factor for the hypotheses, mean, cov,
    logw, gow, ka, a, aw_out, aw_r, whether aw_* became the part)."""
    dt, p_stop, s2, still_q, tau, ao, q, cs = prm[0], prm[1], prm[2], prm[3], prm[4], prm[5], prm[6], prm[7]
    w = np.empty(2)
    w[0], w[1] = _weights2(logw[0], logw[1])
    e_go = np.exp(-go * dt)
    slot_q = np.empty(len(slot_var))
    for i in range(len(slot_var)):
        slot_q[i] = slot_var[i] * (1 - cs) * (1 - ao * ao)
    tot_s, tot_w, m0, P0, mean, cov, F, Q, gow, ka = gauss_predict(w, mean, cov, gow, ka, e_go, go_w, ka_w, p_stop, s2,
                                                                   dt, still_q, tau, ao, slot_k, slot_q, q)
    l0 = math.log(tot_s) if tot_s > 0 else -np.inf
    l1 = math.log(tot_w) if tot_w > 0 else -np.inf
    top = max(l0, l1)
    tot = top + math.log(math.exp(l0 - top) + math.exp(l1 - top))
    l0, l1 = l0 - tot, l1 - tot
    has_away, keep_away = (flags & 2) != 0, (flags & 4) == 0
    made = False
    if flags & 1:
        # Gauss.walls
        x0, y0, x1, y1 = m0[1, 0, 0], m0[1, 1, 0], mean[1, 0, 0], mean[1, 1, 0]
        vx, vv = max(P0[1, 0, 0, 0], P0[1, 1, 0, 0]), max(P0[1, 0, 1, 1], P0[1, 1, 1, 1])
        s = math.sqrt(8.0)
        rch = math.hypot(x1 - x0, y1 - y0) + s * (math.sqrt(max(vx, 0.0)) + abs(F[1, 0, 1]) * math.sqrt(max(vv, 0.0))
                                                  + math.sqrt(max(Q[1, 0, 0], 0.0)))
        nx, ny = wall_grid.shape
        i = min(max(int((x0 - wx0) / cell), 0), nx - 1)
        j = min(max(int((y0 - wy0) / cell), 0), ny - 1)
        if not wall_grid[i, j] > rch:
            hit, mw, cw = wall_moves(m0[1], P0[1], F[1], Q[1], mean[1], cov[1], walls, box)
            if hit:
                mean[1] = mw
                cov[1] = cw
        # Gauss._through_doors (tiling.Tiling.near_doors)
        w0, w1 = _weights2(l0, l1)
        if w1 >= 1e-9 and len(doors_c):
            R1 = doors_r.shape[1]
            rate = np.zeros(R1)
            mx, my, vx, vy = mean[1, 0, 0], mean[1, 1, 0], cov[1, 0, 0, 0], cov[1, 1, 0, 0]
            for d in range(len(doors_c)):
                v0, v1 = vx + doors_v[d, 0], vy + doors_v[d, 1]
                e0, e1 = doors_c[d, 0] - mx, doors_c[d, 1] - my
                dens = math.exp(-0.5 * (e0 * e0 / v0 + e1 * e1 / v1)) / (2 * math.pi * math.sqrt(v0 * v1))
                f = min(dens * doors_a[d], 1.0)
                for r in range(R1):
                    rate[r] += f * doors_r[d, r]
            total = 0.0
            for r in range(R1):
                total += rate[r]
            if total > 0:
                frac = -math.expm1(-total * dt)
                moved = (1 - a) * w1 * frac
                if moved > 0:
                    if keep_away:
                        if has_away:
                            k = a / (a + moved)
                            aw_walk *= k
                            aw_still *= k
                            aw_region *= k
                            aw_out *= k
                        else:
                            made = True
                        for r in range(R1 - 1):
                            aw_region[r, 0] += moved / (a + moved) * rate[r] / total
                        aw_out += moved / (a + moved) * rate[R1 - 1] / total
                    l1 += math.log1p(-frac) if frac < 1 else -np.inf
                    top = max(l0, l1)
                    tot = top + math.log(math.exp(l0 - top) + math.exp(l1 - top))
                    l0, l1 = l0 - tot, l1 - tot
                    a += moved
    lw = np.empty(2)
    lw[0], lw[1] = l0, l1
    if not flags & 8:
        return 0.0, mean, cov, lw, gow, ka, a, aw_out, aw_r, made
    var = np.empty((2, 2))
    pos = np.empty((2, 2))
    for k in range(2):
        for ax in range(2):
            pos[k, ax] = mean[k, ax, 0]
            var[k, ax] = cov[k, ax, 0, 0]
    pts = sigma_points(pos, pos, var, unit, reach, wall_grid, wx0, wy0, cell, walls, box)
    r = gauss_rates(pts, use, sxy, grids, live, ptr, wx0, wy0, cell, acq, p_fa, s50, res, find_radius)
    da = 0.0
    if (has_away or made) and flags & 16:
        qd, aw_out = _hidden_weigh(aw_walk, aw_still, aw_region, aw_out, f_walk, f_still, False)
        da, aw_r = _bernoulli(aw_r, qd)
    d, ka, lw, a = sync_weigh(r, kappa, dt, unit_w, ka, lw, a, da)
    return d, mean, cov, lw, gow, ka, a, aw_out, aw_r, made


def warm():
    """Compile every kernel for the types the tracker calls it with (the image does this when it is
    built, so the app starts without compiling: NUMBA_CACHE_DIR)."""
    f, i = np.zeros, lambda *s: np.zeros(s, dtype=np.int64)
    walls = np.array([[0.0, 0.0, 1.0, 0.0]])
    F, Q = motion(4, 0.2, 1e-4, 1.2, 0.5, 0.9, i(1), f(1))
    w = np.array([0.5, 0.5])
    mean, cov = f((2, 2, 4)), np.broadcast_to(np.eye(4), (2, 2, 4, 4)).copy()
    imm_predict(w, mean, cov, 0.9, 0.1, 0.5, F, Q)
    gauss_predict(w, mean, cov, f(3), f((3, 4)), f(3), f(3), f((3, 4)), 0.1, 0.5, 0.2, 1e-4, 1.2, 0.9, i(1), f(1), 0.1)
    wall_moves(mean[1], cov[1], F[1], Q[1], mean[1], cov[1], walls, walls)
    reflect(f((1, 2)), np.ones((1, 2)), walls, walls)
    crosses_wall(f((1, 2)), np.ones((1, 2)), walls, walls)
    sensor_rates(f((1, 2)), f((2, 0)), 0.0, 0.0, f((2, 2)), 0.0, 0.0, 0.1, f(4), 1e-6, 1.0, f((1, 3)), np.ones(2), 0.7)
    hidden_stays(f(3), f((2, 2, 3)), f((2, 3)), 0.1, f(2), 0.1, f(2), f(2), True)
    hidden_regions(f((1, 3)), f(3), 0.0, f((1, 3)), i(2), i(0), i(1), i(0), 0.1, f(2))
    gauss_exponents(f((1, 2)), 0.0, 0.0, 1.0, 1.0)
    hidden_weigh(f(3), f((2, 2, 3)), f((1, 3)), 0.0, f(3), f((2, 3)), False)
    hidden_weigh_at(f(3), f((2, 2, 3)), f((1, 3)), 0.0, i(1), f(1), f((2, 1)), False)
    hidden_distance(1.0, f(3), f((2, 2, 3)), f((1, 3)), 0.0, 1.0, f(3), f((2, 2, 3)), f((1, 3)), 0.0)
    sigma_points(f((2, 2)), f((2, 2)), f((2, 2)), f((5, 2)), 1.7, f((2, 2)), 0.0, 0.0, 0.1, walls, walls)
    grid_at(f((2, 2)), f((1, 2)), 0.0, 0.0, 0.1)
    near_weight(*near_parts(f((2, 2)), np.ones((2, 2)), 0.0, 0.0, 0.5, f((5, 2)), 1.7, f((2, 2)), 0.0, 0.0, 0.1, walls,
                            walls)[:2])
    sync_weigh(f((2, 5)), f(3), 0.2, f(5), f((3, 4)), f(2), 0.0, 0.0)
    sync_step(mean, cov, w, f(3), f((3, 4)), 0.0, np.array([0.2, 0.1, 0.5, 1e-4, 1.2, 0.9, 0.1, 0.5]), i(1), f(1), f(3),
              f(3), f((3, 4)), 5, walls, walls, f((2, 2)), 0.0, 0.0, 0.1, f((1, 2)), np.ones((1, 2)), f(1), f((1, 2)), f(3),
              f((2, 2, 3)), f((1, 3)), 0.0, 1.0, np.ones(3), np.ones((2, 3)), f((5, 2)), f(5), 1.7, f(3),
              np.ones(1, dtype=np.bool_), f((1, 2)), f((1, 2, 2)), f((1, 3)), i(2), f(4), 1e-6, 1.0, np.ones(2), 0.7)
    gauss_rates(f((2, 5, 2)), np.ones(1, dtype=np.bool_), f((1, 2)), f((1, 2, 2)), f((1, 3)), i(2), 0.0, 0.0, 0.1, f(4),
                1e-6, 1.0, np.ones(2), 0.7)


if __name__ == "__main__":
    warm()
