"""The observed area cut into tiles (MODEL.md 5.3): where a person without a track can be.

A tile is a piece of one room, at most BLOCK m on a side, in which every sensor sees about equally
well: blocks of the 0.1 m place raster, split by room and by how well each sensor sees there (none /
edge of its sight / full), each connected piece one tile. So the edges of what the sensors see and
the walls are tile borders, and the rate at which a sensor starts a track hardly varies inside a
tile. A tile is held as its area, centroid and spread (a small Gaussian, used where a position is
needed: a track starting on somebody there, a person's Gaussian spread onto the tiles).

Walking between tiles is the diffusion limit of the walking model (MODEL.md 3.2): a velocity-jump
process with speed s and turning rate lambda spreads like a diffusion with D = E[s^2] / (2 lambda)
per axis (Saerkkae & Solin 2019, sec. 2.2/4.3; the OU approximation of 3.2 has the same long-time
spread). Discretized by finite volumes: from tile i to a neighbour j at the rate D L_ij / (A_i d_ij)
(L shared border, A area, d distance of the centroids), into a region without a sensor across a door
likewise, with d twice the distance to the door. Walls are no border: nobody walks through them.

Rates (sensors starting tracks, finding a held one) are averaged over the tile at the points of a
0.2 m raster (the tile's sample points), as the projection of a likelihood onto a coarser state
(G_Liao2003 eq. 2).
"""

import math

import numpy as np
from .world import CELL, OBSERVED

BLOCK = 0.6  # m: largest side of a tile
POINT = 0.2  # m: sample points for rates and for the display
MIN_AREA = 0.04  # m^2: smaller pieces join their neighbour in the same room (numerics only)
TICK = 0.1  # s: walking moves the tiles' masses in steps of this length
SIGHT_LEVELS = (0.1, 0.9)  # how well a sensor sees: below, between, above (none / edge / full)


class Tiling:
    def __init__(self, tracker):
        self.tr = tr = tracker
        w = tr.world
        self.world = w
        m = tr.m
        self.sh = tr.shapes
        lab = w.labels
        obs = lab == OBSERVED
        nx, ny = lab.shape
        ii, jj = np.nonzero(obs)
        # the signature of each fine cell: block, room, sight class per sensor
        b = max(int(round(BLOCK / CELL)), 1)
        centers = np.stack([w.x0 + (ii + 0.5) * CELL, w.y0 + (jj + 0.5) * CELL], axis=1)
        sig = [ii // b, jj // b, tr.room_of[ii, jj]]
        for si in range(len(tr.sensors)):
            g = tr._g(si, centers)
            sig.append(np.searchsorted(SIGHT_LEVELS, g))
        sig = np.stack(sig, axis=1)
        _, key = np.unique(sig, axis=0, return_inverse=True)
        key = key.ravel()
        grid = np.full((nx, ny), -1)
        grid[ii, jj] = np.arange(len(ii))
        # neighbours of fine cells (4-neighbourhood) not across a wall
        pairs = []
        for di, dj in ((1, 0), (0, 1)):
            a = grid[: nx - di, : ny - dj]
            c = grid[di:, dj:]
            ok = (a >= 0) & (c >= 0)
            pairs.append(np.stack([a[ok], c[ok]], axis=1))
        pairs = np.concatenate(pairs)
        open_ = ~w.crosses_wall(centers[pairs[:, 0]], centers[pairs[:, 1]]) if len(pairs) else np.zeros(0, bool)
        pairs = pairs[open_]
        # connected pieces of equal signature
        tile = _components(len(ii), pairs[key[pairs[:, 0]] == key[pairs[:, 1]]])
        tile = self._join_small(tile, pairs, tr.room_of[ii, jj])
        self.n = n = int(tile.max()) + 1 if len(tile) else 0
        self.fine = tile  # tile of each fine cell in view
        self.fine_xy = centers
        self.fine_ij = (ii, jj)
        self._outlines = None
        cnt = np.bincount(tile, minlength=n).astype(float)
        self.area = cnt * CELL * CELL
        self.centers = np.stack([np.bincount(tile, centers[:, k], n) / cnt for k in range(2)], axis=1) if n else np.zeros((0, 2))
        d = centers - self.centers[tile] if n else np.zeros((0, 2))
        self.var = np.stack([np.bincount(tile, d[:, k] ** 2, n) / cnt for k in range(2)], axis=1) + CELL * CELL / 12 if n else np.zeros((0, 2))
        self.room = tr.room_of[ii, jj][np.unique(tile, return_index=True)[1]] if n else np.zeros(0, int)
        # sample points: the fine cells on a POINT raster, and each tile's centroid cell if it has none
        p = max(int(round(POINT / CELL)), 1)
        on = (ii % p == p // 2) & (jj % p == p // 2)
        missing = np.setdiff1d(np.arange(n), tile[on])
        extra = [int(np.argmin(((centers - self.centers[t]) ** 2).sum(axis=1) + 1e9 * (tile != t))) for t in missing]
        pick = np.concatenate([np.flatnonzero(on), np.array(extra, dtype=int)])
        self.points = centers[pick]
        self.point_tile = tile[pick]
        self.point_count = np.bincount(self.point_tile, minlength=n).astype(float)
        # borders between tiles, and into the regions without a sensor across a door
        self.R = len(w.places) - 2
        D = (m.speed ** 2 + m.speed_spread ** 2) / (2 * m.turn_rate)
        Q = np.zeros((n + self.R, n + self.R))  # generator, Q[to, from]
        tp = tile[pairs]
        cross = tp[:, 0] != tp[:, 1]
        L = np.zeros((n, n))
        np.add.at(L, (tp[cross, 0], tp[cross, 1]), CELL)
        L = L + L.T
        i_, j_ = np.nonzero(L)
        dist = np.hypot(*(self.centers[i_] - self.centers[j_]).T)
        Q[j_, i_] = D * L[i_, j_] / (self.area[i_] * np.maximum(dist, CELL))
        # door cells: a fine cell in view next to one of a region, not across a wall
        self.door_rate = np.zeros((n, self.R))  # rate (1/s) from a tile into each region
        reg = (lab > OBSERVED) & (lab < w.outside)
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
                r = int(lab[ti[c], tj[c]]) - 1
                mid = centers[c] + 0.5 * CELL * np.array([di, dj])
                border.setdefault((int(tile[c]), r), []).append(mid)
        for (t, r), mids in border.items():
            mids = np.array(mids)
            dd = 2 * max(float(np.hypot(*(mids.mean(axis=0) - self.centers[t]))), CELL)
            self.door_rate[t, r] = D * len(mids) * CELL / (self.area[t] * dd)
            Q[n + r, t] += self.door_rate[t, r]
        Q[np.arange(n), np.arange(n)] = -Q[:, :n].sum(axis=0)
        self.D = D
        self.door_tiles = np.flatnonzero(self.door_rate.sum(axis=1) > 0)
        # one tick of walking: tiles -> tiles, tiles -> regions (the stay there begins)
        T = expm(Q * TICK) if n else np.zeros((self.R, 0))
        T = T[:, :n]
        T[T < 1e-12] = 0.0
        self.T_walk = np.ascontiguousarray(T[:n])
        self.T_in = np.ascontiguousarray(T[n:])
        self.tick = TICK
        # where somebody coming out of a region appears; the ways in from outside straight into view
        self.doors = {r: [] for r in range(self.R)}
        for place, watch, _ in w.portals:
            if place == w.outside:
                continue
            c = self.cell_near(watch)
            if c >= 0:
                self.doors[place - 1].append(c)
        self.entries = []
        for watch, _ in w.portals_of(w.outside):
            c = self.cell_near(watch)
            if c >= 0:
                self.entries.append(c)
        self.open = [p - 1 for p in w.open_places]
        from .hidden import AGE_EDGES
        self.age_edges = AGE_EDGES
        self.widths = np.diff(np.concatenate([AGE_EDGES, [AGE_EDGES[-1] * 2]]))
        self.A = len(AGE_EDGES)
        self._region_rates = None
        self.fresh_stay = self.sh.stay_prior()[:, :, None]  # a stay just begun: (L, K, 1)
        self.g = {}
        self.dist = {}

    @staticmethod
    def _join_small(tile, pairs, room):
        """Pieces smaller than MIN_AREA join the neighbour in the same room they share the longest
        border with (slivers at the edge of a sensor's sight would only cost time)."""
        tile = np.unique(tile, return_inverse=True)[1].ravel()
        while True:
            cnt = np.bincount(tile)
            small = np.flatnonzero(cnt * CELL * CELL < MIN_AREA)
            if not len(small):
                return tile
            tp = tile[pairs]
            same_room = room[pairs[:, 0]] == room[pairs[:, 1]]
            changed = False
            for s in small:
                k = np.flatnonzero(same_room & ((tp[:, 0] == s) ^ (tp[:, 1] == s)))
                if not len(k):
                    continue
                nb = np.where(tp[k, 0] == s, tp[k, 1], tp[k, 0])
                target = int(np.bincount(nb).argmax())
                tile[tile == s] = target
                tp = tile[pairs]
                changed = True
            tile = np.unique(tile, return_inverse=True)[1].ravel()
            if not changed:
                return tile

    def outlines(self) -> list:
        """Per tile its outline: closed loops of [x, y] (the borders of its 0.1 m cells, collinear
        points dropped), for the display."""
        if self._outlines is not None:
            return self._outlines
        w = self.world
        ii, jj = self.fine_ij
        grid = np.full((w.nx + 2, w.ny + 2), -1)
        grid[ii + 1, jj + 1] = self.fine
        edges = {t: {} for t in range(self.n)}
        # each cell's border where the neighbour is another tile, counter-clockwise around the cell
        for di, dj, a, b in ((0, -1, (0, 0), (1, 0)), (1, 0, (1, 0), (1, 1)), (0, 1, (1, 1), (0, 1)), (-1, 0, (0, 1), (0, 0))):
            other = grid[ii + 1 + di, jj + 1 + dj]
            for k in np.flatnonzero(other != self.fine):
                i, j = int(ii[k]), int(jj[k])
                edges[int(self.fine[k])].setdefault((i + a[0], j + a[1]), []).append((i + b[0], j + b[1]))
        out = []
        for t in range(self.n):
            nxt = edges[t]
            loops = []
            while nxt:
                start = next(iter(nxt))
                loop, p = [start], start
                while True:
                    q = nxt[p].pop()
                    if not nxt[p]:
                        del nxt[p]
                    if q == start:
                        break
                    loop.append(q)
                    p = q
                # drop the points in the middle of a straight run
                keep = [loop[m] for m in range(len(loop))
                        if (loop[m][0] - loop[m - 1][0]) * (loop[(m + 1) % len(loop)][1] - loop[m][1])
                        != (loop[m][1] - loop[m - 1][1]) * (loop[(m + 1) % len(loop)][0] - loop[m][0])]
                loops.append([[round(w.x0 + i * CELL, 2), round(w.y0 + j * CELL, 2)] for i, j in keep])
            out.append(loops)
        self._outlines = out
        return out

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
        to 1 over the tiles in view)."""
        d = np.exp(-0.5 * (((self.points - np.asarray(mean)[None, :]) ** 2) / (np.asarray(var)[None, :] + POINT * POINT / 12)).sum(axis=1))
        s = d.sum()
        if not s > 0:
            out = np.zeros(self.n)
            out[self.cell_near(mean)] = 1.0
            return out
        return np.bincount(self.point_tile, d / s, self.n)

    def near_doors(self, mean, var) -> np.ndarray:
        """(R,) rate (1/s) of walking into each region for a walker at N(mean, diag var): its mass in
        the tiles next to a door (each tile as its small Gaussian) times their rates."""
        if not len(self.door_tiles):
            return np.zeros(self.R)
        t = self.door_tiles
        v = np.asarray(var)[None, :] + self.var[t]
        dens = np.exp(-0.5 * (((self.centers[t] - np.asarray(mean)[None, :]) ** 2) / v).sum(axis=1)) / (2 * math.pi * np.sqrt(v.prod(axis=1)))
        return np.minimum(dens * self.area[t], 1.0) @ self.door_rate[t]

    def spread(self, mass: np.ndarray) -> np.ndarray:
        """Masses per tile onto their sample points (for the display)."""
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


def _components(n: int, edges: np.ndarray) -> np.ndarray:
    """Connected components of n nodes (union-find)."""
    parent = np.arange(n)

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    for a, b in edges:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(n)])
    return np.unique(roots, return_inverse=True)[1].ravel()
