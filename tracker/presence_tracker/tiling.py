"""The observed area as a raster of square tiles (MODEL.md 5.3): where a person without a track can be.

A tile is a SIZE m square of the plan, cut at the borders of the rooms (a square across a wall is
two tiles, one per room). Within a tile a person's position is not told apart: they are anywhere in
it alike. Where a position is needed - a track starting on somebody there, walking into a door - a
tile counts as the Gaussian with its mean and spread (the same first two moments; integrating the
uniform density exactly made no difference, 6.10.).

Walking between tiles is the diffusion limit of the walking model (MODEL.md 3.2): a velocity-jump
process with speed s and turning rate lambda spreads like a diffusion with D = E[s^2] / (2 lambda)
per axis (Saerkkae & Solin 2019, sec. 2.2/4.3; the OU approximation of 3.2 has the same long-time
spread). Discretized by finite volumes: from tile i to a neighbour j at the rate D L_ij / (A_i d_ij)
(L shared border, A area, d distance of the centroids), into a region without a sensor across a door
likewise, with d twice the distance to the door, and so out of the house into an outside room (the
stairwell, MODEL.md 2). Walls are no border: nobody walks through them.

Rates (sensors starting tracks, finding a held one) are averaged over the tile at the points of a
0.2 m raster (the tile's sample points), as the projection of a likelihood onto a coarser state
(G_Liao2003 eq. 2). 6.10.: tiles also cut where each sensor's sight changes explained the data
slightly better at 0.6 m, but 0.4 m squares as well, at the same computing time and with much less
code.
"""

import math

import numpy as np

from .world import CELL, OBSERVED

SIZE = 0.4  # m: side of a tile
POINT = 0.2  # m: sample points for rates
TICK = 0.1  # s: walking moves the tiles' masses in steps of this length
class Tiling:
    def __init__(self, tracker):
        self.tr = tr = tracker
        w = tr.world
        self.world = w
        m = tr.m
        self.sh = tr.shapes
        lab = w.labels
        nx, ny = lab.shape
        ii, jj = np.nonzero(lab == OBSERVED)
        centers = np.stack([w.x0 + (ii + 0.5) * CELL, w.y0 + (jj + 0.5) * CELL], axis=1)
        # the tile of each 0.1 m cell in view: its square and its room
        b = max(int(round(SIZE / CELL)), 1)
        bi, bj = (ii - ii.min()) // b if len(ii) else ii, (jj - jj.min()) // b if len(jj) else jj
        room = tr.room_of[ii, jj]
        keys, tile = np.unique(np.stack([bi, bj, room], axis=1), axis=0, return_inverse=True) if len(ii) else (np.zeros((0, 3), int), np.zeros(0, int))
        tile = tile.ravel()
        self.n = n = len(keys)
        self.fine, self.fine_xy, self.fine_ij = tile, centers, (ii, jj)
        cnt = np.bincount(tile, minlength=n).astype(float)
        self.area = cnt * CELL * CELL
        self.centers = np.stack([np.bincount(tile, centers[:, k], n) / cnt for k in range(2)], axis=1) if n else np.zeros((0, 2))
        d = centers - self.centers[tile] if n else np.zeros((0, 2))
        self.var = np.stack([np.bincount(tile, d[:, k] ** 2, n) / cnt for k in range(2)], axis=1) + CELL * CELL / 12 if n else np.zeros((0, 2))
        self.room = keys[:, 2] if n else np.zeros(0, int)
        # the square of each tile (lower left corner), for the display; the room cuts it
        x00, y00 = (w.x0 + ii.min() * CELL, w.y0 + jj.min() * CELL) if n else (0.0, 0.0)
        self.squares = np.stack([x00 + keys[:, 0] * b * CELL, y00 + keys[:, 1] * b * CELL], axis=1) if n else np.zeros((0, 2))
        self.square = b * CELL
        # sample points: the 0.1 m cells on a POINT raster, and each tile's most central one if it has none
        p = max(int(round(POINT / CELL)), 1)
        on = (ii % p == p // 2) & (jj % p == p // 2)
        missing = np.setdiff1d(np.arange(n), tile[on])
        extra = [int(np.argmin(((centers - self.centers[t]) ** 2).sum(axis=1) + 1e9 * (tile != t))) for t in missing]
        pick = np.concatenate([np.flatnonzero(on), np.array(extra, dtype=int)])
        self.points = centers[pick]
        self.point_tile = tile[pick]
        self.point_count = np.bincount(self.point_tile, minlength=n).astype(float)
        # neighbouring 0.1 m cells not across a wall: the borders between tiles
        grid = np.full((nx, ny), -1)
        grid[ii, jj] = np.arange(len(ii))
        pairs = []
        for di, dj in ((1, 0), (0, 1)):
            a, c = grid[: nx - di, : ny - dj], grid[di:, dj:]
            ok = (a >= 0) & (c >= 0)
            pairs.append(np.stack([a[ok], c[ok]], axis=1))
        pairs = np.concatenate(pairs) if pairs else np.zeros((0, 2), int)
        if len(pairs):
            pairs = pairs[~w.crosses_wall(centers[pairs[:, 0]], centers[pairs[:, 1]])]
        self.R = R = len(w.places) - 2
        D = (m.speed ** 2 + m.speed_spread ** 2) / (2 * m.turn_rate)
        Q = np.zeros((n + R + 1, n + R + 1))  # generator, Q[to, from]: tiles, regions, outside
        tp = tile[pairs]
        cross = tp[:, 0] != tp[:, 1]
        L = np.zeros((n, n))
        np.add.at(L, (tp[cross, 0], tp[cross, 1]), CELL)
        L = L + L.T
        i_, j_ = np.nonzero(L)
        dist = np.hypot(*(self.centers[i_] - self.centers[j_]).T)
        Q[j_, i_] = D * L[i_, j_] / (self.area[i_] * np.maximum(dist, CELL))
        # doors into the regions without a sensor and to the outside: a cell in view next to one of
        # a region or of an outside room
        self.door_rate = np.zeros((n, R + 1))  # rate (1/s) from a tile into each region, and out
        reg = lab > OBSERVED
        border = {}
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ti, tj = ii + di, jj + dj
            ok = (ti >= 0) & (ti < nx) & (tj >= 0) & (tj < ny)
            k = np.flatnonzero(ok)
            k = k[reg[ti[k], tj[k]]]
            if not len(k):
                continue
            other = np.stack([w.x0 + (ti[k] + 0.5) * CELL, w.y0 + (tj[k] + 0.5) * CELL], axis=1)
            k = k[~w.crosses_wall(centers[k], other)]
            for c in k:
                mid = centers[c] + 0.5 * CELL * np.array([di, dj])
                border.setdefault((int(tile[c]), int(lab[ti[c], tj[c]]) - 1), []).append(mid)
        for (t, r), mids in border.items():
            dd = 2 * max(float(np.hypot(*(np.mean(mids, axis=0) - self.centers[t]))), CELL)
            self.door_rate[t, r] = D * len(mids) * CELL / (self.area[t] * dd)
            Q[n + r, t] += self.door_rate[t, r]
        Q[np.arange(n), np.arange(n)] = -Q[:, :n].sum(axis=0)
        self.D = D
        self.door_tiles = np.flatnonzero(self.door_rate.sum(axis=1) > 0)
        # one tick of walking: tiles -> tiles, tiles -> regions (the stay there begins) and out
        T = expm(Q * TICK)[:, :n] if n else np.zeros((n + R + 1, 0))
        T[T < 1e-12] = 0.0
        self.T_walk = np.ascontiguousarray(T[:n])
        self.T_in = np.ascontiguousarray(T[n:])
        self.tick = TICK
        self.Q = Q
        self._walk_end = None
        # where somebody coming out of a region appears; the ways in from outside straight into view
        self.doors = {r: [] for r in range(self.R)}
        for place, watch, _ in w.portals:
            if place != w.outside:
                c = self.cell_near(watch)
                if c >= 0:
                    self.doors[place - 1].append(c)
        self.entries = [c for c in (self.cell_near(watch) for watch, _ in w.portals_of(w.outside)) if c >= 0]
        self.exits = np.array(w.exits, dtype=np.int64)  # per region its ways out of the house
        self.ways = int(self.exits.sum()) + len(self.entries)  # all ways into the house
        # the same as arrays (kernels.hidden_regions)
        self.door_ptr = np.cumsum([0] + [len(self.doors[r]) for r in range(self.R)]).astype(np.int64)
        self.door_list = np.array([c for r in range(self.R) for c in self.doors[r]], dtype=np.int64)
        self.entry_idx = np.array(self.entries, dtype=np.int64)
        from .hidden import AGE_EDGES
        self.age_edges = AGE_EDGES
        self.widths = np.diff(np.concatenate([AGE_EDGES, [AGE_EDGES[-1] * 2]]))
        self.A = len(AGE_EDGES)
        self._region_rates = None
        self.g = {}
        self.dist = {}

    # ------------------------------------------------------------ lookups

    def cell_near(self, xy) -> int:
        """The tile of a point, or the nearest one."""
        if not self.n:
            return -1
        d = ((self.fine_xy - np.asarray(xy)[None, :]) ** 2).sum(axis=1)
        return int(self.fine[int(np.argmin(d))])

    def average(self, f_points: np.ndarray) -> np.ndarray:
        """Per tile, the mean of a function given at the sample points (..., P) -> (..., n)."""
        f = np.asarray(f_points)
        if f.ndim == 1:
            return np.bincount(self.point_tile, f, self.n) / self.point_count
        return np.stack([self.average(row) for row in f])

    def seen(self, si: int) -> tuple:
        """(geometry 0..1, distance) of every tile for sensor si."""
        if si not in self.g:
            self.g[si] = self.average(self.tr._g(si, self.points)) if self.n else np.zeros(0)
            self.dist[si] = self.tr._polar(si, self.centers)[0] if self.n else np.zeros(0)
        return self.g[si], self.dist[si]

    def gauss_mass(self, mean, var) -> np.ndarray:
        """(n,) share of a Gaussian N(mean, diag var) in each tile (by the sample points, summing
        to 1 over the tiles in view). Walls: the points behind one seen from the mean get nothing
        (nobody walks through a wall; the Gaussian itself does not know them) - those with a
        density above 1e-3 of the peak (within 3.7 standard deviations)."""
        d = np.exp(-0.5 * (((self.points - np.asarray(mean)[None, :]) ** 2) / (np.asarray(var)[None, :] + POINT * POINT / 12)).sum(axis=1))
        if not self.world.clear(float(mean[0]), float(mean[1]), 3.72 * math.sqrt(float(np.max(var)) + POINT * POINT / 12)):
            near = np.flatnonzero(d > 1e-3)
            if len(near):
                behind = self.world.crosses_wall(np.repeat(np.asarray(mean, dtype=float)[None, :], len(near), axis=0),
                                                 self.points[near])
                if behind.any() and not behind.all():
                    d[near[behind]] = 0.0
        s = d.sum()
        if not s > 0:
            out = np.zeros(self.n)
            out[self.cell_near(mean)] = 1.0
            return out
        return np.bincount(self.point_tile, d / s, self.n)

    def near_doors(self, mean, var) -> np.ndarray:
        """(R + 1,) rate (1/s) of walking into each region and out of the house for a walker at
        N(mean, diag var): their mass in the tiles next to a door (each tile as its small Gaussian)
        times the tiles' rates."""
        if not len(self.door_tiles):
            return np.zeros(self.R + 1)
        t = self.door_tiles
        v = np.asarray(var)[None, :] + self.var[t]
        dens = np.exp(-0.5 * (((self.centers[t] - np.asarray(mean)[None, :]) ** 2) / v).sum(axis=1)) / (2 * math.pi * np.sqrt(v.prod(axis=1)))
        return np.minimum(dens * self.area[t], 1.0) @ self.door_rate[t]

    def spread(self, mass: np.ndarray) -> np.ndarray:
        """Masses per tile onto their sample points."""
        return mass[self.point_tile] / self.point_count[self.point_tile]

    def region_rates(self) -> np.ndarray:
        """(R, A): rate (1/s) at which a stay in each region and age bin ends (from Dwell)."""
        if self._region_rates is None:
            tr = self.tr
            out = np.zeros((self.R, self.A))
            hi = self.age_edges + self.widths
            for r in range(self.R):
                name = self.world.places[r + 1]
                for a in range(self.A):
                    s0 = tr.dwell.survival(name, self.age_edges[a])
                    s1 = tr.dwell.survival(name, hi[a])
                    out[r, a] = math.log(max(s0, 1e-12) / max(s1, 1e-12)) / self.widths[a]
            self._region_rates = out
        return self._region_rates

    def walk_end(self) -> np.ndarray:
        """(n + R + 1, n): where a walk that begins in a tile ends - stopped in a tile, gone into a
        region or out of the house - whatever its length: int mu e^(-mu s) e^(Q s) ds = mu (mu I - Q)^-1 (the walk
        stops at the rate mu, MODEL.md 3.2). For steps much longer than a walk (Hidden.leap)."""
        if self._walk_end is None:
            m = self.tr.m
            mu = m.speed / m.walk_length
            N = self.n + self.R + 1
            self._walk_end = np.linalg.solve(mu * np.eye(N) - self.Q, mu * np.eye(N)[:, :self.n]) if self.n else np.zeros((N, 0))
            self._walk_end[self._walk_end < 1e-12] = 0.0
        return self._walk_end

    def fingerprint(self) -> str:
        """What a density over these tiles depends on: the tiles (where, how big, which room), the
        places behind the doors and the kinds of stay and detectability. A saved state (Tracker.
        people_state) fits only tiles with the same fingerprint."""
        import hashlib
        h = hashlib.sha1()
        for a in (np.round(self.centers, 3), np.round(self.area, 4), self.room, self.exits,
                  np.round(self.sh.go, 12), np.round(self.sh.kappa, 9), self.age_edges):
            h.update(np.ascontiguousarray(a, dtype=float).tobytes())
        h.update("|".join(self.world.places).encode())
        return h.hexdigest()[:16]

    def region_start(self, r: int) -> np.ndarray:
        """Age distribution of a stay in region r seen at a random time: proportional to its
        survival (renewal theory)."""
        name = self.world.places[r + 1]
        mid = self.age_edges + 0.5 * self.widths
        s = np.array([self.tr.dwell.survival(name, a) for a in mid]) * self.widths
        return s / s.sum()


def expm(A: np.ndarray) -> np.ndarray:
    """Matrix exponential by scaling and squaring of the Taylor series (A a rate matrix times a
    time: its norm is small after scaling, the series converges fast)."""
    norm = float(np.abs(A).sum(axis=0).max()) if A.size else 0.0
    s = max(0, int(math.ceil(math.log2(norm / 0.25))) if norm > 0.25 else 0)
    B = A / 2 ** s
    out = np.eye(len(A))
    term = np.eye(len(A))
    for k in range(1, 16):
        term = term @ B / k
        out = out + term
    for _ in range(s):
        out = out @ out
    return out
