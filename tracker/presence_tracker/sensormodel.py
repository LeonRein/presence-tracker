"""What each sensor can see, from the geometry (MODEL.md 4.1, g_s): 0 behind walls and in closed
rooms without a sensor, falling toward the edge of the field of view and beyond the nominal range.
Held on a fixed grid (CELL m, anchored at ORIGIN) over the house's bounding box.
"""

import math

import numpy as np

from .geometry import line_of_sight

CELL = 0.25  # m
ORIGIN = (-30.0, -30.0)  # grid anchor
SIZE = 240  # cells per side: 60 m
PD_MAX = 0.95
# Measured (5.10., 22 h of recordings, the other sensor as reference for a walking person): the
# LD2450 sees walkers up to 7 m as well as near (0.8-1.0 at 5.5-7 m), and still about half of
# them up to 10 degrees beyond its nominal field of view
EDGE_ANGLE = 15.0  # degrees inside the field-of-view edge where it starts to fall
PD_AT_EDGE = 0.5  # share at the nominal edge
BEYOND_ANGLE = 15.0  # degrees beyond the nominal edge where it reaches 0
FULL_RANGE_EXTRA = 1.0  # m beyond the nominal range still seen fully
RANGE_TAIL = 1.5  # m: beyond that it falls by e every this much
RANGE_TAIL_MAX = 4.0  # m beyond the nominal range: 0 from there


def cell_of(x: float, y: float) -> tuple:
    return int((x - ORIGIN[0]) / CELL), int((y - ORIGIN[1]) / CELL)


def cell_center(i: int, j: int) -> tuple:
    return ORIGIN[0] + (i + 0.5) * CELL, ORIGIN[1] + (j + 0.5) * CELL


class SensorModel:
    def __init__(self, config):
        self.config = config
        self.rebuild()

    def rebuild(self):
        """The grid per sensor over the house's bounding box, after a config change."""
        rooms = self.config.zones_of("room")
        self.prior = {}
        if not rooms:
            self.box = (0, 0, 0, 0)
            return
        xs = [p[0] for z in rooms for p in z.geometry.outline(8)]
        ys = [p[1] for z in rooms for p in z.geometry.outline(8)]
        i0, j0 = cell_of(min(xs) - 0.5, min(ys) - 0.5)
        i1, j1 = cell_of(max(xs) + 0.5, max(ys) + 0.5)
        self.box = (i0, j0, i1 + 1, j1 + 1)
        for s in self.config.sensors:
            grid = np.zeros((SIZE, SIZE), dtype=np.float32)
            if s.placed and s.enabled:
                for i in range(i0, i1 + 1):
                    for j in range(j0, j1 + 1):
                        grid[i, j] = self.prior_pd(s, *cell_center(i, j))
            self.prior[s.id] = grid

    def prior_pd(self, s, x: float, y: float) -> float:
        """How well the sensor sees the spot: full in the field of view, falling toward its edge
        and beyond the nominal range."""
        if any(z.contains(x, y) for z in self.config.closed_rooms):
            return 0.0
        lx, ly = s.to_local(x, y)
        r = math.hypot(lx, ly)
        if ly <= 0 or r > s.range + RANGE_TAIL_MAX:
            return 0.0
        az = abs(math.degrees(math.atan2(lx, ly)))
        if az >= s.fov / 2 + BEYOND_ANGLE or not line_of_sight(s.sight_origin(), (x, y), self.config.wall_segments):
            return 0.0
        edge = s.fov / 2
        if az <= edge - EDGE_ANGLE:
            side = 1.0
        elif az <= edge:
            side = 1.0 - (1.0 - PD_AT_EDGE) * (az - edge + EDGE_ANGLE) / EDGE_ANGLE
        else:
            side = PD_AT_EDGE * (1.0 - (az - edge) / BEYOND_ANGLE)
        far = math.exp(-max(r - s.range - FULL_RANGE_EXTRA, 0.0) / RANGE_TAIL)
        return PD_MAX * side * far

    def pd(self, sensor_id: str, x: float, y: float) -> float:
        """From the grid, or computed outside of it."""
        i, j = cell_of(x, y)
        i0, j0, i1, j1 = self.box
        grid = self.prior.get(sensor_id)
        if grid is not None and i0 <= i < i1 and j0 <= j < j1:
            return float(grid[i, j])
        s = self.config.sensor_by_id.get(sensor_id)
        return self.prior_pd(s, x, y) if s is not None and s.placed and s.enabled else 0.0

    def maps(self, sensor_id: str, ghost_map=None) -> dict:
        """For the UI: the sight over the house and, if given, the ghost map's rate (ghost tracks
        per m^2 and hour; None where the sensor doesn't see)."""
        if sensor_id not in self.prior:
            return {}
        i0, j0, i1, j1 = self.box
        sl = (slice(i0, i1), slice(j0, j1))
        prior = self.prior[sensor_id][sl]
        x0, y0 = cell_center(i0, j0)
        out = {
            "cell": CELL, "x0": x0 - CELL / 2, "y0": y0 - CELL / 2, "cols": i1 - i0, "rows": j1 - j0,
            # [column][row], row 0 = lowest y
            "prior": [[round(float(v), 3) for v in row] for row in prior],
        }
        if ghost_map is not None:
            ii, jj = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1), indexing="ij")
            pts = np.stack([ORIGIN[0] + (ii.ravel() + 0.5) * CELL, ORIGIN[1] + (jj.ravel() + 0.5) * CELL], axis=1)
            rate = ghost_map.rate(sensor_id, pts).reshape(ii.shape) * 3600
            out["ghosts"] = [[round(float(v), 4) if p > 0.05 else None for v, p in zip(row, prow)]
                             for row, prow in zip(rate, prior)]
            out["watched_h"] = round(ghost_map.time.get(sensor_id, 0.0) / 3600, 1)
        return out
