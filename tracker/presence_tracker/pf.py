"""Joint particle filter over the people who live here (experimental, step 4).

Instead of tracks that are born, confirmed, lost, taken over and ended, the state is the
people themselves: `residents` persons, each always somewhere -

  in the observed area   position, velocity, walking or still, and whether the LD2450 currently
                         reports them ("visible") or has dropped them ("hidden")
  in a region            behind a door without a sensor (kitchen, balcony, the hallway region,
                         outside), with the time since they went in

A particle is one joint guess for all of them; the weights follow the measurements. Nobody can
appear or vanish, so there are no duplicates and no ghosts that become people by construction;
a ghost has to be explained as clutter or by a person who can plausibly be there.

Dynamics (per person, all from measured or learned distributions):
  - still -> walking by the get-up hazard (decreasing with the time sat), walking -> still
  - walking: velocity random walk, stopped by walls; standing still: small jitter
  - a walker at a door goes through it; a visit ends by the learned stay durations (Dwell) and
    the person comes back out walking at the door
  - visible -> hidden (LD2450 dropout) at a rate per mode, hidden -> visible by the learned
    re-detection survival (heavy-tailed: sitters stay dropped for minutes)

Measurements of one sensor frame: every assignment of its detections to visible people in view
or to clutter (JPDA, all of them enumerated: two people, three targets at most). Detection and
miss terms are tempered like everywhere else (frame_gap / evidence_time: LD2450 frames are not
independent); the position term is not (as in the Kalman filter). LD2410C gate energies: the
learned likelihood ratio at the gate of each person in the trusted cone, once per gate group.
"""

import itertools
import math

import numpy as np

from .sensormodel import CELL, GATE, ORIGIN, PD_MAX, RES_EDGES, SIZE
from .tracker import SensorRuntime, Tracker
from .unobserved import Dwell

ROOM = 0
RES_FAR = 0.8  # LD2450: P(both | at least one) for people well apart (measured)
RES = 0.1  # m, walkable mask resolution
DOOR_REACH = 0.5  # m from the door's center: a walker there may go through
MAX_DT = 0.5  # s, longer gaps are predicted in steps
STATE = ("place", "pos", "vel", "walk", "vis", "hid_t", "place_t", "still_t", "goal")


class PersonFilter:
    def __init__(self, config, sensor_model, dwell: Dwell | None = None, n: int = 600, seed: int = 0):
        self.config = config
        self.p = config.params
        self.sm = sensor_model
        self.dwell = dwell or Dwell(config.params, [rid for rid, r in config.regions.items() if r["open"]] + ["outside"])
        self.rng = np.random.default_rng(seed)
        self.n = n
        self.k = int(self.p.residents)
        self.regions = sorted({q.region for q in config.portals})
        self.places = ["room"] + self.regions
        self.portals = [(q, 1 + self.regions.index(q.region)) for q in config.portals]
        self.helper = Tracker(config)  # detections from frames, frozen targets
        self.runtime: dict[str, SensorRuntime] = {}
        self.listeners = []
        self.t = None
        self.now = 0.0
        self._build_mask()
        self._survival_tables()
        self._grids = {}
        self._grids_t = -math.inf

    # ------------------------------------------------------------ geometry

    def _build_mask(self):
        cfg = self.config
        region_rooms = {r for reg in cfg.regions.values() for r in reg["rooms"]}
        rooms = [z for z in cfg.zones_of("room") if z.id not in region_rooms]
        self.rooms = rooms
        b = [z.geometry.bounds() for z in rooms]
        self.x0, self.y0 = min(v[0] for v in b) - 0.2, min(v[1] for v in b) - 0.2
        x1, y1 = max(v[2] for v in b) + 0.2, max(v[3] for v in b) + 0.2
        self.nx, self.ny = int((x1 - self.x0) / RES) + 1, int((y1 - self.y0) / RES) + 1
        cx = self.x0 + (np.arange(self.nx) + 0.5) * RES
        cy = self.y0 + (np.arange(self.ny) + 0.5) * RES
        X, Y = np.meshgrid(cx, cy, indexing="ij")
        inside = np.zeros(X.shape, dtype=bool)
        for i in range(self.nx):
            for j in range(self.ny):
                inside[i, j] = any(z.contains(X[i, j], Y[i, j]) for z in rooms)
        near_wall = np.zeros(X.shape, dtype=bool)
        for (ax, ay), (bx, by) in cfg.wall_segments:
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            tt = np.clip(((X - ax) * dx + (Y - ay) * dy) / max(l2, 1e-9), 0, 1)
            near_wall |= np.hypot(X - ax - tt * dx, Y - ay - tt * dy) < 0.15
        self.mask = inside & ~near_wall
        self.cells = np.argwhere(self.mask)

    def _walkable(self, pos):
        i = ((pos[..., 0] - self.x0) / RES).astype(int)
        j = ((pos[..., 1] - self.y0) / RES).astype(int)
        ok = (i >= 0) & (i < self.nx) & (j >= 0) & (j < self.ny)
        out = np.zeros(pos.shape[:-1], dtype=bool)
        out[ok] = self.mask[i[ok], j[ok]]
        return out

    def _random_positions(self, shape):
        idx = self.rng.integers(len(self.cells), size=shape)
        c = self.cells[idx]
        return np.stack([self.x0 + (c[..., 0] + self.rng.random(shape)) * RES,
                         self.y0 + (c[..., 1] + self.rng.random(shape)) * RES], axis=-1)

    # ------------------------------------------------- learned distributions

    def _survival_tables(self):
        """Re-detection survival per mode on a grid of hidden times (after the first 1.5 s)."""
        self.tau = np.concatenate([np.arange(0, 60, 0.25), np.arange(60, 3600, 5.0)])
        self.surv = {}
        for mode in ("still", "walking"):
            s0 = self.sm.redetection_survival(mode, 1.5)
            self.surv[mode] = np.array([self.sm.redetection_survival(mode, 1.5 + t) / s0 for t in self.tau])

    def _resolution(self, d):
        edges = np.array(RES_EDGES[:-1])
        table = np.array([self.sm.resolution(e - 0.01) for e in RES_EDGES[:-1]] + [self.sm.resolution(10.0)])
        return table[np.searchsorted(edges, d, side="right")]

    def _hazard_hidden(self, mode, age, dt):
        s = self.surv[mode]
        a = np.interp(age, self.tau, s)
        b = np.interp(age + dt, self.tau, s)
        return np.clip(1 - b / np.maximum(a, 1e-12), 0, 1)

    def _dwell_hazard(self, region, age, dt):
        f = np.vectorize(lambda a: self.dwell.hazard(region, float(a), dt), otypes=[float])
        return f(age) if age.size else age

    def _refresh_grids(self, t):
        """P_D per sensor on the sensor model's grid (cached, the learning is slow)."""
        if t - self._grids_t < 60:
            return
        self._grids_t = t
        for s in self.config.sensors:
            prior = self.sm.prior.get(s.id)
            if prior is None:
                continue
            trials = self.sm.trials.get(s.id, np.zeros_like(prior))
            hits = self.sm.hits.get(s.id, np.zeros_like(prior))
            self._grids[s.id] = np.where(prior > 0, (hits + 20 * prior) / (trials + 20), 0.0)
        self._place_prior()
        self._unseen_maps()

    def _place_prior(self):
        """Where people stay: the frames the sensor model counted a person there for sure (all
        sensors), smoothed, with a floor; relative to the mean over the walkable area. People who
        stop walking stop where people usually are, not in a blind corner under a sensor."""
        total = sum(self.sm.trials.values()) if self.sm.trials else np.zeros((SIZE, SIZE))
        k = np.ones((3, 3)) / 9
        sm = np.zeros_like(total)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                sm += np.roll(np.roll(total, di, 0), dj, 1) * k[di + 1, dj + 1]
        cx = self.x0 + (self.cells[:, 0] + 0.5) * RES
        cy = self.y0 + (self.cells[:, 1] + 0.5) * RES
        i = ((cx - ORIGIN[0]) / CELL).astype(int)
        j = ((cy - ORIGIN[1]) / CELL).astype(int)
        mean = sm[i, j].mean() if len(cx) else 0.0
        if mean <= 0:  # nothing learned yet: everywhere alike
            self._prior_grid = np.ones_like(sm)
            return
        self._prior_grid = (sm + self.p.pf_place_floor * mean) / (mean * (1 + self.p.pf_place_floor))

    def _unseen_maps(self):
        """Per door, on the walkable grid: probability that someone walking from that cell
        through the door is detected by no sensor on the way (all PATH steps at walking speed,
        tempered by evidence_time) - the same quantity as the tracker's _unseen_way."""
        p = self.p
        cx = self.x0 + (self.cells[:, 0] + 0.5) * RES
        cy = self.y0 + (self.cells[:, 1] + 0.5) * RES
        start = np.stack([cx, cy], axis=-1)
        self._unseen = []
        for q, _ in self.portals:
            logu = np.zeros(len(cx))
            a = start
            for b in (np.array(q.watch, dtype=float), np.array(q.center, dtype=float)):
                length = np.linalg.norm(b - a, axis=-1)
                steps = 12
                for k in range(steps):
                    pt = a + (b - a) * ((k + 0.5) / steps)
                    miss = np.ones(len(cx))
                    for s in self.config.sensors:
                        if s.id in self._grids:
                            miss *= 1 - np.clip(self._pd(s.id, pt), 0, PD_MAX)
                    logu += np.log(np.maximum(miss, 1e-3)) * (length / steps / p.walk_speed) / p.evidence_time
                a = np.broadcast_to(b, a.shape)
            grid = np.zeros((self.nx, self.ny))
            grid[self.cells[:, 0], self.cells[:, 1]] = np.exp(logu)
            self._unseen.append(grid)

    def _unseen_at(self, gi, pos):
        i = ((pos[..., 0] - self.x0) / RES).astype(int).clip(0, self.nx - 1)
        j = ((pos[..., 1] - self.y0) / RES).astype(int).clip(0, self.ny - 1)
        return self._unseen[gi][i, j]

    def _log_place(self, pos):
        i = ((pos[..., 0] - ORIGIN[0]) / CELL).astype(int).clip(0, SIZE - 1)
        j = ((pos[..., 1] - ORIGIN[1]) / CELL).astype(int).clip(0, SIZE - 1)
        return np.log(np.maximum(self._prior_grid[i, j], 1e-6))

    def _pd(self, sid, pos):
        grid = self._grids.get(sid)
        if grid is None:
            return np.zeros(pos.shape[:-1])
        i = ((pos[..., 0] - ORIGIN[0]) / CELL).astype(int).clip(0, SIZE - 1)
        j = ((pos[..., 1] - ORIGIN[1]) / CELL).astype(int).clip(0, SIZE - 1)
        return grid[i, j]

    # ------------------------------------------------------------- particles

    def _init(self, t):
        n, k = self.n, self.k
        self.place = np.where(self.rng.random((n, k)) < 0.6, ROOM,
                              self.rng.integers(1, len(self.places), size=(n, k))).astype(np.int16)
        self.pos = self._random_positions((n, k))
        self.vel = np.zeros((n, k, 2))
        self.walk = np.zeros((n, k), dtype=bool)
        self.vis = self.rng.random((n, k)) < 0.5
        self.hid_t = np.zeros((n, k))
        self.place_t = self.rng.random((n, k)) * 300
        self.still_t = self.rng.random((n, k)) * 300
        self.goal = np.full((n, k), -1, dtype=np.int16)  # walking to portal index, -1: somewhere in the room
        self.logw = np.zeros(n)
        self.t = t

    def _predict(self, t):
        while self.t < t:
            dt = min(t - self.t, MAX_DT)
            self._move(dt)
            self.t += dt

    def _move(self, dt):
        p, rng = self.p, self.rng
        room = self.place == ROOM
        # walking or not: get up by the measured hazard (falls with the time sat), stop
        h_up = p.pf_getup_share / (self.still_t + p.pf_getup_time)
        start = room & ~self.walk & (rng.random(room.shape) < 1 - np.exp(-h_up * dt))
        # on the way to a door people rarely stop (changed their mind), otherwise after a few steps
        stop_rate = np.where(self.goal >= 0, p.pf_goal_stop_rate, p.pf_stop_rate)
        stop = room & self.walk & (rng.random(room.shape) < 1 - np.exp(-stop_rate * dt))
        if start.any():
            # where to: one of the doors (pf_door_goal) or somewhere in the room
            m = int(start.sum())
            to_door = rng.random(m) < p.pf_door_goal
            goal = np.where(to_door, rng.integers(len(self.portals), size=m), -1) if self.portals else np.full(m, -1)
            self.goal[start] = goal
            ang = rng.random(m) * 2 * math.pi
            self.vel[start] = 0.6 * np.stack([np.cos(ang), np.sin(ang)], axis=-1)
            # the LD2450 drops people who keep still; whoever gets up moves and is a target again
            self.vis[start] = True
            self.hid_t[start] = 0.0
        if stop.any():
            self.logw += np.where(stop, self._log_place(self.pos), 0.0).sum(axis=1)
        self.walk = (self.walk | start) & ~stop
        self.vel[stop] = 0
        self.goal[stop] = -1
        self.still_t = np.where(self.walk, 0.0, self.still_t + dt)
        # motion, walls stop people
        noise = rng.normal(size=self.pos.shape)
        w = self.walk[..., None]
        vel = self.vel + w * noise * p.walk_accel * math.sqrt(dt)
        # walkers with a door as goal steer toward it
        for gi, (q, _) in enumerate(self.portals):
            sel = self.walk & (self.goal == gi) & room
            if sel.any():
                to = np.array(q.center) - self.pos[sel]
                to /= np.maximum(np.linalg.norm(to, axis=-1, keepdims=True), 1e-6)
                vel[sel] += (to * p.walk_speed - vel[sel]) * min(2.0 * dt, 1.0)
        speed = np.linalg.norm(vel, axis=-1, keepdims=True)
        vel = np.where(speed > 1.6, vel * 1.6 / np.maximum(speed, 1e-9), vel)
        # standing or sitting: the measured wander; dropped by the LD2450 means dead still
        jitter = np.where(self.vis, p.still_jitter, p.pf_hidden_jitter)[..., None]
        step = np.where(w, vel * dt, rng.normal(size=self.pos.shape) * jitter * math.sqrt(dt))
        new = self.pos + step
        ok = self._walkable(new) & room
        self.pos = np.where(ok[..., None], new, self.pos)
        self.vel = np.where(ok[..., None], vel, 0.0)
        # through a door while walking at it
        for q, idx in self.portals:
            c = np.array(q.center)
            at = room & self.walk & (np.linalg.norm(self.pos - c, axis=-1) < DOOR_REACH)
            go = at & (rng.random(at.shape) < 1 - math.exp(-p.pf_door_rate * dt))
            if go.any():
                self.place[go] = idx
                self.place_t[go] = 0.0
                self.goal[go] = -1
                room = self.place == ROOM
        # visits end: back out at the door, walking in
        for q, idx in self.portals:
            inside = self.place == idx
            if not inside.any():
                continue
            h = self._dwell_hazard(q.region, self.place_t[inside], dt)
            out = np.zeros_like(inside)
            out[inside] = rng.random(h.shape) < h
            if out.any():
                c, wpt = np.array(q.center), np.array(q.watch)
                inward = (wpt - c) / max(np.linalg.norm(wpt - c), 1e-6)
                m = out.sum()
                self.place[out] = ROOM
                self.pos[out] = wpt + rng.normal(size=(m, 2)) * 0.15
                self.vel[out] = inward * 0.7
                self.walk[out] = True
                self.goal[out] = -1
                self.vis[out] = True
                self.hid_t[out] = 0.0
                self.still_t[out] = 0.0
        # someone dropped by the LD2450 may also have got up and gone through a door unseen:
        # proposed regularly (pf_leave_proposal per second) and weighted by its real prior
        # (get-up hazard x door share x P(nobody saw the walk)), so the hypothesis never dies out
        room = self.place == ROOM
        hidden = room & ~self.vis & ~self.walk
        if hidden.any() and self.portals and getattr(self, "_unseen", None):
            q = p.pf_leave_proposal * dt
            go = hidden & (rng.random(hidden.shape) < q)
            if go.any():
                ii, kk = np.nonzero(go)
                gi = rng.integers(len(self.portals), size=len(ii))
                h_up = p.pf_getup_share / (self.still_t[ii, kk] + p.pf_getup_time)
                unseen = np.array([self._unseen_at(g, self.pos[a, b]) for g, a, b in zip(gi, ii, kk)])
                prior = h_up * dt * p.pf_door_goal * unseen  # per door: goal share / n doors x n doors proposed
                np.add.at(self.logw, ii, np.log(np.maximum(prior / q, 1e-300)))
                self.place[ii, kk] = np.array([self.portals[g][1] for g in gi])
                self.place_t[ii, kk] = 0.0
                self.goal[ii, kk] = -1
        self.place_t += dt
        room = self.place == ROOM
        # LD2450 dropouts
        drop_rate = np.where(self.walk, p.pf_drop_walking, p.pf_drop_still)
        drop = room & self.vis & (rng.random(room.shape) < 1 - np.exp(-drop_rate * dt))
        hidden = room & ~self.vis
        back = np.zeros_like(hidden)
        if hidden.any():
            for mode, sel in (("walking", hidden & self.walk), ("still", hidden & ~self.walk)):
                if sel.any():
                    h = self._hazard_hidden(mode, self.hid_t[sel], dt)
                    back[sel] = rng.random(h.shape) < h
        self.vis = (self.vis & ~drop) | back
        self.hid_t = np.where(self.vis, 0.0, self.hid_t + dt)

    # ---------------------------------------------------------- measurements

    def process_frame(self, sensor_id: str, t: float, frame: dict):
        p = self.p
        self.now = max(self.now, t)
        s = self.config.sensor_by_id.get(sensor_id)
        rt = self.runtime.setdefault(sensor_id, SensorRuntime())
        frame_gap = min(max(t - rt.last_frame, 1e-3), 1.0)  # the first frame: as one normal frame gap
        rt.last_frame = t
        if s is None:
            return
        dets = self.helper._detections(s, frame)
        self.helper._mark_stale(rt, frame, dets)
        rt.detections = dets
        if not s.enabled or not s.placed:
            return
        if self.t is None:
            self._init(t)
        self._refresh_grids(t)
        self._predict(t)
        w = min(max(frame_gap, 0.0), p.evidence_time) / p.evidence_time
        if w <= 0:
            return
        use = [d for d in dets if not d.hidden and not d.stale and not d.ignored]
        for d in use:
            self._propose_jump(d, frame_gap)
        self.logw += self._loglik(s, use, w, full=len(dets) >= 3)
        self.logw += self._logprior_bodies(w)
        ld = frame.get("ld2410") or {}
        if ld.get("move_gates") and ld.get("still_gates"):
            energies = [max(a, b) for a, b in zip(ld["move_gates"], ld["still_gates"])]
            self.logw += self._loglik_ld(s, energies, w * p.evidence_time / p.ld2410_evidence_time)
        self._resample()

    def _loglik(self, s, dets, w, full):
        p = self.p
        room = self.place == ROOM
        # dropped people: rarely reported (dropouts are per sensor, the other one may still see them)
        pd = self._pd(s.id, self.pos) * room * np.where(self.vis, 1.0, p.pf_hidden_pd)
        pd = np.clip(pd, 0.0, PD_MAX)
        # up close the LD2450 makes one target of two people (learned resolution): the second
        # one's missing target says nothing then
        for i in range(self.k):
            for j in range(i):
                d = np.linalg.norm(self.pos[:, i] - self.pos[:, j], axis=-1)
                both = pd[:, i] * pd[:, j] > 0
                if both.any():
                    r = self._resolution(d) / RES_FAR
                    f = np.where(both, np.clip(r, 0.0, 1.0), 1.0)
                    pd[:, i] *= np.sqrt(f)
                    pd[:, j] *= np.sqrt(f)
        miss = (1 - pd) ** (0.0 if full else w)
        if not dets:
            return np.log(np.prod(miss, axis=1) + 1e-300)
        m = len(dets)
        # ghost targets: per m^2 (learned) and spread over radial speeds of about +-2 m/s
        lam = np.array([self.sm.clutter_density(s.id, d.pos[0], d.pos[1], p.clutter_density, p.clutter_floor) for d in dets]) / 4.0
        # position likelihood of detection j under person i, with a small kernel for the particle spread
        g = np.zeros((self.n, self.k, m))
        peak = np.zeros(m)  # density at the measured spot itself: "a person right here"
        for j, d in enumerate(dets):
            cov = d.R + np.eye(2) * p.pf_kernel**2
            inv = np.linalg.inv(cov)
            det = np.linalg.det(cov)
            diff = self.pos - d.pos
            d2 = np.einsum("nki,ij,nkj->nk", diff, inv, diff)
            # and the radial speed: a sitter's targets don't move, a walker's do
            v_r = np.where(self.walk, self.vel @ d.radial, 0.0)
            s2 = p.sigma_speed**2 + np.where(self.walk, 0.3**2, 0.0)  # walkers' speed is uncertain
            d2 = d2 + (d.speed - v_r) ** 2 / s2
            g[..., j] = np.exp(-0.5 * d2) / (2 * math.pi * math.sqrt(det) * np.sqrt(2 * math.pi * s2))
            peak[j] = 1 / (2 * math.pi * math.sqrt(det) * math.sqrt(2 * math.pi * p.sigma_speed**2))
        # where exactly: full weight, like a Kalman update; person or ghost: tempered like every
        # other piece of evidence (a ghost lasting a second is one observation, not ten)
        hit = (pd ** w)[..., None] * (g / peak) * (peak / lam) ** w  # (n, k, m)
        total = np.zeros(self.n)
        for a in itertools.product(range(m + 1), repeat=self.k):
            used = [x for x in a if x > 0]
            if len(used) != len(set(used)):
                continue
            term = np.ones(self.n)
            for i, x in enumerate(a):
                term = term * (miss[:, i] if x == 0 else hit[:, i, x - 1])
            total += term
        return np.log(total + 1e-300)

    def _propose_jump(self, d, frame_gap):
        """The model can be wrong (a person lost to a wrong region, a missed entry): with the
        tiny prior pf_jump_rate per second, someone is somewhere else than the particle says.
        Proposed where a detection is explained by nobody, more often than the prior
        (pf_jump_proposal per frame) and weighted by prior / proposal, so the posterior stays
        right: it takes a few frames of support before such a particle wins."""
        p, rng = self.p, self.rng
        room = self.place == ROOM
        dist = np.where(room, np.linalg.norm(self.pos - d.pos, axis=-1), np.inf).min(axis=1)
        sel = (dist > 1.0) & (rng.random(self.n) < p.pf_jump_proposal)
        if not sel.any():
            return
        idx = np.flatnonzero(sel)
        who = rng.integers(self.k, size=len(idx))
        self.place[idx, who] = ROOM
        self.pos[idx, who] = d.pos + rng.normal(size=(len(idx), 2)) * 0.1
        self.vel[idx, who] = 0.0
        self.walk[idx, who] = False
        self.vis[idx, who] = True
        self.hid_t[idx, who] = 0.0
        self.still_t[idx, who] = 0.0
        self.goal[idx, who] = -1
        self.logw[idx] += math.log(p.pf_jump_rate * max(frame_gap, 1e-3) / p.pf_jump_proposal)

    def _logprior_bodies(self, w):
        """Two people can't stand in one place: configurations with two bodies closer than
        pf_body m are penalized (as evidence of pf_overlap per evidence_time, so it adds up
        the longer such a particle claims it)."""
        p = self.p
        out = np.zeros(self.n)
        room = self.place == ROOM
        for i in range(self.k):
            for j in range(i):
                d = np.linalg.norm(self.pos[:, i] - self.pos[:, j], axis=-1)
                out -= w * p.pf_overlap * (room[:, i] & room[:, j] & (d < p.pf_body))
        return out

    def _loglik_ld(self, s, energies, w):
        p = self.p
        n_g = len(energies)
        ratio = {}
        for moving in (False, True):
            r = np.ones(n_g + 1)
            single = [self.sm.ld2410_ratio(s.id, k, energies[k], moving=moving) for k in range(n_g)]
            for g in range(n_g):
                # the person's gate is uncertain by about one (slant, which part of the body):
                # the likelihood is the mixture over the gates it could be in
                ks = [(k, wt) for k, wt in ((g - 1, 0.25), (g, 0.5), (g + 1, 0.25)) if 0 <= k < n_g]
                r[g] = sum(single[k] * wt for k, wt in ks) / sum(wt for _, wt in ks)
            ratio[moving] = r
        dx = self.pos[..., 0] - s.x
        dy = self.pos[..., 1] - s.y
        gy = dx * s._cos + dy * s._sin
        gx = dx * s._sin - dy * s._cos
        lx = -gx if s.mirror else gx
        az = np.degrees(np.arctan2(lx, gy))
        slant = np.sqrt(lx**2 + gy**2 + (s.height - p.target_height) ** 2)
        gate = (slant / GATE).astype(int)
        in_room = (self.place == ROOM) & (gy > 0) & (gate < n_g) & (self._pd(s.id, self.pos) > 0)
        seen = in_room & (np.abs(az) <= p.ld2410_fov / 2)  # evidence taken here
        beam = in_room & (np.abs(az) <= p.ld2410_beam / 2)  # energy comes from here
        out = np.zeros(self.n)
        for i in range(self.k):
            gi = np.where(seen[:, i], gate[:, i], n_g)
            r = np.where(self.walk[:, i], ratio[True][gi], ratio[False][gi])
            # energy at a distance where someone else is (counted before, or in the beam but
            # outside the trusted cone) is explained already
            for j in range(self.k):
                if j == i:
                    continue
                other = (seen[:, j] if j < i else beam[:, j] & ~seen[:, j]) & (np.abs(gate[:, j] - gate[:, i]) <= 2)
                r = np.where(other, 1.0, r)
            out += w * np.log(np.where(seen[:, i], r, 1.0))
        return out

    def _resample(self):
        lw = self.logw - self.logw.max()
        wts = np.exp(lw)
        wts /= wts.sum()
        ess = 1.0 / np.sum(wts**2)
        if ess >= self.n / 2:
            self.logw = np.log(wts + 1e-300)
            return
        u = (self.rng.random() + np.arange(self.n)) / self.n
        idx = np.minimum(np.searchsorted(np.cumsum(wts), u), self.n - 1)
        for name in STATE:
            setattr(self, name, getattr(self, name)[idx].copy())
        self.logw = np.zeros(self.n)
        self._align()

    def _align(self):
        """People are anonymous: order each particle's people like the reference (the mean of
        the population), so that summaries per person make sense."""
        if self.k != 2:
            return
        ref_place = np.array([np.bincount(self.place[:, i], minlength=len(self.places)).argmax() for i in range(2)])
        ref_pos = np.array([self.pos[self.place[:, i] == ROOM, i].mean(axis=0) if (self.place[:, i] == ROOM).any() else np.zeros(2) for i in range(2)])

        def cost(a, b):
            c = np.where(self.place[:, a] == ref_place[0], 0.0, 4.0) + np.where(self.place[:, b] == ref_place[1], 0.0, 4.0)
            c += np.where(self.place[:, a] == ROOM, np.linalg.norm(self.pos[:, a] - ref_pos[0], axis=-1), 0.0)
            c += np.where(self.place[:, b] == ROOM, np.linalg.norm(self.pos[:, b] - ref_pos[1], axis=-1), 0.0)
            return c
        swap = cost(1, 0) < cost(0, 1)
        if swap.any():
            for name in STATE:
                arr = getattr(self, name)
                arr[swap] = arr[swap][:, ::-1]

    # ---------------------------------------------------------------- output

    def step(self, t: float):
        self.now = max(self.now, t)
        if self.t is not None and t > self.t:
            self._predict(t)

    def weights(self):
        lw = self.logw - self.logw.max()
        wts = np.exp(lw)
        return wts / wts.sum()

    def summary(self) -> dict:
        """Per place: expected number of people and P(at least one); per person: P(room) and
        mean position."""
        if self.t is None:
            return {"places": {}, "people": []}
        wts = self.weights()
        places = {}
        for idx, name in enumerate(self.places):
            inside = self.place == idx
            places[name] = {"expected": float(wts @ inside.sum(axis=1)), "occupied": float(wts @ inside.any(axis=1))}
        people = []
        for i in range(self.k):
            room = self.place[:, i] == ROOM
            pr = float(wts @ room)
            mean = (wts[room] @ self.pos[room, i]) / max(wts[room].sum(), 1e-12) if room.any() else np.zeros(2)
            dist = {name: float(wts @ (self.place[:, i] == idx)) for idx, name in enumerate(self.places)}
            people.append({"room": pr, "pos": mean.tolist(), "where": dist,
                           "hidden": float(wts @ (room & ~self.vis[:, i])) / max(pr, 1e-12)})
        return {"places": places, "people": people}

    def zone_occupancy(self) -> dict:
        """Zone id -> (expected count, P(occupied)) for room and area zones in the observed area,
        and for the rooms of each region."""
        wts = self.weights()
        out = {}
        for z in self.config.zones:
            if z.kind not in ("room", "area"):
                continue
            inside = np.zeros((self.n, self.k), dtype=bool)
            region = next((rid for rid, r in self.config.regions.items() if z.id in r["rooms"]), None)
            if region is not None and region in self.places:
                inside = self.place == self.places.index(region)
            elif region is None:
                room = self.place == ROOM
                for i in range(self.k):
                    xs, ys = self.pos[:, i, 0], self.pos[:, i, 1]
                    inside[:, i] = room[:, i] & np.array([z.contains(x, y) for x, y in zip(xs, ys)])
            out[z.id] = (float(wts @ inside.sum(axis=1)), float(wts @ inside.any(axis=1)))
        return out
