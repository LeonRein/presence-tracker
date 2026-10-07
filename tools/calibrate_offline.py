"""Calibrate the LD2450s from everyday recordings, also sensors that hardly overlap with another.

usage: python tools/calibrate_offline.py --config FILE [--recordings DIR] [--from T] [--to T]
                                         [--fit ID,...] [--positions] [--no-pairs] [--no-plan]
                                         [--bootstrap N] [--profile ID] [--mirrors] [--out FILE]

The app's calibration (calibration.py) needs one person walking alone through areas that two
sensors see at once. A sensor whose view hardly overlaps another one (a kitchen behind a door, a
study that sees the hall only through its door) gets few pairs, or pairs on one line, and its
heading or mirror stays open. Here two kinds of evidence are combined in one likelihood
(Rahimi, Dunagan & Darrell 2004: calibration and the people's paths together; Mohedano, Cavallaro &
Garcia 2014: camera poses from trajectories and a map of the walkable area):

(a) Pairs: a person walking, measured by two sensors at the same time (one moving target each,
    interpolated to the other's frame times), must be at the same place. Gaussian error from the
    measurement model (MODEL.md 4.1), plus a uniform part for wrong pairs (second person, echo):
    the mixture of Myronenko & Song 2010 (coherent point drift), fitted by EM.
(b) Floor plan: a walking person is where the sensor can see him directly: in a room, in its line
    of sight (through door openings, not through walls). Per LD2450 track (MODEL.md 2: every track
    has an owner, a person or an echo) a mixture of "person": every point uniform on the visible
    free space F, blurred with the measurement error (the likelihood field of Thrun, Burgard & Fox
    2005, 6.4, for free space instead of obstacles), and "echo": uniform on the view out to 8 m.
    The share of echo tracks is fitted per sensor (EM). Per track, not per point: a few points of
    an echo track that a turn would bring into the room don't pull the heading. This judges
    heading and position (rotation and shift keep areas), not the scale: how far people come up
    to the walls is unknown (furniture), and an echo at 1.5 times the distance looks like a
    person at 2/3 the scale. A density uniform on F would set the scale by pushing the points
    out to the walls (kitchen: 1.55), one without its Jacobian by pulling the echoes in (0.53).
(c) The scales of the LD2450s: ln scale ~ N(mu, tau^2) for every sensor, mu and tau from the
    sensors that have a ruler (at least MIN_RULER agreeing pairs), refitted with them
    (hierarchical, empirical Bayes). A sensor without pairs gets the scale of the others.

Parameters per fitted sensor: heading and scale; the drawn positions are the ruler of the whole
(as in the app), the sensors not fitted stay as they are. --positions adds a shift of the drawn
position (prior +-0.3 m per axis); it ignores that a sensor hangs on a wall, so it is a diagnosis,
not a result. Heading prior +-10 deg around the drawn one (assumed). Fitted by EM with
Gauss-Newton steps (walls as one-sided springs to the nearest visible free point, as
point-to-plane ICP). Uncertainty: Laplace (Gauss-Newton Hessian) and block bootstrap over 2-min
blocks (the errors of one LD2450 track are correlated over tens of seconds, MODEL.md 4.1); both
are printed.

--mirrors also fits each sensor with its x axis mirrored (from several headings) and prints the
log posterior of both. The floor plan alone may not decide it (a narrow room); sensors in the same
housing mounted the same way share the mirror. --profile ID: that sensor's scale held at values
from 0.7 to 1.3, the rest refitted. --out: a copy of the config with the fitted values (a private
file; never the live config).
"""

import argparse
import collections
import copy
import itertools
import json
import math
import os
import sys
import time
from types import SimpleNamespace

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))
MIN_SPEED = 0.05  # m/s raw radial speed: walking (as calibration.py)
MIN_RANGE = 0.5  # m on the floor: closer, the slant correction decides everything
MAX_GAP = 0.25  # s, interpolate pairs only between frames this close (as calibration.py)
EVERY = 0.25  # s, at most one pair per this time (MODEL.md 4.1: one position per 0.25 s)
PLAN_EVERY = 1.0  # s, one walking point per LD2450 track and second for the floor plan (its
                  # error wanders with 3.5 s, MODEL.md 4.1; denser points only repeat it)
CELL = 0.05  # m, grid of the free-space field
VIEW = 8.0  # m, reflections: uniform on the view out to here
PAIR_OUTLIER_AREA = 30.0  # m^2, wrong pairs: residual uniform on this area
PRIOR = {"heading": math.radians(10.0), "lnscale": 0.15, "shift": 0.3}
BLOCK = 120.0  # s, bootstrap blocks
MIN_RULER = 100  # agreeing pairs for a sensor's scale to count for the others' prior


def parse_time(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


# ------------------------------------------------------------------------------------ data

def load(config, recordings: str, t0: float, t1: float) -> dict:
    """Measured LD2450 targets per sensor from the recordings: t, track, raw x, y (m), radial speed
    (m/s), whether the target was the only moving one of its frame."""
    from presence_tracker.frames import SensorClock
    from presence_tracker.sensortracks import SensorTracks

    ids = itertools.count()
    tracks, clocks = {}, collections.defaultdict(SensorClock)
    rows = collections.defaultdict(list)
    t = t0 - t0 % 3600
    while t <= t1:
        path = os.path.join(recordings, time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl")
        t += 3600
        if not os.path.exists(path):
            continue
        for line in open(path):
            m = json.loads(line)
            if not m["topic"].endswith("/frame") or not t0 <= m["t"] <= t1:
                continue
            sid = m["topic"].split("/")[1]
            if sid not in config.sensor_by_id:
                continue
            p = m["payload"]
            tt = clocks[sid](m["t"], p.get("uptime_ms"))
            dets = [SimpleNamespace(slot=tg.get("slot", 0), local=(tg["x"] / 1000, tg["y"] / 1000), hidden=False)
                    for tg in p.get("targets", []) if tg["y"] > 0]
            ev = tracks.setdefault(sid, SensorTracks(sid, ids)).update(tt, p, dets)
            speed = {tg.get("slot", 0): tg.get("speed", 0) / 1000 for tg in p.get("targets", [])}
            meas = [(seg, d, speed.get(d.slot, 0.0)) for seg, d in ev.born + ev.measured]
            moving = sum(abs(v) >= MIN_SPEED for _, _, v in meas)
            for seg, d, v in meas:
                rows[sid].append((tt, seg, d.local[0], d.local[1], v, moving == 1 and abs(v) >= MIN_SPEED))
    return {sid: np.array(r) for sid, r in rows.items()}


def thin(t: np.ndarray, key: np.ndarray, every: float) -> np.ndarray:
    """Indices keeping at most one entry per `every` seconds per key."""
    keep, last = [], {}
    for i in np.argsort(t, kind="stable"):
        k = key[i]
        if t[i] - last.get(k, -1e18) >= every:
            keep.append(i)
            last[k] = t[i]
    return np.array(sorted(keep), dtype=int)


# ------------------------------------------------------------------------------ geometry

def ground(s, lx, ly, mirror: bool, target_height: float) -> np.ndarray:
    """Raw LD2450 meters -> floor point in the sensor frame as complex x + iy (as calibration.py)."""
    x = -lx if mirror else lx
    slant = np.hypot(x, ly)
    dh = s.height - target_height
    g = np.sqrt(np.maximum(slant**2 - dh**2, 0.01))
    k = g / np.maximum(slant, 1e-6)
    return x * k + 1j * ly * k


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


class FreeSpace:
    """Where a sensor can see a person directly: in a room that is not a closed room without a
    sensor, in its line of sight. On a grid: for every cell outside, the nearest free point."""

    def __init__(self, config, s, origin):
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
        self.area = free.sum() * CELL * CELL
        # nearest free cell for every cell: brute force against the free cells on the edge
        edge = free & ~(np.roll(free, 1, 0) & np.roll(free, -1, 0) & np.roll(free, 1, 1) & np.roll(free, -1, 1))
        ec = (px[edge] + 1j * py[edge])
        cells = px + 1j * py
        near = cells.copy()
        out = np.flatnonzero(~free.ravel())
        flat = cells.ravel()
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


# --------------------------------------------------------------------------------- model

class Problem:
    """The data of one fit: walking points per sensor (for the floor plan) and pairs."""

    def __init__(self, config, data, fit, positions, mirrors=None):
        self.config = config
        self.fit = list(fit)
        self.positions = positions
        self.mirror = {sid: (config.sensor_by_id[sid].mirror if mirrors is None or sid not in mirrors else mirrors[sid])
                       for sid in config.sensor_by_id}
        self.npar = 4 if positions else 2
        self.th = config.params.target_height
        self.data = data
        self.points, self.pairs = {}, []
        for sid, a in data.items():
            if not len(a):
                continue
            s = config.sensor_by_id[sid]
            t, seg, lx, ly, v, single = a.T
            z = ground(s, lx, ly, self.mirror[sid], self.th)
            walk = (np.abs(v) >= MIN_SPEED) & (np.abs(z) >= MIN_RANGE)
            idx = np.flatnonzero(walk)
            idx = idx[thin(t[idx], seg[idx], PLAN_EVERY)]
            _, track = np.unique(seg[idx], return_inverse=True)
            self.points[sid] = SimpleNamespace(t=t[idx], z=z[idx], sigma=0.125 + 0.035 * np.abs(z[idx]),
                                               track=track, ntracks=int(track.max()) + 1 if len(idx) else 0)
        for a_id, b_id in itertools.combinations(sorted(self.points), 2):
            pa, pb = self._pairs(a_id, b_id)
            if pa is not None:
                self.pairs.append(SimpleNamespace(a=a_id, b=b_id, **pa, **pb))
        self.fields = {sid: FreeSpace(config, config.sensor_by_id[sid], config.sensor_by_id[sid].sight_origin())
                       for sid in self.points}
        self.views = {sid: math.radians(config.sensor_by_id[sid].fov) / 2 * VIEW**2 for sid in self.points}

    def _pairs(self, a_id, b_id):
        """One moving target in both sensors at once: a interpolated to b's frame times."""
        sa, sb = self.config.sensor_by_id[a_id], self.config.sensor_by_id[b_id]
        A, B = self.data[a_id], self.data[b_id]
        A, B = A[A[:, 5] > 0], B[B[:, 5] > 0]
        if len(A) < 2 or not len(B):
            return None, None
        k = np.clip(np.searchsorted(A[:, 0], B[:, 0]), 1, len(A) - 1)
        t0, t1 = A[k - 1, 0], A[k, 0]
        ok = (B[:, 0] > t0) & (B[:, 0] <= t1) & (t1 - t0 <= MAX_GAP) & (A[k - 1, 1] == A[k, 1])
        if ok.sum() < 3:
            return None, None
        w = ((B[:, 0] - t0) / np.maximum(t1 - t0, 1e-6))[ok]
        za0 = ground(sa, A[k - 1, 2], A[k - 1, 3], self.mirror[a_id], self.th)[ok]
        za1 = ground(sa, A[k, 2], A[k, 3], self.mirror[a_id], self.th)[ok]
        za = za0 * (1 - w) + za1 * w
        zb = ground(sb, B[:, 2], B[:, 3], self.mirror[b_id], self.th)[ok]
        tb = B[ok, 0]
        keep = thin(tb, np.zeros(len(tb)), EVERY)
        keep = keep[(np.abs(za[keep]) >= MIN_RANGE) & (np.abs(zb[keep]) >= MIN_RANGE)]
        if len(keep) < 3:
            return None, None
        za, zb, tb = za[keep], zb[keep], tb[keep]
        sigma = np.hypot(0.125 + 0.035 * np.abs(za), 0.125 + 0.035 * np.abs(zb))
        return {"za": za, "t": tb}, {"zb": zb, "sigma": sigma}

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
                p += complex(q[2], q[3])
        if scale is not None:
            k = scale
        return k * complex(math.cos(h), math.sin(h)), p

    def world(self, phi, sid, z, scale=None):
        c, p = self.pose(phi, sid, scale)
        return p + c * z

    def lnscale(self, phi, sid):
        return phi[self.npar * self.fit.index(sid) + 1]

    def prior_residuals(self, phi, mu=0.0, tau=PRIOR["lnscale"]):
        """Heading and shift around the drawn ones; ln scale ~ N(mu, tau^2), the scales of the
        LD2450s (hierarchical, mu and tau from the sensors with pairs)."""
        r = []
        for i, sid in enumerate(self.fit):
            q = phi[self.npar * i:][:self.npar]
            r += [q[0] / PRIOR["heading"], (q[1] - mu) / tau]
            if self.positions:
                r += [q[2] / PRIOR["shift"], q[3] / PRIOR["shift"]]
        return np.array(r)

    def start(self):
        phi = []
        for sid in self.fit:
            phi += [0.0, math.log(self.config.sensor_by_id[sid].scale)] + ([0.0, 0.0] if self.positions else [])
        return np.array(phi)


class Fit:
    """EM for the mixtures, Gauss-Newton for the poses."""

    def __init__(self, prob: Problem, use_pairs=True, use_plan=True, weights=None, hold=None):
        self.p = prob
        self.use_pairs, self.use_plan = use_pairs, use_plan
        self.hold = hold or {}  # sensor -> scale held fixed (profile)
        self.mu, self.tau = 0.0, PRIOR["lnscale"]
        self.kref = {}  # the scales the floor plan is judged with (see run)
        # bootstrap weights per point / pair (multiplicity of its block)
        self.wp = {sid: np.ones(len(pt.z)) for sid, pt in prob.points.items()}
        self.wq = [np.ones(len(q.za)) for q in prob.pairs]
        if weights is not None:
            for sid, pt in prob.points.items():
                self.wp[sid] = weights(pt.t)
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
                wrong = eps / PAIR_OUTLIER_AREA
                total += float((self.wq[i] * np.log(person + wrong)).sum())
                resp[("pair", i)] = person / (person + wrong)
        total -= 0.5 * float((self.p.prior_residuals(phi, self.mu, self.tau) ** 2).sum())
        total -= 0.5 * sum(((self.p.lnscale(phi, sid) - math.log(k)) / 1e-4) ** 2 for sid, k in self.hold.items())
        return (total, resp) if details else total

    def residuals(self, phi, resp, anchors):
        out = [self.p.prior_residuals(phi, self.mu, self.tau)]
        out.append(np.array([(self.p.lnscale(phi, sid) - math.log(k)) / 1e-4 for sid, k in self.hold.items()]))
        if self.use_plan:
            for sid, pt in self.p.points.items():
                w = self.p.world(phi, sid, pt.z, self.kref.get(sid))
                b, n = anchors[sid]
                # one-sided spring to the nearest free point, along the normal of that edge
                dist = ((w - b) * np.conj(n)).real
                g = np.sqrt(resp[("plan", sid)] * self.wp[sid])
                out.append(g * np.maximum(dist, 0.0) / pt.sigma)
        if self.use_pairs:
            for i, q in enumerate(self.p.pairs):
                r = (self.p.world(phi, q.a, q.za) - self.p.world(phi, q.b, q.zb)) / q.sigma
                g = np.sqrt(resp[("pair", i)] * self.wq[i])
                out += [g * r.real, g * r.imag]
        return np.concatenate(out)

    def hyper(self, phi, resp):
        """mu, tau of the LD2450 scales from the sensors measured against another one (rulers):
        at least MIN_RULER agreeing pairs."""
        agree = collections.Counter()
        if self.use_pairs:
            for i, q in enumerate(self.p.pairs):
                n = float((resp[("pair", i)] * self.wq[i]).sum())
                agree[q.a] += n
                agree[q.b] += n
        self.rulers = [sid for sid in self.p.points if agree[sid] >= MIN_RULER]
        lk = [self.p.lnscale(phi, sid) if sid in self.p.fit else math.log(self.p.config.sensor_by_id[sid].scale)
              for sid in self.rulers]
        if len(lk) >= 2:
            self.mu, self.tau = float(np.mean(lk)), max(float(np.std(lk, ddof=1)), 0.03)

    def run(self, phi=None, iterations=60, tol=1e-6, verbose=False):
        phi = self.p.start() if phi is None else phi.copy()
        last = -math.inf
        for it in range(iterations):
            # The floor plan judges heading and position at the scale of now, it doesn't set the
            # scale: rotation and shift keep areas, so "uniform on the visible space" is a proper
            # density for them; for the scale it is not (people don't come up to the walls, and
            # an echo 1.5 times as far looks like a person at 2/3 the scale). The scale comes from
            # pairs and the scales of the other LD2450s.
            self.kref = {sid: abs(self.p.pose(phi, sid)[0]) for sid in self.p.points}
            ll, resp = self.loglik(phi, details=True)
            self.hyper(phi, resp)
            # M step for the mixture weights
            if self.use_plan:
                for sid, pt in self.p.points.items():
                    wt = np.bincount(pt.track, self.wp[sid], pt.ntracks) / np.maximum(np.bincount(pt.track, None, pt.ntracks), 1)
                    self.eps_plan[sid] = float(np.clip(1 - (resp[("track", sid)] * wt).sum() / wt.sum(), 0.01, 0.95))
            if self.use_pairs:
                for i in range(len(self.p.pairs)):
                    self.eps_pair[i] = float(np.clip(1 - (resp[("pair", i)] * self.wq[i]).sum() / self.wq[i].sum(), 0.01, 0.99))
            ll, resp = self.loglik(phi, details=True)
            # Gauss-Newton step with the anchors (nearest free point, normal) of now
            anchors = {}
            if self.use_plan:
                for sid in self.p.points:
                    w, b, d = self._plan_terms(phi, sid)
                    n = np.where(d > 1e-9, (w - b) / np.maximum(d, 1e-9), 0)
                    anchors[sid] = (b, n)
            r0 = self.residuals(phi, resp, anchors)
            J = np.empty((len(r0), len(phi)))
            for j in range(len(phi)):
                h = 1e-5
                e = np.zeros(len(phi))
                e[j] = h
                J[:, j] = (self.residuals(phi + e, resp, anchors) - self.residuals(phi - e, resp, anchors)) / (2 * h)
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
            if verbose:
                print(f"  it {it}: log post {ll:.1f} step {np.abs(a * step).max():.2e}")
            if abs(ll - last) < tol * max(1.0, abs(ll)) and np.abs(a * step).max() < 1e-5:
                break
            last = ll
        self.phi = phi
        self.ll = self.loglik(phi)
        # Laplace: covariance from the Gauss-Newton Hessian at the end (errors taken as
        # independent: too narrow where the points of one track share their offset)
        self.cov = np.linalg.inv(H + 1e-9 * np.eye(len(phi)))
        return phi


# ------------------------------------------------------------------------------- report

def describe(prob, phi, sid):
    c, p = prob.pose(phi, sid)
    return {"heading": round((math.degrees(math.atan2(c.imag, c.real)) + 90) % 360, 2), "scale": round(abs(c), 4),
            "x": round(p.real, 3), "y": round(p.imag, 3), "mirror": prob.mirror[sid]}


def checks(prob, phi, eps_plan=None):
    """Per sensor: share of walking points outside what it can see; per pair: mean difference of
    the agreeing pairs (within 1 m) and their median distance."""
    out = {"outside": {}, "pairs": {}}
    for sid, pt in prob.points.items():
        _, d = prob.fields[sid].lookup(prob.world(phi, sid, pt.z))
        out["outside"][sid] = (float((d > 0.3).mean()), float((d > 1.0).mean()))
    for q in prob.pairs:
        r = prob.world(phi, q.a, q.za) - prob.world(phi, q.b, q.zb)
        good = np.abs(r) < 1.0
        out["pairs"][f"{q.a}|{q.b}"] = (len(r), int(good.sum()), complex(r[good].mean()) if good.any() else 0j,
                                        float(np.median(np.abs(r[good]))) if good.any() else float("nan"))
    return out


def short(sid):
    return sid.replace("presence-", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--recordings", default=os.path.join(TOOLS, "..", "recordings"))
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "tracker"))
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--fit", help="sensor ids (without presence-) to fit, default all placed")
    ap.add_argument("--positions", action="store_true", help="also fit a shift of the drawn positions")
    ap.add_argument("--no-pairs", action="store_true")
    ap.add_argument("--no-plan", action="store_true")
    ap.add_argument("--bootstrap", type=int, default=0)
    ap.add_argument("--profile")
    ap.add_argument("--mirrors", action="store_true")
    ap.add_argument("--cache", help="npz file for the extracted targets")
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(a.config)))
    placed = [s.id for s in config.sensors if s.placed and s.enabled]
    if a.start:
        t0 = parse_time(a.start)
    else:
        first = min(f for f in os.listdir(a.recordings) if f.endswith(".jsonl"))
        t0 = time.mktime(time.strptime(first[:11], "%Y%m%d-%H"))
    t1 = parse_time(a.end) if a.end else time.time()
    if a.cache and os.path.exists(a.cache):
        data = dict(np.load(a.cache))
    else:
        data = load(config, a.recordings, t0, t1)
        if a.cache:
            np.savez(a.cache, **data)
    data = {sid: d[(d[:, 0] >= t0) & (d[:, 0] <= t1)] for sid, d in data.items() if sid in placed}
    full = lambda x: x if x.startswith("presence-") else "presence-" + x
    fit = [full(x) for x in a.fit.split(",")] if a.fit else placed

    prob = Problem(config, data, fit, a.positions)
    print("walking points:", {short(k): len(v.z) for k, v in prob.points.items()},
          "pairs:", {f"{short(q.a)}|{short(q.b)}": len(q.za) for q in prob.pairs})
    F = Fit(prob, not a.no_pairs, not a.no_plan)
    phi0 = prob.start()
    phi = F.run()
    print(f"log posterior {F.ll:.1f} (drawn {F.loglik(phi0):.1f})")
    print("echo tracks (share of walking tracks):", {short(k): round(v, 2) for k, v in F.eps_plan.items()})
    print(f"LD2450 scales: ln scale ~ N({F.mu:.3f}, {F.tau:.3f}^2) from", [short(x) for x in F.rulers])
    print("wrong pairs:", {f"{short(q.a)}|{short(q.b)}": round(F.eps_pair[i], 2) for i, q in enumerate(prob.pairs)})

    boot = collections.defaultdict(list)
    if a.bootstrap:
        rng = np.random.default_rng(1)
        tmin = min(pt.t.min() for pt in prob.points.values())
        nblocks = int(max(pt.t.max() for pt in prob.points.values()) - tmin) // int(BLOCK) + 1
        for _ in range(a.bootstrap):
            counts = np.bincount(rng.integers(0, nblocks, nblocks), minlength=nblocks).astype(float)
            weights = lambda t: counts[((t - tmin) // BLOCK).astype(int)]
            Fb = Fit(prob, not a.no_pairs, not a.no_plan, weights=weights)
            pb = Fb.run(phi)
            for sid in fit:
                boot[sid].append(describe(prob, pb, sid))

    print(f"\n{'sensor':14s} {'heading':>13s} {'sd':>9s} {'scale':>13s} {'sd':>11s} {'x':>11s} {'y':>11s}"
          "   drawn -> fitted, sd: Laplace / block bootstrap")
    result = {}
    for sid in fit:
        s = config.sensor_by_id[sid]
        r = describe(prob, phi, sid)
        i = prob.npar * fit.index(sid)
        lap = {"heading": math.degrees(math.sqrt(F.cov[i, i])), "scale": r["scale"] * math.sqrt(F.cov[i + 1, i + 1])}
        if a.positions:
            lap.update(x=math.sqrt(F.cov[i + 2, i + 2]), y=math.sqrt(F.cov[i + 3, i + 3]))
        sd = {k: float(np.std([b[k] if k != "heading" else (b[k] - r[k] + 180) % 360 - 180 for b in boot[sid]]))
              for k in ("heading", "scale", "x", "y")} if boot[sid] else {}
        r["sd"] = {k: round(max(v, sd.get(k, 0.0)), 4) for k, v in lap.items()}
        result[sid] = r
        bs = lambda k, nd: f"/{sd[k]:.{nd}f}" if sd else ""
        print(f"{short(sid):14s} {s.heading:5.1f}->{r['heading']:5.1f} {lap['heading']:4.1f}{bs('heading', 1):5s} "
              f"{s.scale:.3f}->{r['scale']:.3f} {lap['scale']:.3f}{bs('scale', 3):6s} "
              f"{s.x:5.2f}->{r['x']:5.2f} {s.y:5.2f}->{r['y']:5.2f}")

    before, after = checks(prob, phi0), checks(prob, phi)
    print("\nwalking points out of sight (> 0.3 m / > 1 m from what the sensor can see), drawn -> fitted:")
    for sid in prob.points:
        (b3, b1), (a3, a1) = before["outside"][sid], after["outside"][sid]
        print(f"  {short(sid):14s} {100 * b3:4.0f} % / {100 * b1:3.0f} %  ->  {100 * a3:4.0f} % / {100 * a1:3.0f} %")
    print("pairs within 1 m: count, mean difference (a - b), median distance; drawn -> fitted:")
    for k in before["pairs"]:
        (n, g0, m0, d0), (_, g1, m1, d1) = before["pairs"][k], after["pairs"][k]
        a_, b_ = k.split("|")
        print(f"  {short(a_) + '|' + short(b_):26s} n={n:5d}  {g0:5d} ({m0.real:5.2f},{m0.imag:5.2f}) {d0:.2f}"
              f"  ->  {g1:5d} ({m1.real:5.2f},{m1.imag:5.2f}) {d1:.2f}")

    if a.mirrors:
        print("\nmirrored x axis, best of several start headings (log posterior; higher is better):")
        for sid in fit:
            flipped = {sid: not prob.mirror[sid]}
            pm = Problem(config, data, fit, a.positions, mirrors=flipped)
            best = None
            for dh in (-60, -40, -20, 0, 20, 40, 60):
                Fm = Fit(pm, not a.no_pairs, not a.no_plan)
                st = pm.start()
                st[pm.npar * fit.index(sid)] = math.radians(dh)
                Fm.run(st)
                if best is None or Fm.ll > best[0]:
                    best = (Fm.ll, describe(pm, Fm.phi, sid))
            print(f"  {short(sid):14s} as fitted {F.ll:.1f}; mirrored {best[0]:.1f} (heading {best[1]['heading']}, scale {best[1]['scale']})")

    if a.profile:
        sid = full(a.profile)
        print(f"\nprofile of the scale of {short(sid)}, the rest refitted: log posterior relative to the fit "
              f"(pairs and prior), the floor plan's log likelihood at that scale, walking points out of sight")
        for k in np.arange(0.7, 1.31, 0.05):
            Fp = Fit(prob, not a.no_pairs, not a.no_plan, hold={sid: float(k)})
            st = phi.copy()
            st[prob.npar * fit.index(sid) + 1] = math.log(k)
            Fp.run(st)
            Fp.hold = {}
            Fp.kref = {x: abs(prob.pose(Fp.phi, x)[0]) for x in prob.points}
            ll = Fp.loglik(Fp.phi)
            pt = prob.points[sid]
            _, _, d = Fp._plan_terms(Fp.phi, sid)
            F_, eps = prob.fields[sid].area, Fp.eps_plan[sid]
            plan = float(np.log((1 - eps) * np.exp(-0.5 * (d / pt.sigma) ** 2) / F_ + eps / prob.views[sid]).sum())
            print(f"  {k:.2f}: {ll - F.ll:8.1f}   plan {plan:9.1f}   out of sight {100 * (d > 0.3).mean():3.0f} %"
                  f"   heading {describe(prob, Fp.phi, sid)['heading']}")

    if a.out:
        out = copy.deepcopy(json.load(open(a.config)))
        for sd in out["sensors"]:
            if sd["id"] in result:
                r = result[sd["id"]]
                sd.update(heading=r["heading"], scale=r["scale"], x=r["x"], y=r["y"], mirror=r["mirror"])
        json.dump(out, open(a.out, "w"), indent=1, ensure_ascii=False)
        print(f"\nwritten {a.out}")


if __name__ == "__main__":
    main()
