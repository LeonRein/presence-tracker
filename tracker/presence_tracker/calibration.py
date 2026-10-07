"""Sensor calibration (MODEL.md 10, "Kalibrierung"): heading and scale of every LD2450 from people
walking, for all sensors at once, also for sensors that share only a door area with another one.

The drawn positions are the ruler of the whole and stay. The mirror (x axis to the left) is how the
sensor is installed; it is taken from the configuration and only compared on request. One posterior
over the headings and scales of all sensors (bundle adjustment, Triggs et al. 2000), with four kinds
of evidence:

(a) Pairs: a person walking, measured by two sensors at the same time (one moving target each, the
    one interpolated to the other's frame times within one LD2450 track), is at one place. Gaussian
    error from the measurement model (MODEL.md 4.1) plus a uniform part for wrong pairs (a second
    person, an echo, a ghost): the outlier mixture of Myronenko & Song 2010, fitted by EM.
(b) Handovers: someone leaving one sensor's view and entering another's walks on in between
    (Rahimi, Dunagan & Darrell 2004: a motion model over the paths connects the poses of sensors
    that don't overlap). The end of one LD2450 track, carried forward over the gap of up to 2 s with
    its velocity by the walking model (MODEL.md 3.2: Ornstein-Uhlenbeck velocity), and the start of
    another sensor's track are a pair with the variance of that prediction, in the same mixture.
(c) Floor plan: a walking person is where the sensor can see him directly: in a room, in its line
    of sight (through door openings, not through walls). Per LD2450 track (MODEL.md 2: every track
    has an owner, a person or an echo) a mixture of "person": every point uniform on the visible free
    space, blurred with the measurement error (the likelihood field of Thrun, Burgard & Fox 2005,
    6.4, for free space instead of obstacles), and "echo": uniform on the view out to 8 m. Per
    track, not per point: a few points of an echo track that a turn would bring into the room don't
    pull the heading. It judges the heading, not the scale (people don't come up to the walls, and
    an echo 1.5 times as far looks like a person at 2/3 the scale).
(d) The scales of the LD2450s: ln scale ~ N(mu, tau^2) for every sensor; mu the median of the
    sensors that pairs measure against another one (hierarchical), tau as measured between the
    LD2450s here. A sensor without such pairs gets the scale of the others, and none is proposed.

The drawn heading is a weak prior (+-20 deg: the drawing is rough). Pairs and floor-plan points both
at most one per second (per sensor pair, per track): their errors wander with 3.5 s, and the same
rate keeps their weights fair. Fitted by EM for the mixture weights with Gauss-Newton steps; the
start is the best heading of a coarse search per sensor, since the floor plan has several modes.

What is proposed (solve): a heading or scale whose uncertainty is small enough (DETERMINED_*). The
uncertainty is the Laplace one with a cluster-robust covariance (Liang & Zeger 1986) over 30 s
blocks (the errors of one LD2450 track are correlated over tens of seconds, MODEL.md 4.1), plus a
model error measured on everyday data (MODEL_*). No proposal where the heading has a second mode,
and none where the floor plan alone and the pairs and handovers alone want different headings:
then the drawing (the sensor's position, the walls) is off, and calibrating would only move the
error elsewhere.

Calibrator: the session in the app. It keeps the measured targets of every frame (via the
LD2450's own tracks, sensortracks.py), independent of the poses, and solves on request.
`tools/calibrate_offline.py` feeds recordings through the same code.
"""

import collections
import itertools
import math
import time
from types import SimpleNamespace

import numpy as np

from .floorplan import wall_pieces
from .model import Config
from .sensortracks import SensorTracks

MIN_SPEED = 0.05  # m/s raw radial speed: walking
MIN_RANGE = 0.5  # m on the floor: closer, the slant correction decides everything
MAX_GAP = 0.25  # s, interpolate pairs only between frames this close
EVERY = 1.0  # s, at most one pair per sensor pair, and one walking point per LD2450 track for the
             # floor plan, per this time: the error of a track wanders with 3.5 s (MODEL.md 4.1),
             # denser points only repeat it; the same rate for both keeps their weights fair
HANDOVER = 2.0  # s, longest gap between one sensor's track end and another's start
END_SPAN = (0.3, 1.0)  # s, velocity at a track end from its last second, at least this long
TAU_V = 1 / 0.85  # s, correlation time of the walking velocity (MODEL.md 3.2: lambda_d = 0.85 /s)
VAR_V = 0.425  # m^2/s^2 per axis, its variance (MODEL.md 3.2: (s^2 + spread^2) / 2)
CELL = 0.05  # m, grid of the free-space field
VIEW = 8.0  # m, echoes: uniform on the view out to here
OUTLIER_AREA = 30.0  # m^2, wrong pairs: residual uniform on this area
PRIOR_DEG = 20.0  # heading prior around the drawn one (assumed: drawn by eye)
PRIOR_HEADING = math.radians(PRIOR_DEG)
PRIOR_LNSCALE = 0.05  # spread of ln scale between LD2450s (measured: four sensors here, 1.01-1.13)
PRIOR_MU = 0.06  # ln of their mean scale (measured, 1.06), until rulers of this home say otherwise
PRIOR_SHIFT = 0.3  # m, only offline (--positions)
MIN_RULER = 25  # agreeing pairs (one per second) for a sensor to have its own scale: it counts for the
                # others' prior and is proposed
BLOCK = 30.0  # s, clusters of the robust covariance: about the life of a walking LD2450 track
SEARCH = np.radians(np.arange(-90, 91, 5))  # coarse search of the heading around the drawn one
SECOND_MODE = 3.0  # adjusted log posterior: another heading closer than this is a second mode
MIN_POINTS = 5  # walking points (1 per second and track) for a sensor to be fitted at all
# A value counts as determined when its uncertainty moves a person 5 m away by less than the
# LD2450's own error there (MODEL.md 4.1: 0.10 + 0.05 * 5 = 0.35 m across, 0.15 + 0.02 * 5 = 0.25 m
# along).
DETERMINED_HEADING = math.degrees(math.atan(0.35 / 5))  # 4.0 deg
DETERMINED_SCALE = 0.25 / 5  # 0.05
# Model error: how far the fits of 2-h windows of everyday data (6./7.10., 7-8 windows) scatter
# beyond their own uncertainty, root mean square over the four sensors with pairs (measured; drawn
# positions slightly off, the LD2450's angle error, the floor plan). Added to the uncertainty of
# every fit.
MODEL_HEADING = 2.1  # deg
MODEL_SCALE = 0.042
# Independent stretches: errors of the model (a drawn position slightly off, the LD2450's angle error)
# depend on where people walk, so a fit holds only if it holds across stretches of different
# everyday situations. A stretch is an hour; the uncertainty is a delete-one-group jackknife over at
# most JACK_GROUPS groups of consecutive hours (Kuensch 1989, blocks of a dependent series). With
# fewer than MIN_STRETCHES hours with walking data of a sensor, its spread between situations can't be
# estimated, and nothing is proposed for it: on 7.10. a 10-minute walk alone turned the hall by 17 deg,
# self-consistent within the walk (jackknife over its minutes 1.6 deg), but it put thousands of the
# hall's everyday measurements behind walls (MODEL.md 10).
STRETCH = 3600.0  # s
MIN_STRETCHES = 3
JACK_GROUPS = 8
KEEP = 24 * 3600.0  # s of walking measurements the app keeps (about 5 MB a day for six sensors)
T, SEG, LX, LY, V, SINGLE = range(6)  # columns of the measurement rows


# ------------------------------------------------------------------------------ measurements

def track_frame(tracks: dict, ids, rows: dict, sensor_id: str, t: float, frame: dict):
    """One LD2450 frame into rows of its walking targets (t, track, raw x, raw y (m), raw radial
    speed (m/s), whether it is the only moving target of its frame), through the sensor's own
    tracks: coasted and frozen frames are no measurement (sensortracks.py, MODEL.md 4.1)."""
    dets = [SimpleNamespace(slot=tg.get("slot", 0), local=(tg["x"] / 1000, tg["y"] / 1000), hidden=False)
            for tg in frame.get("targets", []) if tg["y"] > 0]
    ev = tracks.setdefault(sensor_id, SensorTracks(sensor_id, ids)).update(t, frame, dets)
    speed = {tg.get("slot", 0): tg.get("speed", 0) / 1000 for tg in frame.get("targets", [])}
    meas = [(seg, d, speed.get(d.slot, 0.0)) for seg, d in ev.born + ev.measured]
    moving = sum(abs(v) >= MIN_SPEED for _, _, v in meas)
    for seg, d, v in meas:
        if abs(v) >= MIN_SPEED:  # only walking counts (pairs, handovers, floor plan)
            rows[sensor_id].append((t, seg, d.local[0], d.local[1], v, moving == 1))


def thin(t: np.ndarray, key: np.ndarray, every: float) -> np.ndarray:
    """Indices keeping at most one entry per `every` seconds per key."""
    keep, last = [], {}
    for i in np.argsort(t, kind="stable"):
        k = key[i]
        if t[i] - last.get(k, -1e18) >= every:
            keep.append(i)
            last[k] = t[i]
    return np.array(sorted(keep), dtype=int)


def walking_times(config: Config, sid: str, a: np.ndarray) -> np.ndarray:
    """The times of one sensor's walking points as Problem takes them (one per EVERY seconds and
    LD2450 track, at least MIN_RANGE away on the floor): for the progress before a solve."""
    s = config.sensor_by_id[sid]
    z = ground(s, a[:, LX], a[:, LY], s.mirror, config.params.target_height)
    idx = np.flatnonzero((np.abs(a[:, V]) >= MIN_SPEED) & (np.abs(z) >= MIN_RANGE))
    idx = idx[thin(a[idx, T], a[idx, SEG], EVERY)]
    return a[idx, T]


def ground(s, lx, ly, mirror: bool, target_height: float) -> np.ndarray:
    """Raw LD2450 meters -> floor point in the sensor frame as complex x + iy (as SensorConfig.to_world
    without scale and pose)."""
    x = -lx if mirror else lx
    slant = np.hypot(x, ly)
    dh = s.height - target_height
    g = np.sqrt(np.maximum(slant**2 - dh**2, 0.01))
    k = g / np.maximum(slant, 1e-6)
    return x * k + 1j * ly * k


def heading_of(c: complex) -> float:
    """Heading of a sensor whose frame is turned by the angle of c (its forward axis is y)."""
    return (math.degrees(math.atan2(c.imag, c.real)) + 90) % 360


def angle_diff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180


# ---------------------------------------------------------------------------------- geometry

def _in_polygon(px, py, pts) -> np.ndarray:
    inside = np.zeros(px.shape, bool)
    n = len(pts)
    for i in range(n):
        (xi, yi), (xj, yj) = pts[i], pts[i - 1]
        cross = (yi > py) != (yj > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = (xj - xi) * (py - yi) / (yj - yi) + xi
        inside ^= cross & (px < xs)
    return inside


def _sight(origin, px, py, segments) -> np.ndarray:
    """Line of sight from origin to every point (no wall segment crossed)."""
    ox, oy = origin
    free = np.ones(px.shape, bool)
    for (ax, ay), (bx, by) in segments:
        d1 = (bx - ax) * (oy - ay) - (by - ay) * (ox - ax)
        d2 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        d3 = (px - ox) * (ay - oy) - (py - oy) * (ax - ox)
        d4 = (px - ox) * (by - oy) - (py - oy) * (bx - ox)
        free &= ~((d1 * d2 < 0) & (d3 * d4 < 0))
    return free


def _crossing(p, q, segments) -> np.ndarray:
    """For steps p -> q (complex arrays): does it cross any of the segments?"""
    hit = np.zeros(len(p), bool)
    cross = lambda u, v, w: ((v - u) * np.conj(w - u)).imag  # noqa: E731
    for (ax, ay), (bx, by) in segments:
        a, b = complex(ax, ay), complex(bx, by)
        d1, d2 = cross(a, b, p), cross(a, b, q)
        d3, d4 = cross(p, q, a), cross(p, q, b)
        hit |= (d1 * d2 < 0) & (d3 * d4 < 0)
    return hit


class FreeSpace:
    """Where a sensor can see a person directly: in a room that is not a closed room without a
    sensor, in its line of sight. On a grid: for every cell outside, the nearest free point."""

    def __init__(self, config: Config, origin):
        rooms = [z for z in config.zones_of("room") if z not in config.closed_rooms]
        b = np.array([z.geometry.bounds() for z in rooms])
        self.x0, self.y0 = b[:, 0].min() - 2.0, b[:, 1].min() - 2.0
        x1, y1 = b[:, 2].max() + 2.0, b[:, 3].max() + 2.0
        self.nx, self.ny = int((x1 - self.x0) / CELL) + 1, int((y1 - self.y0) / CELL) + 1
        gx = self.x0 + CELL * (np.arange(self.nx) + 0.5)
        gy = self.y0 + CELL * (np.arange(self.ny) + 0.5)
        px, py = np.meshgrid(gx, gy, indexing="ij")
        inroom = np.zeros(px.shape, bool)
        for z in rooms:
            inroom |= _in_polygon(px, py, z.geometry.outline())
        free = inroom & _sight(origin, px, py, config.wall_segments)
        self.free = free
        self.area = max(free.sum() * CELL * CELL, 1.0)
        # nearest free cell for every cell: brute force against the free cells on the edge
        edge = free & ~(np.roll(free, 1, 0) & np.roll(free, -1, 0) & np.roll(free, 1, 1) & np.roll(free, -1, 1))
        ec = px[edge] + 1j * py[edge]
        cells = px + 1j * py
        near = cells.copy()
        out = np.flatnonzero(~free.ravel())
        flat = cells.ravel()
        if len(ec):
            for k in range(0, len(out), 4000):
                idx = out[k:k + 4000]
                j = np.argmin(np.abs(flat[idx, None] - ec[None, :]), axis=1)
                near.ravel()[idx] = ec[j]
        self.near = near

    def lookup(self, w: np.ndarray):
        """(nearest free point, distance) for points w (complex); distance 0 inside."""
        i = np.clip(((w.real - self.x0) / CELL).astype(int), 0, self.nx - 1)
        j = np.clip(((w.imag - self.y0) / CELL).astype(int), 0, self.ny - 1)
        inside = self.free[i, j]
        b = np.where(inside, w, self.near[i, j])
        return b, np.abs(w - b)


# ------------------------------------------------------------------------------------- model

class Problem:
    """The data of one fit: walking points per sensor (for the floor plan), pairs and handovers.

    data: {sensor id: rows (T, SEG, LX, LY, V, SINGLE)}. fit: the sensors whose heading and scale
    are fitted (the others stay as configured). mirrors: {sensor id: mirror} instead of the
    configured ones. positions: sensors whose drawn position may shift (offline only)."""

    def __init__(self, config: Config, data: dict, fit, mirrors=None, positions=()):
        self.config = config
        self.fit = list(fit)
        self.positions = list(positions)
        self.npar = 4 if self.positions else 2
        mirrors = mirrors or {}
        self.mirror = {sid: mirrors.get(sid, s.mirror) for sid, s in config.sensor_by_id.items()}
        p = config.params
        self.th = p.target_height
        # MODEL.md 4.1: the error along and across the line of sight, per axis their mean
        self.sigma0 = (p.range_sigma_base + p.lateral_sigma_base) / 2
        self.sigma1 = (p.range_sigma_slope + p.lateral_sigma_slope) / 2
        self.data = {sid: a[np.argsort(a[:, T], kind="stable")] for sid, a in data.items()
                     if sid in config.sensor_by_id and len(a)}
        self.points, self.pairs = {}, []
        for sid, a in self.data.items():
            z = self.z(sid, a)
            walk = (np.abs(a[:, V]) >= MIN_SPEED) & (np.abs(z) >= MIN_RANGE)
            idx = np.flatnonzero(walk)
            idx = idx[thin(a[idx, T], a[idx, SEG], EVERY)]
            _, track = np.unique(a[idx, SEG], return_inverse=True)
            self.points[sid] = SimpleNamespace(t=a[idx, T], z=z[idx], sigma=self.sigma(z[idx]),
                                               track=track, ntracks=int(track.max()) + 1 if len(idx) else 0)
        for a_id, b_id in itertools.combinations(sorted(self.data), 2):
            q = self._pairs(a_id, b_id)
            if q is not None:
                self.pairs.append(q)
        ends = {sid: self._ends(sid) for sid in self.data}
        for a_id, b_id in itertools.permutations(sorted(self.data), 2):
            q = self._handovers(ends[a_id], ends[b_id], a_id, b_id)
            if q is not None:
                self.pairs.append(q)
        self.rooms = bool([z for z in config.zones_of("room") if z not in config.closed_rooms])
        self.fields = {sid: FreeSpace(config, config.sensor_by_id[sid].sight_origin())
                       for sid in self.points} if self.rooms else {}
        self.views = {sid: math.radians(config.sensor_by_id[sid].fov) / 2 * VIEW**2 for sid in self.points}

    def z(self, sid, a) -> np.ndarray:
        return ground(self.config.sensor_by_id[sid], a[:, LX], a[:, LY], self.mirror[sid], self.th)

    def sigma(self, z):
        return self.sigma0 + self.sigma1 * np.abs(z)

    def _pairs(self, a_id, b_id):
        """One moving target in both sensors at once: a interpolated to b's frame times."""
        A, B = self.data[a_id], self.data[b_id]
        A, B = A[A[:, SINGLE] > 0], B[B[:, SINGLE] > 0]
        if len(A) < 2 or not len(B):
            return None
        k = np.clip(np.searchsorted(A[:, T], B[:, T]), 1, len(A) - 1)
        t0, t1 = A[k - 1, T], A[k, T]
        ok = (B[:, T] > t0) & (B[:, T] <= t1) & (t1 - t0 <= MAX_GAP) & (A[k - 1, SEG] == A[k, SEG])
        if ok.sum() < 3:
            return None
        w = ((B[:, T] - t0) / np.maximum(t1 - t0, 1e-6))[ok]
        za = self.z(a_id, A[k - 1])[ok] * (1 - w) + self.z(a_id, A[k])[ok] * w
        zb = self.z(b_id, B)[ok]
        tb = B[ok, T]
        keep = thin(tb, np.zeros(len(tb)), EVERY)
        keep = keep[(np.abs(za[keep]) >= MIN_RANGE) & (np.abs(zb[keep]) >= MIN_RANGE)]
        if len(keep) < 3:
            return None
        za, zb = za[keep], zb[keep]
        return SimpleNamespace(kind="pair", a=a_id, b=b_id, za=za, zb=zb, t=tb[keep],
                               sigma=np.hypot(self.sigma(za), self.sigma(zb)))

    def _ends(self, sid):
        """Per walking LD2450 track of the sensor: its first point, its last point and the velocity
        over its last second (in the sensor frame)."""
        a = self.data[sid]
        z = self.z(sid, a)
        ok = (np.abs(a[:, V]) >= MIN_SPEED) & (np.abs(z) >= MIN_RANGE)
        t, seg, z = a[ok, T], a[ok, SEG], z[ok]
        order = np.lexsort((t, seg))
        t, seg, z = t[order], seg[order], z[order]
        cut = np.flatnonzero(np.diff(seg)) + 1
        first, last = np.r_[0, cut], np.r_[cut, len(seg)] - 1
        if not len(seg):
            first = last = np.array([], int)
        vel, good = np.zeros(len(last), complex), np.zeros(len(last), bool)
        for n, (f, j) in enumerate(zip(first, last)):
            i = max(f, np.searchsorted(t[f:j + 1], t[j] - END_SPAN[1]) + f)
            if t[j] - t[i] >= END_SPAN[0]:
                vel[n], good[n] = (z[j] - z[i]) / (t[j] - t[i]), True
        order = np.argsort(t[first])
        return SimpleNamespace(t_start=t[first][order], z_start=z[first][order],
                               t_end=t[last][good], z_end=z[last][good], v_end=vel[good])

    def _handovers(self, ea, eb, a_id, b_id):
        """A track of a ends, one of b starts within HANDOVER s: b's start against a's end carried
        forward by the walking model. Ornstein-Uhlenbeck velocity with the measured velocity at the
        end (Sarkka & Solin 2019, ex. 6.2): mean shift v tau (1 - e^(-dt/tau)), variance per axis
        VAR_V tau^2 (2 dt/tau - 3 + 4 e^(-dt/tau) - e^(-2 dt/tau))."""
        za, zb, tt, var = [], [], [], []
        for te, ze, ve in zip(ea.t_end, ea.z_end, ea.v_end):
            lo, hi = np.searchsorted(eb.t_start, te, side="right"), np.searchsorted(eb.t_start, te + HANDOVER)
            for j in range(lo, hi):
                dt = eb.t_start[j] - te
                x = dt / TAU_V
                za.append(ze + ve * TAU_V * (1 - math.exp(-x)))
                zb.append(eb.z_start[j])
                tt.append(eb.t_start[j])
                var.append(VAR_V * TAU_V**2 * (2 * x - 3 + 4 * math.exp(-x) - math.exp(-2 * x)))
        if len(za) < 3:
            return None
        za, zb = np.array(za), np.array(zb)
        sigma = np.sqrt(self.sigma(za) ** 2 + self.sigma(zb) ** 2 + np.array(var))
        return SimpleNamespace(kind="handover", a=a_id, b=b_id, za=za, zb=zb, t=np.array(tt), sigma=sigma)

    # parameters: per fitted sensor [d heading (rad), ln scale, (shift x, shift y)]
    def pose(self, phi, sid, scale=None):
        """(c, p): floor point z in the sensor frame lies at p + c z in the house. scale: use this
        scale instead of the one in phi."""
        s = self.config.sensor_by_id[sid]
        p = complex(s.x, s.y)
        h, k = math.radians(s.heading - 90), s.scale
        if sid in self.fit:
            q = phi[self.npar * self.fit.index(sid):][:self.npar]
            h, k = h + q[0], math.exp(q[1])
            if self.positions:
                p += complex(q[-2], q[-1])
        if scale is not None:
            k = scale
        return k * complex(math.cos(h), math.sin(h)), p

    def world(self, phi, sid, z, scale=None):
        c, p = self.pose(phi, sid, scale)
        return p + c * z

    def lnscale(self, phi, sid):
        return phi[self.npar * self.fit.index(sid) + 1]

    def prior_residuals(self, phi, mu=0.0, tau=PRIOR_LNSCALE):
        """Heading around the drawn one; ln scale ~ N(mu, tau^2), the scales of the LD2450s."""
        r = []
        for i, sid in enumerate(self.fit):
            q = phi[self.npar * i:][:self.npar]
            r += [q[0] / PRIOR_HEADING, (q[1] - mu) / tau]
            if self.positions:
                sd = PRIOR_SHIFT if sid in self.positions else 1e-4
                r += [q[-2] / sd, q[-1] / sd]
        return np.array(r)

    def start(self):
        phi = []
        for sid in self.fit:
            phi += [0.0, math.log(self.config.sensor_by_id[sid].scale)] + ([0.0, 0.0] if self.positions else [])
        return np.array(phi)

    def describe(self, phi, sid) -> dict:
        c, p = self.pose(phi, sid)
        return {"heading": round(heading_of(c), 2), "scale": round(abs(c), 4), "x": round(p.real, 3),
                "y": round(p.imag, 3), "mirror": self.mirror[sid]}


class Fit:
    """EM for the mixtures, Gauss-Newton for the poses."""

    def __init__(self, prob: Problem, use_pairs=True, use_plan=True, weights=None, hold=None):
        self.p = prob
        self.use_pairs, self.use_plan = use_pairs, use_plan and prob.rooms
        self.hold = hold or {}  # sensor -> scale held fixed (profile)
        self.mu, self.tau = PRIOR_MU, PRIOR_LNSCALE
        self.rulers = []
        self.kref = {}  # the scales the floor plan is judged with (see run)
        # bootstrap weights per point / pair (multiplicity of its block)
        self.wp = {sid: np.ones(len(pt.z)) for sid, pt in prob.points.items()}
        self.wq = [np.ones(len(q.za)) for q in prob.pairs]
        if weights is not None:
            self.wp = {sid: weights(pt.t) for sid, pt in prob.points.items()}
            self.wq = [weights(q.t) for q in prob.pairs]
        self.eps_plan = {sid: 0.3 for sid in prob.points}
        self.eps_pair = [0.3 for _ in prob.pairs]

    def _plan_terms(self, phi, sid):
        pt = self.p.points[sid]
        w = self.p.world(phi, sid, pt.z, self.kref.get(sid))
        b, d = self.p.fields[sid].lookup(w)
        return w, b, d

    def loglik(self, phi, details=False):
        """Log posterior (up to a constant) and the responsibilities."""
        total, resp = 0.0, {}
        if self.use_plan:
            # per LD2450 track: its owner is a person (every point where it can be seen) or an
            # echo (anywhere in the view), MODEL.md 2 and 4.2
            for sid, pt in self.p.points.items():
                if not pt.ntracks:
                    continue
                _, _, d = self._plan_terms(phi, sid)
                eps, w = self.eps_plan[sid], self.wp[sid]
                person = np.bincount(pt.track, w * (-0.5 * (d / pt.sigma) ** 2 - math.log(self.p.fields[sid].area)),
                                     pt.ntracks) + math.log(1 - eps)
                echo = np.bincount(pt.track, w * -math.log(self.p.views[sid]), pt.ntracks) + math.log(eps)
                both = np.logaddexp(person, echo)
                total += float(both.sum())
                r = np.exp(person - both)
                resp[("plan", sid)] = r[pt.track]
                resp[("track", sid)] = r
        if self.use_pairs:
            for i, q in enumerate(self.p.pairs):
                r = self.p.world(phi, q.a, q.za) - self.p.world(phi, q.b, q.zb)
                eps = self.eps_pair[i]
                person = (1 - eps) * np.exp(-0.5 * np.abs(r / q.sigma) ** 2) / (2 * math.pi * q.sigma**2)
                wrong = eps / OUTLIER_AREA
                total += float((self.wq[i] * np.log(person + wrong)).sum())
                resp[("pair", i)] = person / (person + wrong)
        total -= 0.5 * float((self.p.prior_residuals(phi, self.mu, self.tau) ** 2).sum())
        total -= 0.5 * sum(((self.p.lnscale(phi, sid) - math.log(k)) / 1e-4) ** 2 for sid, k in self.hold.items())
        return (total, resp) if details else total

    def residuals(self, phi, resp, anchors, times=False):
        """Gauss-Newton residuals at fixed responsibilities and anchors. times: also the time of
        every residual (nan for the priors), for the robust covariance."""
        out = [self.p.prior_residuals(phi, self.mu, self.tau)]
        out.append(np.array([(self.p.lnscale(phi, sid) - math.log(k)) / 1e-4 for sid, k in self.hold.items()]))
        ts = [np.full(len(out[0]) + len(out[1]), np.nan)]
        if self.use_plan:
            for sid, pt in self.p.points.items():
                w = self.p.world(phi, sid, pt.z, self.kref.get(sid))
                b, n = anchors[sid]
                # one-sided spring to the nearest free point, along the normal of that edge
                dist = ((w - b) * np.conj(n)).real
                g = np.sqrt(resp[("plan", sid)] * self.wp[sid])
                out.append(g * np.maximum(dist, 0.0) / pt.sigma)
                ts.append(pt.t)
        if self.use_pairs:
            for i, q in enumerate(self.p.pairs):
                r = (self.p.world(phi, q.a, q.za) - self.p.world(phi, q.b, q.zb)) / q.sigma
                g = np.sqrt(resp[("pair", i)] * self.wq[i])
                out += [g * r.real, g * r.imag]
                ts += [q.t, q.t]
        r = np.concatenate(out)
        return (r, np.concatenate(ts)) if times else r

    def hyper(self, phi, resp):
        """mu of the LD2450 scales from the sensors measured against another one (rulers: at least
        MIN_RULER agreeing pairs), their median: one sensor with wrong pairs (echoes that walk along
        with the people) doesn't move it. tau stays as measured: estimated from a handful of sensors,
        it grew with such a sensor and freed it (kitchen, 7.10.: scale 1.9-2.5)."""
        agree = collections.Counter()
        if self.use_pairs:
            for i, q in enumerate(self.p.pairs):
                n = float((resp[("pair", i)] * self.wq[i]).sum())
                agree[q.a] += n
                agree[q.b] += n
        self.agree = agree
        self.rulers = [sid for sid in self.p.points if agree[sid] >= MIN_RULER]
        lk = [self.p.lnscale(phi, sid) if sid in self.p.fit else math.log(self.p.config.sensor_by_id[sid].scale)
              for sid in self.rulers]
        if len(lk) >= 2:
            self.mu = float(np.median(lk))

    def _anchors(self, phi):
        anchors = {}
        if self.use_plan:
            for sid in self.p.points:
                w, b, d = self._plan_terms(phi, sid)
                anchors[sid] = (b, np.where(d > 1e-9, (w - b) / np.maximum(d, 1e-9), 0))
        return anchors

    def _jacobian(self, phi, resp, anchors, r0):
        J = np.empty((len(r0), len(phi)))
        for j in range(len(phi)):
            e = np.zeros(len(phi))
            e[j] = 1e-5
            J[:, j] = (self.residuals(phi + e, resp, anchors) - self.residuals(phi - e, resp, anchors)) / 2e-5
        return J

    def search(self, phi):
        """Coarse start: per fitted sensor (most walking points first), the best heading on a 5 deg
        grid within +-90 deg of the drawn one, the others held. Gauss-Newton only finds the nearest
        mode, and the floor plan has several."""
        phi = phi.copy()
        npts = lambda sid: len(self.p.points[sid].z) if sid in self.p.points else 0  # noqa: E731
        for sid in sorted(self.p.fit, key=lambda s: (-npts(s), s)):
            i = self.p.npar * self.p.fit.index(sid)
            best = None
            for dh in SEARCH:
                cand = phi.copy()
                cand[i] = dh
                ll = self.loglik(cand)
                if best is None or ll > best[0]:
                    best = (ll, dh)
            phi[i] = best[1]
        return phi

    def run(self, phi=None, iterations=60, tol=1e-6, search=False):
        phi = self.p.start() if phi is None else phi.copy()
        if search:
            phi = self.search(phi)
        last = -math.inf
        H = np.eye(len(phi))
        for _ in range(iterations):
            # The floor plan judges heading and position at the scale of now, it doesn't set the
            # scale: rotation and shift keep areas, so "uniform on the visible space" is a proper
            # density for them; for the scale it is not. The scale comes from pairs and handovers
            # and the scales of the other LD2450s.
            self.kref = {sid: abs(self.p.pose(phi, sid)[0]) for sid in self.p.points}
            ll, resp = self.loglik(phi, details=True)
            self.hyper(phi, resp)
            # M step for the mixture weights
            if self.use_plan:
                for sid, pt in self.p.points.items():
                    if not pt.ntracks:
                        continue
                    wt = np.bincount(pt.track, self.wp[sid], pt.ntracks) / np.maximum(np.bincount(pt.track, None, pt.ntracks), 1)
                    self.eps_plan[sid] = float(np.clip(1 - (resp[("track", sid)] * wt).sum() / max(wt.sum(), 1e-9), 0.01, 0.95))
            if self.use_pairs:
                for i in range(len(self.p.pairs)):
                    self.eps_pair[i] = float(np.clip(1 - (resp[("pair", i)] * self.wq[i]).sum() / max(self.wq[i].sum(), 1e-9),
                                                     0.01, 0.99))
            ll, resp = self.loglik(phi, details=True)
            # Gauss-Newton step with the anchors (nearest free point, normal) of now
            anchors = self._anchors(phi)
            r0 = self.residuals(phi, resp, anchors)
            J = self._jacobian(phi, resp, anchors, r0)
            H = J.T @ J
            step = -np.linalg.solve(H + 1e-9 * np.eye(len(phi)), J.T @ r0)
            # backtracking on the true log posterior
            a = 1.0
            while a > 1e-3:
                cand = phi + a * step
                if self.loglik(cand) >= ll - 1e-9:
                    break
                a /= 2
            else:
                cand = phi
            phi = cand
            if abs(ll - last) < tol * max(1.0, abs(ll)) and np.abs(a * step).max() < 1e-5:
                break
            last = ll
        self.phi = phi
        self.ll = self.loglik(phi)
        self.cov_laplace = np.linalg.inv(H + 1e-9 * np.eye(len(phi)))
        self.cov = self.robust_cov(phi)
        return phi

    def robust_cov(self, phi):
        """Cluster-robust covariance (Liang & Zeger 1986): H^-1 (B + H_prior) H^-1 with B the sum
        over BLOCK-s blocks of the outer products of the blocks' scores. Equal to the Laplace
        covariance H^-1 for independent errors, wider where the points of a block share their error;
        a sensor without data keeps its prior."""
        self.kref = {sid: abs(self.p.pose(phi, sid)[0]) for sid in self.p.points}
        _, resp = self.loglik(phi, details=True)
        anchors = self._anchors(phi)
        r0, ts = self.residuals(phi, resp, anchors, times=True)
        J = self._jacobian(phi, resp, anchors, r0)
        prior = np.isnan(ts)
        H = J.T @ J + 1e-9 * np.eye(len(phi))
        Hp = J[prior].T @ J[prior]
        g = J[~prior] * r0[~prior, None]
        block = np.floor(ts[~prior] / BLOCK).astype(np.int64)
        _, idx = np.unique(block, return_inverse=True)
        G = np.zeros((idx.max() + 1 if len(idx) else 0, len(phi)))
        np.add.at(G, idx, g)
        Hi = np.linalg.inv(H)
        return Hi @ (G.T @ G + Hp) @ Hi

    def sd(self, sid) -> dict:
        """Standard deviations of heading (deg) and scale of a fitted sensor; the robust one and
        the Laplace one."""
        i = self.p.npar * self.p.fit.index(sid)
        k = abs(self.p.pose(self.phi, sid)[0])
        return {"heading": math.degrees(math.sqrt(max(self.cov[i, i], 0))),
                "scale": k * math.sqrt(max(self.cov[i + 1, i + 1], 0)),
                "heading_laplace": math.degrees(math.sqrt(max(self.cov_laplace[i, i], 0))),
                "scale_laplace": k * math.sqrt(max(self.cov_laplace[i + 1, i + 1], 0))}

    def second_mode(self, sid):
        """The best other heading of this sensor (at least 15 deg away, the rest held) and how much
        worse it is, in log posterior divided by the variance inflation of the robust covariance
        (adjusted likelihood ratio, Chandler & Bate 2007): small = a second mode."""
        i = self.p.npar * self.p.fit.index(sid)
        best = None
        for dh in SEARCH:
            if abs(dh - self.phi[i]) < math.radians(15):
                continue
            cand = self.phi.copy()
            cand[i] = dh
            ll = self.loglik(cand)
            if best is None or ll > best[0]:
                best = (ll, dh)
        if best is None:
            return None, math.inf
        k = max(self.cov[i, i] / max(self.cov_laplace[i, i], 1e-12), 1.0)
        s = self.p.config.sensor_by_id[sid]
        return (s.heading + math.degrees(best[1])) % 360, (self.ll - best[0]) / k


# ------------------------------------------------------------------------------------ checks

def outside(prob: Problem, phi) -> dict:
    """Per sensor: share of its walking points more than 0.3 m away from what it can see."""
    out = {}
    for sid, pt in prob.points.items():
        if sid in prob.fields and len(pt.z):
            _, d = prob.fields[sid].lookup(prob.world(phi, sid, pt.z))
            out[sid] = float((d > 0.3).mean())
    return out


def out_of_sight(prob: Problem, phi, sid) -> np.ndarray:
    """Per walking point of the sensor: more than 0.3 m away from what it can see?"""
    pt = prob.points[sid]
    if sid not in prob.fields or not len(pt.z):
        return np.zeros(len(pt.z), bool)
    return prob.fields[sid].lookup(prob.world(phi, sid, pt.z))[1] > 0.3


def more_out_of_sight(prob: Problem, phi0, phi, sid) -> bool:
    """Does the new pose put more of the sensor's walking points out of sight? Paired, point by point
    (McNemar): of the points whose verdict changes, b go out of sight and c come into it; more out
    by more than one standard deviation of b - c (sqrt(b + c)) counts. The points of one track are
    not independent, so this errs on the side of keeping the old pose."""
    a, b_ = out_of_sight(prob, phi0, sid), out_of_sight(prob, phi, sid)
    b, c = int((~a & b_).sum()), int((a & ~b_).sum())
    return b - c > math.sqrt(b + c)


def pair_agreement(prob: Problem, phi) -> dict:
    """Per sensor pair (simultaneous pairs only): count, within 1 m, median distance of those."""
    out = {}
    for q in prob.pairs:
        if q.kind != "pair":
            continue
        r = np.abs(prob.world(phi, q.a, q.za) - prob.world(phi, q.b, q.zb))
        good = r < 1.0
        out[(q.a, q.b)] = (len(r), int(good.sum()), float(np.median(r[good])) if good.any() else float("nan"))
    return out


def crossings(prob: Problem, phi) -> dict:
    """People walk through doors, not walls (Woodman & Harle 2008): per sensor, the steps of its
    walking tracks (one LD2450 track, < 0.5 s apart) that cross a wall line, and how many of them
    pass through an opening."""
    pieces = [(a, b) for a, b, k in wall_pieces(prob.config.walls) if k == "wall"]
    out = {}
    for sid, a in prob.data.items():
        z = prob.z(sid, a)
        keep = (np.abs(a[:, V]) >= MIN_SPEED) & (np.abs(z) >= MIN_RANGE)
        t, seg, w = a[keep, T], a[keep, SEG], prob.world(phi, sid, z[keep])
        order = np.lexsort((t, seg))
        t, seg, w = t[order], seg[order], w[order]
        step = (seg[1:] == seg[:-1]) & (np.diff(t) <= 0.5)
        p, q = w[:-1][step], w[1:][step]
        line = _crossing(p, q, pieces)
        wall = _crossing(p[line], q[line], prob.config.wall_segments)
        out[sid] = (int(line.sum()), int(line.sum() - wall.sum()))
    return out


# ------------------------------------------------------------------------------------- solve

def stretch_groups(prob: Problem) -> list:
    """The hours with walking data, merged into at most JACK_GROUPS groups of consecutive hours with
    about equal numbers of walking points: [(first hour, last hour)]."""
    hours = collections.Counter()
    for pt in prob.points.values():
        hours.update((pt.t // STRETCH).astype(int).tolist())
    keys = sorted(hours)
    if not keys:
        return []
    total, groups, acc, first = sum(hours.values()), [], 0, keys[0]
    for i, h in enumerate(keys):
        acc += hours[h]
        if acc >= total * (len(groups) + 1) / JACK_GROUPS or i == len(keys) - 1:
            groups.append((first, h))
            if i + 1 < len(keys):
                first = keys[i + 1]
    return groups


def jackknife(prob: Problem, phi, groups) -> dict:
    """Delete-one-group jackknife standard deviations of heading (deg) and scale per fitted sensor."""
    reps = []
    for lo, hi in groups:
        def weights(t, lo=lo, hi=hi):
            h = t // STRETCH
            return ((h < lo) | (h > hi)).astype(float)
        Fj = Fit(prob, weights=weights)
        reps.append(Fj.run(phi))
    k = len(reps)
    out = {}
    for sid in prob.fit:
        i = prob.npar * prob.fit.index(sid)
        dh = np.array([(r[i] - phi[i] + math.pi) % (2 * math.pi) - math.pi for r in reps])
        dk = np.array([math.exp(r[i + 1]) for r in reps])
        out[sid] = (math.degrees(math.sqrt((k - 1) / k * ((dh - dh.mean()) ** 2).sum())),
                    math.sqrt((k - 1) / k * ((dk - dk.mean()) ** 2).sum()))
    return out


def solve(config: Config, data: dict, check_mirror: bool = False) -> dict:
    """New heading and scale for every placed sensor with walking measurements, each with its
    uncertainty; only what is determined is proposed. Positions and mirrors stay as configured
    (check_mirror: also fit each sensor mirrored and report which the measurements prefer).

    Per sensor, a value is proposed when its uncertainty (robust covariance and the model error)
    is below DETERMINED_*, the heading has no second mode, and the floor plan alone and the pairs
    and handovers alone give the same heading: if they don't, something in the drawing is off (the
    sensor's position, the walls around it), and no heading or scale fits both. And only if the
    sensor has walking data from at least MIN_STRETCHES hours (its uncertainty then includes the
    jackknife over them), and the new pose doesn't put more of its walking points out of sight."""
    placed = [s.id for s in config.sensors if s.placed and s.enabled]
    data = {sid: np.asarray(a, float).reshape(-1, 6) for sid, a in data.items() if sid in placed}
    base = Problem(config, data, [])
    fit = [sid for sid in placed if sid in base.points and len(base.points[sid].z) >= MIN_POINTS]
    if not fit:
        return {"error": "Keine Messungen einer Person in Bewegung."}
    prob = Problem(config, data, fit)
    F = Fit(prob)
    phi = F.run(search=True)
    # the two kinds of evidence alone: the floor plan at the scales of the joint fit (it doesn't
    # judge scales), the pairs and handovers alone
    Fplan = Fit(prob, use_pairs=False, hold={sid: abs(prob.pose(phi, sid)[0]) for sid in fit})
    Fplan.run(phi, search=True)
    Fpair = Fit(prob, use_plan=False)
    Fpair.run(phi, search=True)
    phi0 = prob.start()
    before, after = outside(prob, phi0), outside(prob, phi)
    agree0, agree1 = pair_agreement(prob, phi0), pair_agreement(prob, phi)
    groups = stretch_groups(prob)
    jack = jackknife(prob, phi, groups) if len(groups) >= MIN_STRETCHES else {}
    out = {}
    for sid in fit:
        s = config.sensor_by_id[sid]
        r = prob.describe(phi, sid)
        sd = F.sd(sid)
        jh, jk = jack.get(sid, (0.0, 0.0))
        sd_h = math.hypot(max(sd["heading"], jh), MODEL_HEADING)
        sd_k = math.hypot(max(sd["scale"], jk), MODEL_SCALE)
        per_hour = collections.Counter((prob.points[sid].t // STRETCH).astype(int).tolist())
        stretches = sum(n >= MIN_POINTS for n in per_hour.values())
        few = stretches < MIN_STRETCHES
        worse = more_out_of_sight(prob, phi0, phi, sid)
        alt, gap = F.second_mode(sid)
        h_plan, h_pair = prob.describe(Fplan.phi, sid)["heading"], prob.describe(Fpair.phi, sid)["heading"]
        sd_plan = math.hypot(Fplan.sd(sid)["heading"], MODEL_HEADING)
        sd_pair = math.hypot(Fpair.sd(sid)["heading"], MODEL_HEADING)
        conflict = (sd_plan < PRIOR_DEG / 2 and sd_pair < PRIOR_DEG / 2
                    and abs(angle_diff(h_plan, h_pair)) > 2 * math.hypot(sd_plan, sd_pair))
        good_heading = sd_h <= DETERMINED_HEADING and gap >= SECOND_MODE and not conflict and not few and not worse
        ruler = sid in F.rulers
        good_scale = sd_k <= DETERMINED_SCALE and ruler and not conflict and not few and not worse
        # per sensor only what is its own; what holds for all (why 3 hours, walk more) the web UI says once
        reasons = []
        walk_more = False
        if few:
            reasons.append(f"Erst {stretches} von {MIN_STRETCHES} Stunden mit Gehenden.")
        elif worse:
            reasons.append(f"Mit dem neuen Wert lägen mehr Punkte in Bewegung außerhalb der Sicht "
                           f"({100 * after[sid]:.0f} % statt {100 * before[sid]:.0f} %). Bleibt wie bisher.")
        elif conflict:
            reasons.append(f"Grundriss ({h_plan:.0f}°) und gemeinsame Messungen mit anderen Sensoren ({h_pair:.0f}°) "
                           "widersprechen sich. Stimmt die eingezeichnete Position des Sensors und der Wände um ihn?")
        else:
            if not good_heading:
                reasons.append(f"Drehung mehrdeutig: {alt:.0f}° passt fast so gut." if gap < SECOND_MODE
                               else f"Drehung unsicher (± {sd_h:.0f}°).")
            if not ruler:
                reasons.append("Maßstab: zu wenig gemeinsame Messungen mit anderen Sensoren.")
            elif not good_scale:
                reasons.append(f"Maßstab unsicher (± {sd_k:.2f}).")
            walk_more = bool(reasons)
        quality = "ok" if good_heading and good_scale else "warn" if good_heading or good_scale else "bad"
        mine0 = [v[2] for k, v in agree0.items() if sid in k and v[1]]
        mine1 = [v[2] for k, v in agree1.items() if sid in k and v[1]]
        out[sid] = {
            "heading": r["heading"] if good_heading else s.heading,
            "scale": r["scale"] if good_scale else s.scale,
            "mirror": s.mirror,
            "fitted_heading": r["heading"], "fitted_scale": r["scale"],
            "heading_sd": round(sd_h, 2), "scale_sd": round(sd_k, 4),
            "heading_plan": h_plan, "heading_pairs": h_pair, "stretches": stretches,
            "jackknife": [round(jh, 2), round(jk, 4)] if jack else None,
            "turn": round(angle_diff(r["heading"], s.heading), 1),
            "apply": {"heading": good_heading, "scale": good_scale},
            "quality": quality, "reason": " ".join(reasons), "few": few, "walk_more": walk_more,
            "points": int(len(prob.points[sid].z)), "tracks": int(prob.points[sid].ntracks),
            "pairs": sum(len(q.za) for q in prob.pairs if q.kind == "pair" and sid in (q.a, q.b)),
            "handovers": sum(len(q.za) for q in prob.pairs if q.kind == "handover" and sid in (q.a, q.b)),
            "agree": int(round(F.agree.get(sid, 0.0))),
            "outside": [round(before.get(sid, 0.0), 3), round(after.get(sid, 0.0), 3)],
            "pair_median": [round(float(np.median(mine0)), 3) if mine0 else None,
                            round(float(np.median(mine1)), 3) if mine1 else None],
        }
    if check_mirror:
        for sid in fit:
            pm = Problem(config, data, fit, mirrors={sid: not prob.mirror[sid]})
            Fm = Fit(pm)
            Fm.run(search=True)
            i = prob.npar * fit.index(sid)
            k = max(F.cov[i, i] / max(F.cov_laplace[i, i], 1e-12), 1.0)
            gain = (Fm.ll - F.ll) / k
            out[sid]["mirror_gain"] = round(gain, 1)
            if gain > SECOND_MODE:
                rm = pm.describe(Fm.phi, sid)
                out[sid].update(mirror=not prob.mirror[sid], heading=rm["heading"], scale=rm["scale"],
                                turn=round(angle_diff(rm["heading"], config.sensor_by_id[sid].heading), 1),
                                apply={"heading": True, "scale": True})
                out[sid]["reason"] = (f"Gespiegelt passen die Messungen besser (log {gain:.0f}). " + out[sid]["reason"]).strip()
    inside = [1 - after[sid] for sid in fit if sid in after]
    return {"sensors": out, "unsolved": [s for s in placed if s not in fit], "min_hours": MIN_STRETCHES,
            "inside": round(float(np.mean(inside)), 3) if inside else None,
            "scale_prior": [round(math.exp(F.mu), 3), round(F.tau, 3)]}


# ----------------------------------------------------------------------------------- session

class Calibrator:
    """The app's calibration data: the walking measurements of every LD2450 frame of the last KEEP
    seconds, collected all the time (in raw sensor coordinates, so a change of the drawing doesn't
    spoil them; a sensor turned or moved for real needs `reset`). Everyday walking connects the
    sensors as well as a calibration walk does, and over a day far better (MODEL.md 10: a 10-minute
    walk alone put the hall's heading 15 deg off); a walk alone through rooms and doors adds what
    everyday walking doesn't reach."""

    CHUNK = 5000  # rows per sensor gathered before they are packed into an array

    def __init__(self, config: Config):
        self.config = config
        self.reset()

    def reset(self):
        self._rows: dict[str, list] = collections.defaultdict(list)
        self._chunks: dict[str, list] = collections.defaultdict(list)
        self._tracks, self._ids = {}, itertools.count()
        self._status, self._status_t = None, -math.inf
        self.since = None

    def on_frame(self, sensor_id: str, t: float, frame: dict):
        """A raw LD2450 frame (the MQTT payload) at the sensor's frame time."""
        if self.since is None:
            self.since = t
        rows = self._rows[sensor_id]
        track_frame(self._tracks, self._ids, self._rows, sensor_id, t, frame)
        if len(rows) >= self.CHUNK:
            chunks = self._chunks[sensor_id]
            chunks.append(np.array(rows, float))
            rows.clear()
            while chunks and chunks[0][-1, T] < t - KEEP:
                chunks.pop(0)
            self.since = max(self.since, t - KEEP)

    def save(self, path):
        """The collected data, for the next start (a restart would otherwise lose up to a day)."""
        data = self.data()
        tmp = path.with_name(path.name + ".tmp.npz")
        np.savez_compressed(tmp, since=np.array([self.since if self.since is not None else np.nan]),
                            **{"s_" + sid: a for sid, a in data.items()})
        tmp.replace(path)

    def load(self, path):
        """What `save` wrote; track numbers go on after the loaded ones."""
        self.reset()
        with np.load(path) as f:
            since = float(f["since"][0])
            self.since = None if math.isnan(since) else since
            last = -1
            for key in f.files:
                if key.startswith("s_") and len(f[key]):
                    self._chunks[key[2:]].append(np.array(f[key], float))
                    last = max(last, int(f[key][:, SEG].max()))
        self._ids = itertools.count(last + 1)

    def data(self) -> dict:
        out = {}
        for sid in sorted(set(self._rows) | set(self._chunks)):
            parts = self._chunks.get(sid, []) + [np.array(self._rows[sid], float).reshape(-1, 6)]
            a = np.concatenate(parts)
            if len(a):
                out[sid] = a
        return out

    def status(self) -> dict:
        """Since when, walking measurements per sensor ("frames"), the walking points of each as solve
        counts them ("points") and its hours with walking data ("hours", a proposal needs "min_hours"),
        and seconds in which two sensors each had exactly one moving target (recounted every 30 s)."""
        now = time.monotonic()
        if self._status is None or now - self._status_t > 30:
            data = {sid: a for sid, a in self.data().items() if sid in self.config.sensor_by_id}
            frames = {sid: len(a) for sid, a in data.items()}
            points, hours = {}, {}
            for sid, a in data.items():
                t = walking_times(self.config, sid, a)
                points[sid] = len(t)
                per_hour = collections.Counter((t // STRETCH).astype(int).tolist())
                hours[sid] = sum(n >= MIN_POINTS for n in per_hour.values())
            bins = {sid: np.unique(np.floor(a[a[:, SINGLE] > 0, T] / EVERY)) for sid, a in data.items()}
            pairs = {}
            for a, b in itertools.combinations(sorted(bins), 2):
                n = len(np.intersect1d(bins[a], bins[b], assume_unique=True))
                if n:
                    pairs[f"{a}|{b}"] = n
            self._status, self._status_t = {"since": self.since, "frames": frames, "points": points, "hours": hours,
                                            "min_hours": MIN_STRETCHES, "pairs": pairs}, now
        return self._status

    def solve(self, check_mirror: bool = False) -> dict:
        return solve(self.config, self.data(), check_mirror)
