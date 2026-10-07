"""The floor plan as the person model sees it (MODEL.md section 2): the observed area, the
places without a sensor behind doors, the walls a person can't walk through.

Places are numbered: 0 is the observed area, 1.. the regions without a sensor (Config.regions),
then "outside". A raster of 0.1 m cells says which place a point belongs to (-1: no room, e.g.
inside a wall).
"""

import math

import numpy as np

from . import kernels

OBSERVED = 0
CELL = 0.1  # m, raster of the place labels


class World:
    def __init__(self, config):
        self.config = config
        self.regions = list(config.regions)  # region ids
        self.places = ["observed"] + self.regions + ["outside"]
        self.outside = len(self.places) - 1
        self.index = {name: i for i, name in enumerate(self.places)}
        # places people can come in to from outside and leave to it (an entry, a stairwell)
        self.open_places = [self.index[rid] for rid, r in config.regions.items() if r["open"]]
        rooms = config.zones_of("room")
        region_of_room = {r: rid for rid, reg in config.regions.items() for r in reg["rooms"]}
        if rooms:
            xs = [b for z in rooms for b in (z.geometry.bounds()[0], z.geometry.bounds()[2])]
            ys = [b for z in rooms for b in (z.geometry.bounds()[1], z.geometry.bounds()[3])]
            self.x0, self.y0 = min(xs) - CELL, min(ys) - CELL
            self.nx = int(math.ceil((max(xs) - self.x0) / CELL)) + 2
            self.ny = int(math.ceil((max(ys) - self.y0) / CELL)) + 2
        else:
            self.x0 = self.y0 = 0.0
            self.nx = self.ny = 1
        self._masks = {}
        self._reach = {}
        self.labels = np.full((self.nx, self.ny), -1, dtype=np.int16)
        for i in range(self.nx):
            for j in range(self.ny):
                x, y = self.x0 + (i + 0.5) * CELL, self.y0 + (j + 0.5) * CELL
                z = next((z for z in rooms if z.contains(x, y)), None)
                if z is not None:
                    rid = region_of_room.get(z.id)
                    self.labels[i, j] = OBSERVED if rid is None else self.index[rid]
        # area per place (m^2): a person in a place without a sensor is somewhere in it
        self.area = np.bincount(self.labels[self.labels >= 0].ravel(), minlength=len(self.places)) * CELL * CELL
        # walls a person can't cross (door gaps cut out, like for the radar's sight)
        segs = [(a[0], a[1], b[0], b[1]) for a, b in config.wall_segments]
        self.walls = np.array(segs, dtype=float).reshape(-1, 4)
        w = self.walls
        self.wall_box = np.stack([np.minimum(w[:, 0], w[:, 2]), np.minimum(w[:, 1], w[:, 3]),
                                   np.maximum(w[:, 0], w[:, 2]), np.maximum(w[:, 1], w[:, 3])], axis=1)
        # per cell the distance from anywhere in it to the nearest wall (a lower bound): moves
        # within that distance of a point cross no wall
        cx = self.x0 + (np.arange(self.nx) + 0.5) * CELL
        cy = self.y0 + (np.arange(self.ny) + 0.5) * CELL
        pts = np.stack(np.meshgrid(cx, cy, indexing="ij"), axis=-1).reshape(-1, 2)
        dist = np.full(len(pts), np.inf)
        for ax, ay, bx, by in w:
            d = np.array([bx - ax, by - ay])
            t = np.clip(((pts - [ax, ay]) @ d) / max(float(d @ d), 1e-12), 0.0, 1.0)
            dist = np.minimum(dist, np.hypot(*(pts - [ax, ay] - t[:, None] * d).T))
        self.wall_dist = (dist - CELL / math.sqrt(2)).reshape(self.nx, self.ny).tolist()
        # doors out of the observed area: where someone coming out appears, walking inward
        self.portals = []
        for q in config.portals:
            if q.region not in self.index:
                continue
            watch = np.array(q.watch, dtype=float)
            inward = watch - np.array(q.center, dtype=float)
            inward /= max(float(np.linalg.norm(inward)), 1e-6)
            self.portals.append((self.index[q.region], watch, inward))

    def reach(self, radius: float) -> tuple:
        """The cells within radius of each cell that a straight line from its centre reaches
        without crossing a wall (the same side of the walls; through a door gap): (offsets (K, 2)
        in cells, mask (K, nx, ny)). Cached per radius."""
        key = round(radius, 3)
        hit = self._reach.get(key)
        if hit is None:
            n = int(radius / CELL)
            offs = np.array([(di, dj) for di in range(-n, n + 1) for dj in range(-n, n + 1)
                             if di * di + dj * dj <= (radius / CELL) ** 2], dtype=int).reshape(-1, 2)
            ii, jj = np.nonzero(self.labels >= 0)
            p0 = np.stack([self.x0 + (ii + 0.5) * CELL, self.y0 + (jj + 0.5) * CELL], axis=1)
            mask = np.zeros((len(offs), self.nx, self.ny), dtype=bool)
            for k, (di, dj) in enumerate(offs):
                ti, tj = ii + di, jj + dj
                ok = (ti >= 0) & (ti < self.nx) & (tj >= 0) & (tj < self.ny)
                ok[ok] = self.labels[ti[ok], tj[ok]] >= 0
                if di == 0 and dj == 0:
                    mask[k, ii[ok], jj[ok]] = True
                    continue
                sel = np.nonzero(ok)[0]
                cross = self.crosses_wall(p0[sel], p0[sel] + np.array([di, dj], dtype=float) * CELL)
                mask[k, ii[sel[~cross]], jj[sel[~cross]]] = True
            hit = self._reach[key] = (offs, mask)
        return hit

    def cell_of(self, xy: np.ndarray) -> tuple:
        """Raster indices (clipped to the raster) of the points (n, 2)."""
        i = np.minimum(np.maximum(((xy[:, 0] - self.x0) / CELL).astype(int), 0), self.nx - 1)
        j = np.minimum(np.maximum(((xy[:, 1] - self.y0) / CELL).astype(int), 0), self.ny - 1)
        return i, j

    def clear(self, x: float, y: float, r: float) -> bool:
        """No wall within r of the point (x, y)."""
        i = min(max(int((x - self.x0) / CELL), 0), self.nx - 1)
        j = min(max(int((y - self.y0) / CELL), 0), self.ny - 1)
        return self.wall_dist[i][j] > r

    def zone_mask(self, zone) -> np.ndarray:
        """Raster of the cells whose centers are in the zone (cached)."""
        mask = self._masks.get(zone.id)
        if mask is None:
            mask = np.zeros((self.nx, self.ny), dtype=bool)
            for i in range(self.nx):
                for j in range(self.ny):
                    mask[i, j] = zone.contains(self.x0 + (i + 0.5) * CELL, self.y0 + (j + 0.5) * CELL)
            self._masks[zone.id] = mask
        return mask

    def place_of(self, xy: np.ndarray) -> np.ndarray:
        """Place index per point (n, 2); -1 outside all rooms."""
        i = np.floor((xy[:, 0] - self.x0) / CELL).astype(int)
        j = np.floor((xy[:, 1] - self.y0) / CELL).astype(int)
        ok = (i >= 0) & (i < self.nx) & (j >= 0) & (j < self.ny)
        out = np.full(len(xy), -1, dtype=np.int16)
        out[ok] = self.labels[i[ok], j[ok]]
        return out

    def crosses_wall(self, p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
        """Per move p0 -> p1 (n, 2): does it cross a wall?"""
        return kernels.crosses_wall(np.ascontiguousarray(p0, dtype=float), np.ascontiguousarray(p1, dtype=float),
                                    self.walls, self.wall_box)

    def reflect(self, p0: np.ndarray, p1: np.ndarray) -> tuple:
        """Per move p0 -> p1 (n, 2): where it ends if walls reflect (MODEL.md 3.2) - mirrored at
        the first wall it crosses (once) - and the unit normal of that wall (0 where it crosses
        none), for mirroring a velocity too."""
        return kernels.reflect(np.ascontiguousarray(p0, dtype=float), np.ascontiguousarray(p1, dtype=float),
                               self.walls, self.wall_box)

    def portals_of(self, place: int) -> list:
        return [(watch, inward) for p, watch, inward in self.portals if p == place]
