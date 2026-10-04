"""The floor plan as the person model sees it (MODEL.md section 2): the observed area, the
places without a sensor behind doors, the walls a person can't walk through.

Places are numbered: 0 is the observed area, 1.. the regions without a sensor (Config.regions),
then "outside". A raster of 0.1 m cells says which place a point belongs to (-1: no room, e.g.
inside a wall).
"""

import math

import numpy as np

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
        # doors out of the observed area: where someone coming out appears, walking inward
        self.portals = []
        for q in config.portals:
            if q.region not in self.index:
                continue
            watch = np.array(q.watch, dtype=float)
            inward = watch - np.array(q.center, dtype=float)
            inward /= max(float(np.linalg.norm(inward)), 1e-6)
            self.portals.append((self.index[q.region], watch, inward))

    def cell_of(self, xy: np.ndarray) -> tuple:
        """Raster indices (clipped to the raster) of the points (n, 2)."""
        i = np.clip(np.floor((xy[:, 0] - self.x0) / CELL).astype(int), 0, self.nx - 1)
        j = np.clip(np.floor((xy[:, 1] - self.y0) / CELL).astype(int), 0, self.ny - 1)
        return i, j

    def sample(self, place: int, n: int, rng) -> np.ndarray:
        """n points evenly in the place (n, 2)."""
        ii, jj = np.nonzero(self.labels == place)
        k = rng.integers(0, len(ii), n)
        return np.stack([self.x0 + (ii[k] + rng.random(n)) * CELL, self.y0 + (jj[k] + rng.random(n)) * CELL], axis=1)

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
        if len(self.walls) == 0 or len(p0) == 0:
            return np.zeros(len(p0), dtype=bool)
        ax, ay, bx, by = (self.walls[:, k][None, :] for k in range(4))
        px, py = p0[:, 0:1], p0[:, 1:2]
        qx, qy = p1[:, 0:1], p1[:, 1:2]

        def orient(x1, y1, x2, y2, x3, y3):
            return (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)

        d1 = orient(ax, ay, bx, by, px, py)
        d2 = orient(ax, ay, bx, by, qx, qy)
        d3 = orient(px, py, qx, qy, ax, ay)
        d4 = orient(px, py, qx, qy, bx, by)
        return ((d1 * d2 < 0) & (d3 * d4 < 0)).any(axis=1)

    def portals_of(self, place: int) -> list:
        return [(watch, inward) for p, watch, inward in self.portals if p == place]
