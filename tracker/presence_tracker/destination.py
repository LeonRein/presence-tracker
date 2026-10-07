"""Where a walker goes next ("Ziel", MODEL.md 6): a map, learned by the app, of where the walks went
from each place, in each direction and at each speed.

The map: per cell (CELL m squares, split at the rooms like the tiles), per velocity cell (SECTORS
directions x the speed classes SPEEDS) and per outcome - the room behind the first door the walk goes
through, a region without a sensor, out of the house, or "stays" (the walk ends without a door) - the
number of walks that went so (J_Luber2014 ch. 6, sec. 6.5: place-dependent motion learned from
tracks; G_Liao2003: transitions learned on a graph). Each step of a walk counts with a Gaussian kernel
(BW_POS m, BW_VEL m/s, as the kernel map of the investigation) on the cells around it, normalized so
that one walk straight through a cell's centre at its velocity counts 1 there: the counts are walks.
Spread only within the walk's own space (its room and the rooms joined to it by a divider) and not
across a wall.

A divider is no door: rooms joined by one are one space for the walks (an open boundary where people
come and go; the investigation found nothing predictable there, and the light of both is one group).
A walk that crosses it goes on; its outcome is the next door.

Learned from the model's own walkers (Learner): per hypothesis and person with a track, weighted by the
hypothesis and by P(walking) (soft counts over all kept hypotheses, MODEL.md 10 "Geisterkarte"). A
person is followed by their earliest live track; a walk whose tracks end is taken over by a walker
who appears within LINK_DIST m and LINK_TIME s (a hand-over, also at a door). Its outcome:
the room or region the person is in for ARRIVE steps after a door, or "stays" after STOP_TIME s
standing; a walk that ends otherwise (the tracks end, nobody takes it over) is not counted.

The map is an output only: it never changes the filter's state or weights. Counts fade with FORGET.
It belongs to its floor plan (cells, rooms, doors, regions): when that changes it starts empty
(Geometry.fingerprint); a recalibrated sensor changes nothing of it.
"""

import base64
import hashlib
import math
import zlib

import numpy as np

from .floorplan import wall_pieces
from .world import CELL as FINE, OBSERVED

CELL = 0.3  # m: side of a cell of the map
SECTORS = 16  # directions
SPEEDS = (0.3, 0.7, 1.1)  # m/s: lower edges of the speed classes (the last open)
SPEED_MID = (0.5, 0.9, 1.35)  # m/s: their centres
BW_POS = 0.3  # m: kernel (the investigation's kernel map)
BW_VEL = 0.3  # m/s
MIN_SPEED = 0.3  # m/s: slower walkers have no direction worth learning or asking
STOP_TIME = 2.0  # s standing (P(walking) < 1/2): the walk ended in the room
ARRIVE = 2  # steps in the room behind a door: the walk went there
LINK_TIME = 1.5  # s
LINK_DIST = 1.0  # m
MAX_PENDING = 60.0  # s: a walk's steps older than this are dropped (walks last 2.4 s on average)
MIN_WEIGHT = 0.02  # a walker with less weight (hypothesis x P(walking)) is not counted
FORGET = 14 * 86400.0  # s
DECAY_EVERY = 60.0  # s
NEAR = 2.5 * BW_POS  # m: the kernel reaches this far
QUERY = 0.5 * CELL  # m: asked, the map is averaged over the cells around the place with this spread


def _vel_cells():
    ang = 2 * math.pi * np.arange(SECTORS) / SECTORS
    return np.array([[s * math.cos(a), s * math.sin(a)] for s in SPEED_MID for a in ang])


VEL = _vel_cells()  # (V, 2) centres of the velocity cells, speed class major


def vel_cell(vx: float, vy: float) -> int:
    s = math.hypot(vx, vy)
    k = sum(1 for e in SPEEDS[1:] if s >= e)
    sec = int(round(math.atan2(vy, vx) / (2 * math.pi / SECTORS))) % SECTORS
    return k * SECTORS + sec


def vel_interp(vx: float, vy: float):
    """(velocity cells, weights summing to 1): linear interpolation over direction and speed."""
    s = math.hypot(vx, vy)
    a = (math.atan2(vy, vx) / (2 * math.pi / SECTORS)) % SECTORS
    s0 = int(math.floor(a))
    fa = a - s0
    secs = (s0 % SECTORS, (s0 + 1) % SECTORS)
    mids = SPEED_MID
    if s <= mids[0]:
        ks, fs = (0, 0), 0.0
    elif s >= mids[-1]:
        ks, fs = (len(mids) - 1, len(mids) - 1), 0.0
    else:
        k = max(i for i in range(len(mids)) if mids[i] <= s)
        ks, fs = (k, k + 1), (s - mids[k]) / (mids[k + 1] - mids[k])
    cells = np.array([ks[0] * SECTORS + secs[0], ks[0] * SECTORS + secs[1], ks[1] * SECTORS + secs[0], ks[1] * SECTORS + secs[1]])
    w = np.array([(1 - fs) * (1 - fa), (1 - fs) * fa, fs * (1 - fa), fs * fa])
    return cells, w


def vel_weights(vx: float, vy: float):
    """(velocity cells, kernel weights) of a velocity, those above 0.02."""
    w = np.exp(-0.5 * ((VEL - [vx, vy]) ** 2).sum(axis=1) / BW_VEL ** 2)
    k = np.flatnonzero(w > 0.02)
    return k, w[k]


class Geometry:
    """The map's cells and outcomes for a floor plan (from the tracker's world and rooms)."""

    def __init__(self, tr):
        w = tr.world
        self.world = w
        self.rooms = list(tr.rooms)
        K = len(self.rooms)
        self.R = len(w.places) - 2  # regions without a sensor
        # outcomes: the observed rooms, the regions, out of the house, stays
        self.names = self.rooms + list(w.regions) + ["outside", "stays"]
        self.C = len(self.names)
        self.OUT, self.STAY = K + self.R, K + self.R + 1
        room_of = tr.room_of
        # spaces: rooms joined by a divider
        parent = list(range(K))

        def find(a):
            while parent[a] != a:
                a = parent[a]
            return a
        index = {r: k for k, r in enumerate(self.rooms)}
        for (ax, ay), (bx, by), kind in wall_pieces(tr.config.walls):
            if kind != "divider":
                continue
            ln = math.hypot(bx - ax, by - ay)
            if ln <= 0:
                continue
            nx, ny = -(by - ay) / ln, (bx - ax) / ln
            cx, cy = (ax + bx) / 2, (ay + by) / 2
            side = []
            for s in (0.25, -0.25):
                i, j = w.cell_of(np.array([[cx + s * nx, cy + s * ny]]))
                side.append(int(room_of[i[0], j[0]]))
            if min(side) >= 0 and side[0] != side[1]:
                parent[find(side[0])] = find(side[1])
        self.space = np.array([find(k) for k in range(K)], dtype=np.int64)
        # the 0.1 m cells in view -> cells of the map (CELL squares split by room)
        ii, jj = np.nonzero(room_of >= 0)
        b = max(int(round(CELL / FINE)), 1)
        keys = np.stack([ii // b, jj // b, room_of[ii, jj]], axis=1)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True) if len(ii) else (np.zeros((0, 3), int), np.zeros(0, int))
        inv = inv.ravel()
        self.n = n = len(uniq)
        self.cell_room = uniq[:, 2].astype(np.int64) if n else np.zeros(0, np.int64)
        fine_xy = np.stack([w.x0 + (ii + 0.5) * FINE, w.y0 + (jj + 0.5) * FINE], axis=1)
        cnt = np.bincount(inv, minlength=n).astype(float)
        self.centers = np.stack([np.bincount(inv, fine_xy[:, k], n) / cnt for k in range(2)], axis=1) if n else np.zeros((0, 2))
        self.cell_of_fine = np.full(room_of.shape, -1, dtype=np.int64)
        self.cell_of_fine[ii, jj] = inv
        # per cell its neighbours for the kernel: same space, within NEAR, no wall in between
        self.neigh = []
        for c in range(n):
            d = np.hypot(*(self.centers - self.centers[c]).T)
            k = np.flatnonzero((d <= NEAR + CELL) & (self.space[self.cell_room] == self.space[self.cell_room[c]]))
            k = k[k != c]
            if len(k):
                k = k[~w.crosses_wall(np.repeat(self.centers[c][None, :], len(k), axis=0), self.centers[k])]
            self.neigh.append(np.concatenate([[c], k]).astype(np.int64))
        # what a walk in each space can end in, and where its doors are (for the distance to them)
        S = K
        self.allowed = np.zeros((S, self.C), dtype=bool)
        self.allowed[:, self.STAY] = True
        doors = {}
        lab = w.labels
        nxf, nyf = lab.shape
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ti, tj = ii + di, jj + dj
            ok = (ti >= 0) & (ti < nxf) & (tj >= 0) & (tj < nyf)
            k = np.flatnonzero(ok)
            a_room = room_of[ii[k], jj[k]]
            b_room = room_of[ti[k], tj[k]]
            b_lab = lab[ti[k], tj[k]]
            other = np.where(b_room >= 0, b_room, np.where(b_lab > OBSERVED, K + b_lab - 1, -1))
            sel = (other >= 0) & ((b_room < 0) | (self.space[np.maximum(b_room, 0)] != self.space[a_room]))
            k, a_room, other = k[sel], a_room[sel], other[sel]
            if not len(k):
                continue
            p0 = fine_xy[k]
            p1 = p0 + np.array([di, dj]) * FINE
            open_ = ~w.crosses_wall(p0, p1)
            for kk, ar, ot, m in zip(k[open_], a_room[open_], other[open_], (p0[open_] + p1[open_]) / 2):
                s = int(self.space[ar])
                self.allowed[s, int(ot)] = True
                doors.setdefault((s, int(ot)), []).append(m)
        self.doors = {key: np.array(v) for key, v in doors.items()}

    def cell(self, x: float, y: float) -> int:
        i, j = self.world.cell_of(np.array([[x, y]]))
        return int(self.cell_of_fine[i[0], j[0]])

    def around(self, x: float, y: float):
        """(cells, weights summing to 1) to ask the map at (x, y): the cell there and its neighbours
        with a Gaussian of QUERY m (None if not in view)."""
        c = self.cell(x, y)
        if c < 0:
            return None
        cells = self.neigh[c]
        w = np.exp(-0.5 * ((self.centers[cells] - [x, y]) ** 2).sum(axis=1) / QUERY ** 2)
        return cells, w / w.sum()

    def fingerprint(self) -> str:
        h = hashlib.sha1()
        for a in (np.round(self.centers, 3), self.cell_room, self.space, self.allowed, VEL):
            h.update(np.ascontiguousarray(a, dtype=float).tobytes())
        for key in sorted(self.doors):  # where the doors are
            h.update(repr(key).encode())
            h.update(np.ascontiguousarray(np.round(self.doors[key], 2), dtype=float).tobytes())
        h.update("|".join(self.names).encode())
        return h.hexdigest()[:16]

    def door_distance(self, space: int, cls: int, x: float, y: float):
        """Straight distance from (x, y) to the nearest door of the space to cls (None: none)."""
        pts = self.doors.get((space, cls))
        if pts is None or not len(pts):
            return None
        return float(np.sqrt(((pts - [x, y]) ** 2).sum(axis=1).min()))


class DestinationMap:
    """The counts: (cells, velocity cells, outcomes) walks, fading with FORGET."""

    def __init__(self, fingerprint: str, n: int, C: int, forget: float = FORGET):
        self.fingerprint = fingerprint
        self.count = np.zeros((n, len(VEL), C), dtype=np.float32)
        self.forget = forget
        self.walks = 0.0  # walks counted (faded like the counts)
        self._faded = None

    @classmethod
    def for_geometry(cls, geo: Geometry) -> "DestinationMap":
        return cls(geo.fingerprint(), geo.n, geo.C)

    def fade(self, t: float) -> bool:
        if self._faded is None:
            self._faded = t
        if not self.forget or t - self._faded < DECAY_EVERY:
            return False
        f = math.exp(-(t - self._faded) / self.forget)
        self._faded = t
        self.count *= np.float32(f)
        self.walks *= f
        return True

    def add(self, steps, cls: int):
        """Count a walk's steps [(cells, cell weights, velocity cells, velocity weights, weight)] for
        the outcome cls."""
        for cells, cw, vc, vw, wt in steps:
            self.count[cells[:, None], vc[None, :], cls] += (wt * cw[:, None] * vw[None, :]).astype(np.float32)

    def lookup(self, cells, cw, vx: float, vy: float) -> np.ndarray:
        """(outcomes,) walks counted near a place (its cells with weights summing to 1,
        Geometry.around) for a velocity, interpolated between the neighbouring directions and speed
        classes: else a walker at the edge of a cell flickers between two counts."""
        vc, vw = vel_interp(vx, vy)
        return np.einsum("c,v,cvk->k", cw, vw, self.count[cells[:, None], vc[None, :]].astype(float))

    def to_dict(self) -> dict:
        raw = np.ascontiguousarray(self.count, dtype=np.float32).tobytes()
        return {"fingerprint": self.fingerprint, "shape": list(self.count.shape), "walks": self.walks,
                "forget": self.forget, "count": base64.b64encode(zlib.compress(raw, 6)).decode()}

    @classmethod
    def from_dict(cls, d: dict) -> "DestinationMap":
        n, V, C = (int(x) for x in d["shape"])
        m = cls(str(d["fingerprint"]), n, C, float(d.get("forget", FORGET)))
        a = np.frombuffer(zlib.decompress(base64.b64decode(d["count"])), dtype=np.float32)
        if V != len(VEL) or a.size != n * V * C:
            raise ValueError("destination map of another shape")
        m.count = a.reshape(n, V, C).copy()
        m.walks = float(d.get("walks", 0.0))
        return m


class _Walk:
    __slots__ = ("space", "pending", "stand", "last", "cand", "cand_n", "pos")

    def __init__(self, space, t, pos):
        self.space = space
        self.pending = []  # (t, cells, cell weights, velocity cells, velocity weights, weight)
        self.stand = 0.0
        self.last = t
        self.cand = None
        self.cand_n = 0
        self.pos = pos


class Learner:
    """Follows the model's walkers and counts their finished walks into the map (module doc). Reads
    the hypotheses only."""

    def __init__(self):
        self.walks = {}  # key (earliest live track) -> _Walk
        self.orphans = []  # (t, _Walk) of keys gone, to be taken over

    def observe(self, tr, geo: Geometry, dmap: DestinationMap):
        from .filtermodel import WALK
        from .gauss import Gauss
        t = tr.now
        dmap.fade(t)
        hw = tr.hyp_weights()
        feats = {}
        agg = {}
        K = len(geo.rooms)
        for wh, hy in zip(hw, tr.hyps):
            if not hy.groups:
                continue
            gs = hy.group_segs()
            for gid, obj in hy.groups.items():
                if not isinstance(obj, Gauss) or obj.phantom or gid not in gs:
                    continue
                f = feats.get(id(obj))
                if f is None:
                    wts = obj.weights()
                    pw = float(wts[WALK]) * (1.0 - obj.a)
                    st = np.array([obj.mean[WALK, 0, 0], obj.mean[WALK, 1, 0], obj.mean[WALK, 0, 1], obj.mean[WALK, 1, 1]])
                    pos = wts @ obj.pos
                    loc = -1
                    if obj.a > 0.5 and obj.away is not None:
                        pl = obj.away.places()
                        loc = K + int(np.argmax(pl[1:]))  # a region or out of the house
                    else:
                        i, j = tr.world.cell_of(pos[None, :])
                        r = int(tr.room_of[i[0], j[0]])
                        loc = r if r >= 0 else -1
                    f = feats[id(obj)] = (pw, st, loc, pos)
                key = min(gs[gid])
                a = agg.get(key)
                if a is None:
                    a = agg[key] = [0.0, 0.0, np.zeros(4), -1, -1.0, None]
                a[0] += wh
                a[1] += wh * f[0]
                a[2] += wh * f[0] * f[1]
                if wh > a[4]:
                    a[3], a[4], a[5] = f[2], wh, f[3]
        # keys gone: their walks wait to be taken over
        for key in list(self.walks):
            if key not in agg:
                wk = self.walks.pop(key)
                if wk.pending and wk.space is not None:
                    self.orphans.append((t, wk))
        self.orphans = [(t0, wk) for t0, wk in self.orphans if t - t0 <= LINK_TIME]
        for key, (W, walk, wst, loc, _, pos) in agg.items():
            wk = self.walks.get(key)
            space = int(geo.space[loc]) if 0 <= loc < K else None
            if wk is None:  # a new key: the walk of one gone nearby goes on (a hand-over, also at a door)
                for k, (t0, ow) in enumerate(self.orphans):
                    if math.hypot(*(ow.pos - pos)) <= LINK_DIST:
                        wk = ow
                        del self.orphans[k]
                        break
                if wk is None:
                    wk = _Walk(space, t, pos)
                self.walks[key] = wk
            dt = min(max(t - wk.last, 0.0), 0.5)
            wk.last = t
            wk.pos = pos
            self._advance(wk, loc, space, W, walk, wst, dt, t, geo, dmap)

    def _advance(self, wk, loc, space, W, walk, wst, dt, t, geo, dmap):
        K = len(geo.rooms)
        if loc < 0:
            return
        if wk.space is None:  # beyond a door, or nowhere yet: a walk begins in the next space in view
            if space is not None:
                wk.space, wk.pending, wk.stand, wk.cand, wk.cand_n = space, [], 0.0, None, 0
            return
        if space != wk.space:  # through a door (or a region, out)
            cls = loc
            if cls == wk.cand:
                wk.cand_n += 1
            else:
                wk.cand, wk.cand_n = cls, 1
            if wk.cand_n >= ARRIVE:
                if geo.allowed[wk.space, cls] and wk.pending:
                    dmap.add([p[1:] for p in wk.pending], cls)
                    dmap.walks += 1.0
                wk.pending, wk.stand, wk.cand, wk.cand_n = [], 0.0, None, 0
                wk.space = space
            return
        wk.cand, wk.cand_n = None, 0
        frac = walk / W if W > 0 else 0.0
        if frac < 0.5:
            wk.stand += dt
            if wk.stand >= STOP_TIME:
                if wk.pending:
                    dmap.add([p[1:] for p in wk.pending], geo.STAY)
                    dmap.walks += 1.0
                wk.pending = []
                return
        else:
            wk.stand = 0.0
        if walk < MIN_WEIGHT or dt <= 0:
            return
        x, y, vx, vy = wst / walk
        speed = math.hypot(vx, vy)
        if speed < MIN_SPEED:
            return
        c = geo.cell(x, y)
        if c < 0:
            return
        cells = geo.neigh[c]
        d2 = ((geo.centers[cells] - [x, y]) ** 2).sum(axis=1)
        cw = np.exp(-0.5 * d2 / BW_POS ** 2)
        keep = cw > 0.02
        vc, vw = vel_weights(vx, vy)
        # one walk straight through a cell's centre counts 1 there: the kernel integrates to
        # sqrt(2 pi) BW_POS along the path, the step covers speed x dt of it
        wt = walk * speed * dt / (math.sqrt(2 * math.pi) * BW_POS)
        wk.pending.append((t, cells[keep], cw[keep], vc, vw, wt))
        while wk.pending and t - wk.pending[0][0] > MAX_PENDING:
            wk.pending.pop(0)
