"""Rooms from walls.

The floor plan is drawn as walls only. Every wall is a polyline with a kind:
  wall     a real wall: blocks the radar's line of sight
  divider  a room boundary without a wall (e.g. between living and dining area): splits rooms,
           blocks nothing
Doors are separate objects on a wall (center + width). For finding rooms they count as closed,
for the line of sight they are open.

Rooms are the closed areas between the walls (faces of the planar graph). Each room zone keeps an
anchor point inside it; after an edit, the face that contains the anchor is the same room, so
names and Home Assistant entities survive moving walls.
"""

import math
import uuid

from .geometry import distance_to_segment, point_in_polygon

SNAP = 0.03  # m, wall ends this close to each other or to a wall are joined
MIN_ROOM_AREA = 0.3  # m², smaller closed areas are slivers, not rooms
DOOR_REACH = 0.25  # m, a door belongs to a wall segment this close to its center


def normalize_walls(walls: list) -> list:
    """Old configs stored walls as plain point lists."""
    out = []
    for w in walls:
        if isinstance(w, dict):
            out.append({"points": [list(p) for p in w.get("points", [])], "kind": w.get("kind", "wall")})
        else:
            out.append({"points": [list(p) for p in w], "kind": "wall"})
    return [w for w in out if len(w["points"]) >= 2]


def wall_pieces(walls: list) -> list:
    """All straight pieces of all walls: (a, b, kind)."""
    out = []
    for w in walls:
        pts = w["points"]
        for a, b in zip(pts, pts[1:]):
            if math.dist(a, b) > 1e-6:
                out.append((tuple(a), tuple(b), w["kind"]))
    return out


# ------------------------------------------------------------------ line of sight

def door_openings(walls: list, doors: list) -> list:
    """For every door: (piece index, t0, t1) along the wall piece it sits on, in meters."""
    pieces = [p for p in wall_pieces(walls) if p[2] == "wall"]
    out = []
    for d in doors:
        c = (d["x"], d["y"])
        best = None
        for i, (a, b, _) in enumerate(pieces):
            dist = distance_to_segment(c[0], c[1], a, b)
            if dist < DOOR_REACH and (best is None or dist < best[0]):
                best = (dist, i)
        if best is None:
            continue
        a, b, _ = pieces[best[1]]
        length = math.dist(a, b)
        t = ((c[0] - a[0]) * (b[0] - a[0]) + (c[1] - a[1]) * (b[1] - a[1])) / length
        half = d.get("width", 0.9) / 2
        out.append((best[1], max(t - half, 0.0), min(t + half, length)))
    return out


def sight_segments(walls: list, doors: list) -> list:
    """Segments that block the radar: real walls without their door openings."""
    pieces = [p for p in wall_pieces(walls) if p[2] == "wall"]
    cuts: dict[int, list] = {}
    for i, t0, t1 in door_openings(walls, doors):
        cuts.setdefault(i, []).append((t0, t1))
    out = []
    for i, (a, b, _) in enumerate(pieces):
        length = math.dist(a, b)
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        start = 0.0
        for t0, t1 in sorted(cuts.get(i, [])):
            if t0 > start:
                out.append(((a[0] + ux * start, a[1] + uy * start), (a[0] + ux * t0, a[1] + uy * t0)))
            start = max(start, t1)
        if start < length:
            out.append(((a[0] + ux * start, a[1] + uy * start), b))
    return out


# ------------------------------------------------------------------------- rooms

def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _intersection(p1, p2, q1, q2):
    """Proper crossing point of two segments, as (t on p, point), or None."""
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if abs(d) < 1e-12:
        return None
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    if 0 < t < 1 and 0 < u < 1:
        return t, (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def faces(walls: list, snap: float = SNAP, min_area: float = MIN_ROOM_AREA) -> list:
    """Closed areas enclosed by the walls (all kinds), as counterclockwise polygons."""
    segs = [(a, b) for a, b, _ in wall_pieces(walls)]

    # split every piece where another one crosses it or ends on it (T-junctions)
    cuts = [[(0.0, a), (1.0, b)] for a, b in segs]
    for i, (a, b) in enumerate(segs):
        length = math.dist(a, b)
        for j, (c, d) in enumerate(segs):
            if i == j:
                continue
            x = _intersection(a, b, c, d)
            if x:
                cuts[i].append(x)
            for e in (c, d):
                if distance_to_segment(e[0], e[1], a, b) < snap:
                    t = ((e[0] - a[0]) * (b[0] - a[0]) + (e[1] - a[1]) * (b[1] - a[1])) / (length * length)
                    if snap / length < t < 1 - snap / length:
                        # the point on this wall, not the loose end next to it
                        cuts[i].append((t, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))))

    nodes: list = []

    def node(p):
        for k, q in enumerate(nodes):
            if math.dist(p, q) < snap:
                return k
        nodes.append(p)
        return len(nodes) - 1

    # points on walls first, so that loose wall ends join them and not the other way round
    for pts in cuts:
        for _, p in pts[2:]:
            node(p)
    edges = set()
    for pts in cuts:
        pts.sort()
        ids = [node(p) for _, p in pts]
        for u, v in zip(ids, ids[1:]):
            if u != v:
                edges.add((min(u, v), max(u, v)))

    adj: dict[int, set] = {}
    for u, v in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    # free-standing wall ends enclose nothing
    loose = [n for n, nb in adj.items() if len(nb) < 2]
    while loose:
        n = loose.pop()
        for m in adj.pop(n, ()):
            adj[m].discard(n)
            if len(adj[m]) == 1:
                loose.append(m)
    adj = {n: nb for n, nb in adj.items() if nb}

    order = {n: sorted(nb, key=lambda m: math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0]))
             for n, nb in adj.items()}
    seen = set()
    out = []
    for u in order:
        for v in order[u]:
            if (u, v) in seen:
                continue
            # walk with the face on the left: at each corner take the next edge clockwise
            ring = []
            a, b = u, v
            while (a, b) not in seen:
                seen.add((a, b))
                ring.append(nodes[a])
                nb = order[b]
                k = nb.index(a)
                a, b = b, nb[(k - 1) % len(nb)]
            area = _signed_area(ring)
            if area > min_area:
                out.append([[round(x, 3), round(y, 3)] for x, y in _drop_collinear(ring)])
    return out


def _signed_area(ring: list) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2


def _drop_collinear(ring: list) -> list:
    out = []
    n = len(ring)
    for i in range(n):
        o, a, b = ring[i - 1], ring[i], ring[(i + 1) % n]
        if abs(_cross(o, a, b)) > 1e-6 * max(math.dist(o, a) * math.dist(a, b), 1e-9):
            out.append(a)
    return out or ring


def interior_point(poly: list) -> list:
    """A point well inside the polygon (as far from the edges as a coarse search finds)."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    edges = list(zip(poly, poly[1:] + poly[:1]))
    best, best_d = None, -1.0
    candidates = [(sum(xs) / len(xs), sum(ys) / len(ys))]
    for i in range(1, 16):
        for j in range(1, 16):
            candidates.append((min(xs) + (max(xs) - min(xs)) * i / 16, min(ys) + (max(ys) - min(ys)) * j / 16))
    for x, y in candidates:
        if not point_in_polygon(x, y, poly):
            continue
        d = min(distance_to_segment(x, y, a, b) for a, b in edges)
        if d > best_d:
            best, best_d = (x, y), d
    return [round(best[0], 3), round(best[1], 3)] if best else list(poly[0])


def sync_rooms(zones: list, walls: list) -> list:
    """Room zones for the current walls; other zones unchanged. `zones` are dicts.

    A room keeps its id, name and entry flag when its anchor lies in one of the new areas. Old
    rooms without an anchor (drawn by hand before) use a point inside their outline.
    """
    polys = faces(walls)
    rooms = [z for z in zones if z.get("kind") == "room"]
    others = [z for z in zones if z.get("kind") != "room"]
    anchors = {}
    for r in rooms:
        a = r.get("anchor")
        if not a:
            outline = r.get("points") or []
            if r.get("shape") == "rect" and len(outline) == 2:
                (x1, y1), (x2, y2) = outline
                outline = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            a = interior_point(outline) if len(outline) >= 3 else None
        anchors[r["id"]] = a

    matched: dict[int, dict] = {}
    for r in rooms:
        a = anchors[r["id"]]
        if not a:
            continue
        for k, poly in enumerate(polys):
            if k not in matched and point_in_polygon(a[0], a[1], poly):
                matched[k] = r
                break

    names = {r["name"] for r in rooms}
    result = []
    for k, poly in enumerate(polys):
        old = matched.get(k)
        if old is None:
            n = 1
            while f"Raum {n}" in names:
                n += 1
            names.add(f"Raum {n}")
            old = {"id": "r" + uuid.uuid4().hex[:7], "name": f"Raum {n}", "entry": False}
        result.append({"id": old["id"], "name": old["name"], "kind": "room", "shape": "polygon",
                       "points": poly, "anchor": interior_point(poly), "entry": bool(old.get("entry"))})
    # keep the previous order of the rooms, new ones at the end
    position = {r["id"]: i for i, r in enumerate(rooms)}
    result.sort(key=lambda z: position.get(z["id"], len(position)))
    return result + others
