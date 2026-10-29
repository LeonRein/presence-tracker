"""Plane geometry in the house frame (meters, Dobby map axes) for drawing the plan: the zones' outlines, the
rooms between the walls (floorplan.py). What the sensors see is the core's (Config.geometry)."""

import math


def point_in_polygon(x: float, y: float, points: list) -> bool:
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def distance_to_segment(x: float, y: float, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


class Shape:
    """Zone outline: rect (two corners), circle (center + radius) or polygon."""

    def __init__(self, shape: str, points: list | None = None, center=None, radius: float = 0.0):
        self.shape = shape
        self.points = [tuple(p) for p in points or []]
        self.center = tuple(center) if center else None
        self.radius = radius
        if shape == "rect":
            (x1, y1), (x2, y2) = self.points
            self.xmin, self.xmax = sorted((x1, x2))
            self.ymin, self.ymax = sorted((y1, y2))
        # a polygon's bounding box: far outside it (by more than the margin and any rounding) a point
        # is not inside, whatever the edges say (contains answers so without them)
        self._box = None
        if shape not in ("rect", "circle") and self.points:
            xs, ys = [p[0] for p in self.points], [p[1] for p in self.points]
            self._box = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        """Point inside, with the outline grown (margin > 0) or shrunk (< 0)."""
        if self.shape == "circle":
            return math.hypot(x - self.center[0], y - self.center[1]) <= self.radius + margin
        if self.shape == "rect":
            return (self.xmin - margin <= x <= self.xmax + margin
                    and self.ymin - margin <= y <= self.ymax + margin)
        if self._box is not None:
            far = max(margin, 0.0) + 1e-6
            x0, y0, x1, y1 = self._box
            if x < x0 - far or x > x1 + far or y < y0 - far or y > y1 + far:
                return False
        inside = point_in_polygon(x, y, self.points)
        if margin == 0.0:
            return inside
        edge = self.edge_distance(x, y)
        return edge <= margin if not inside else (margin > 0 or edge >= -margin)

    def edge_distance(self, x: float, y: float) -> float:
        points = self.outline()
        return min(distance_to_segment(x, y, a, b) for a, b in zip(points, points[1:] + points[:1]))

    def outline(self, segments: int = 32) -> list:
        if self.shape == "circle":
            cx, cy = self.center
            return [(cx + self.radius * math.cos(2 * math.pi * i / segments),
                     cy + self.radius * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
        if self.shape == "rect":
            return [(self.xmin, self.ymin), (self.xmax, self.ymin), (self.xmax, self.ymax), (self.xmin, self.ymax)]
        return list(self.points)

    def bounds(self) -> tuple:
        pts = self.outline()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def area(self) -> float:
        pts = self.outline(64)
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2
