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
def poisson_binomial(ps, means, lgam):
    """filter.Tracker._poisson_binomial (lgam[i] = lgamma(i + 1), long enough)."""
    P, C = ps.shape
    dist = np.zeros((C, P + 1))
    dist[:, 0] = 1.0
    for p in range(P):
        for c in range(C):
            q = ps[p, c]
            prev = 0.0
            for i in range(p + 2):
                cur = dist[c, i]
                dist[c, i] = cur * (1 - q) + prev * q
                prev = cur
    width = 0
    for c in range(C):
        if means[c] > 0:
            width = max(width, int(means[c] + 10 * math.sqrt(means[c]) + 6))
    if width == 0:
        return dist
    out = np.zeros((C, P + width))
    for c in range(C):
        mean = means[c]
        if mean <= 0:
            out[c, :P + 1] = dist[c]
            continue
        cut = int(mean + 10 * math.sqrt(mean) + 6)
        lm = math.log(mean)
        pois = np.empty(cut)
        for k in range(cut):
            pois[k] = math.exp(k * lm - mean - lgam[k])
        for i in range(P + 1):
            for k in range(cut):
                out[c, i + k] += dist[c, i] * pois[k]
    return out


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
def hidden_stays(walk, still, stop_p, up, q, kappa_w, go_w):
    """hidden.Hidden.move, in place: walkers stop with probability stop_p, still people get up
    with up (L,), the detectability is drawn anew with q, who stops begins a fresh stay
    (still (L, K, n): kind of stay, detectability, tile)."""
    L, K, n = still.shape
    s = np.empty(L)
    for c in range(n):
        stop = walk[c] * stop_p
        rise = 0.0
        for l in range(L):
            t = 0.0
            for k in range(K):
                t += still[l, k, c]
            s[l] = t
        for l in range(L):
            rise += up[l] * s[l]
        walk[c] = walk[c] - stop + rise
        for l in range(L):
            f = (1 - up[l]) * (1 - q)
            fresh = q * (s[l] - up[l] * s[l]) + go_w[l] * stop
            for k in range(K):
                still[l, k, c] = still[l, k, c] * f + kappa_w[k] * fresh


@njit(cache=True)
def hidden_regions(region, walk, out, ends_p, door_ptr, door_tiles, open_, entries, leave_p, arrive_p, older_p):
    """hidden.Hidden._regions, in place on region (R, A) and walk; returns out. ends_p (R, A):
    probability that a stay ends in the step; who comes out appears walking at the region's doors
    (door_tiles[door_ptr[r]:door_ptr[r + 1]]; none: the stay goes on); leaving the house from the
    open regions with leave_p, coming home with arrive_p by the open regions and the entries;
    the stays age with older_p (A - 1,)."""
    R, A = region.shape
    for r in range(R):
        o = 0.0
        for a in range(A):
            e = region[r, a] * ends_p[r, a]
            region[r, a] -= e
            o += e
        nd = door_ptr[r + 1] - door_ptr[r]
        if nd == 0:
            region[r, 0] += o
        else:
            for i in range(door_ptr[r], door_ptr[r + 1]):
                walk[door_tiles[i]] += o / nd
    for r in open_:
        lv = 0.0
        for a in range(A):
            e = region[r, a] * leave_p
            region[r, a] -= e
            lv += e
        out += lv
    ways = len(open_) + len(entries)
    if ways and out > 0:
        come = out * arrive_p
        out -= come
        for r in open_:
            region[r, 0] += come / ways
        for c in entries:
            walk[c] += come / ways
    for r in range(R):
        prev = 0.0
        for a in range(A):
            older = region[r, a] * older_p[a] if a < A - 1 else 0.0
            region[r, a] = region[r, a] - older + prev
            prev = older
    return out


def warm():
    """Compile every kernel for the types the tracker calls it with (the image does this when it is
    built, so the app starts without compiling: NUMBA_CACHE_DIR)."""
    f, i = np.zeros, lambda *s: np.zeros(s, dtype=np.int64)
    walls = np.array([[0.0, 0.0, 1.0, 0.0]])
    F, Q = motion(4, 0.2, 1e-4, 1.2, 0.5, 0.9, i(1), f(1))
    w = np.array([0.5, 0.5])
    mean, cov = f((2, 2, 4)), np.broadcast_to(np.eye(4), (2, 2, 4, 4)).copy()
    imm_predict(w, mean, cov, 0.9, 0.1, 0.5, F, Q)
    wall_moves(mean[1], cov[1], F[1], Q[1], mean[1], cov[1], walls, walls)
    poisson_binomial(f((1, 2)), np.array([0.0, 0.5]), f(64))
    reflect(f((1, 2)), np.ones((1, 2)), walls, walls)
    crosses_wall(f((1, 2)), np.ones((1, 2)), walls, walls)
    sensor_rates(f((1, 2)), f((2, 0)), 0.0, 0.0, f((2, 2)), 0.0, 0.0, 0.1, f(4), 1e-6, 1.0, f((1, 3)), np.ones(2), 0.7)
    hidden_stays(f(3), f((2, 2, 3)), 0.1, f(2), 0.1, f(2), f(2))
    hidden_regions(f((1, 3)), f(3), 0.0, f((1, 3)), i(2), i(0), i(0), i(0), 0.1, 0.1, f(2))


if __name__ == "__main__":
    warm()
