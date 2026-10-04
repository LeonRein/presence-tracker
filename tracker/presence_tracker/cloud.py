"""One person as a cloud of weighted particles (MODEL.md section 5): every particle is one possible
whereabouts of the person, the cloud is their superposition.

A particle is either in the observed area (position, velocity, walking or standing, time in that
mode, time of the last hit per sensor) or in a place without a sensor (World place index > 0,
time since it went in). Between frames the particles move by the motion model (MODEL.md 3.1, 3.2);
a frame multiplies their weights by how well the person there explains it (crowd.py).
"""

import math

import numpy as np

from .world import OBSERVED

WALK, STILL = 1, 0


class Motion:
    """The motion model's numbers (MODEL.md 3.1, 3.2). Priors; learned later."""

    walk_noise = 1.0  # m/s^2 per sqrt(s): random changes of a walker's velocity
    max_speed = 2.0  # m/s
    still_noise = 0.02  # m per sqrt(s): a standing person sways
    stop_rate = 0.5  # 1/s: a walker stops
    go_share, go_time = 0.58, 5.6  # standing -> walking at rate go_share / (time standing + go_time)
    start_speed = (0.6, 1.3)  # m/s, a walker that just started
    exit_speed = 0.8  # m/s, walking into the room from a door
    exit_proposal = 0.01  # per step: share of the particles behind a door tried at the door (importance sampling)
    start_proposal = 0.02  # per step: share of the standing particles near a measurement tried as starting to walk
    start_near = 2.0  # m: "near"
    start_kappa = 4.0  # concentration of the tried directions around the measurements


class Cloud:
    def __init__(self, n: int, n_sensors: int, rng: np.random.Generator):
        self.n = n
        self.rng = rng
        self.place = np.zeros(n, dtype=np.int16)
        self.pos = np.zeros((n, 2))
        self.vel = np.zeros((n, 2))
        self.mode = np.full(n, STILL, dtype=np.int8)
        self.since = np.zeros(n)  # time the particle entered its mode (observed) or its place (else)
        self.anchor = np.zeros((n, 2))  # where the last mode change happened (for learning)
        self.counted = np.ones(n, dtype=bool)  # that mode change was learned already
        self.last_hit = np.zeros((n, n_sensors))  # time of the last detection per sensor
        self.logw = np.full(n, -math.log(n))

    # ------------------------------------------------------------------ setup

    @classmethod
    def at_place(cls, n, n_sensors, rng, place: int, t: float) -> "Cloud":
        c = cls(n, n_sensors, rng)
        c.place[:] = place
        c.since[:] = t
        c.last_hit[:] = t
        return c

    @classmethod
    def anywhere(cls, n, n_sensors, rng, world, t: float) -> "Cloud":
        """Nothing known (start without a saved state): half standing anywhere in the observed
        area, half behind the doors."""
        c = cls(n, n_sensors, rng)
        ii, jj = np.nonzero(world.labels == OBSERVED)
        regions = [i for i in range(1, len(world.places) - 1)]
        k = rng.integers(0, len(ii), n)
        c.pos = np.stack([world.x0 + (ii[k] + rng.random(n)) * 0.1, world.y0 + (jj[k] + rng.random(n)) * 0.1], axis=1)
        behind = rng.random(n) < 0.5 if regions else np.zeros(n, dtype=bool)
        if regions:
            c.place[behind] = np.array(regions)[rng.integers(0, len(regions), int(behind.sum()))]
        c.since[:] = t
        c.last_hit[:] = t - 60.0  # unseen for a while
        c.anchor[:] = c.pos
        return c

    # -------------------------------------------------------------- weights

    def weights(self) -> np.ndarray:
        w = np.exp(self.logw - self.logw.max())
        return w / w.sum()

    def normalize(self) -> float:
        """Normalize; returns the log of the total weight before (the evidence)."""
        top = self.logw.max()
        total = top + math.log(np.exp(self.logw - top).sum())
        self.logw -= total
        return total

    def ess(self) -> float:
        w = self.weights()
        return 1.0 / float((w * w).sum())

    def resample(self):
        """Systematic resampling when a few particles carry nearly all weight."""
        if self.ess() > self.n / 2:
            return
        w = self.weights()
        u = (self.rng.random() + np.arange(self.n)) / self.n
        idx = np.minimum(np.searchsorted(np.cumsum(w), u), self.n - 1)
        for name in ("place", "pos", "vel", "mode", "since", "last_hit", "anchor", "counted"):
            setattr(self, name, getattr(self, name)[idx].copy())
        self.logw[:] = -math.log(self.n)

    # ---------------------------------------------------------------- motion

    def predict(self, t: float, dt: float, world, dwell, m: Motion = Motion, targets=None, habits=None):
        """targets: positions (k, 2) of the current measurements; they only steer which rare moves
        are tried (getting up toward them), the weights stay exact."""
        if dt <= 0:
            return
        rng = self.rng
        obs = self.place == OBSERVED
        walk = obs & (self.mode == WALK)
        still = obs & (self.mode == STILL)

        # walkers: random acceleration, a speed limit; standing: a little sway
        k = np.flatnonzero(walk)
        if len(k):
            self.vel[k] += rng.normal(0, m.walk_noise * math.sqrt(dt), (len(k), 2))
            speed = np.linalg.norm(self.vel[k], axis=1)
            fast = speed > m.max_speed
            self.vel[k[fast]] *= (m.max_speed / speed[fast])[:, None]
        k_still = np.flatnonzero(still)
        new = self.pos.copy()
        new[k] += self.vel[k] * dt
        new[k_still] += rng.normal(0, m.still_noise * math.sqrt(dt), (len(k_still), 2))
        moved = np.flatnonzero(obs)
        if len(moved):
            # walls: only walkers get far enough to cross one (a sway may only leave the rooms)
            blocked = np.zeros(len(moved), dtype=bool)
            walking = self.mode[moved] == WALK
            blocked[walking] = world.crosses_wall(self.pos[moved[walking]], new[moved[walking]])
            target = world.place_of(new[moved])
            ok = ~blocked & (target >= 0)
            go = moved[ok]
            self.pos[go] = new[go]
            # through a door into a place without a sensor
            through = go[target[ok] != OBSERVED]
            self.place[through] = target[ok][target[ok] != OBSERVED]
            self.since[through] = t
            # into a wall: stopped there
            stop = moved[~ok]
            self.vel[stop] = 0.0

        # mode changes: walkers stop, standing people start walking (the longer they stand, the
        # less likely)
        obs = self.place == OBSERVED
        k = np.flatnonzero(obs & (self.mode == WALK))
        stop_rate = m.stop_rate * (habits.factor("stop", self.pos[k]) if habits is not None else 1.0)
        stops = k[rng.random(len(k)) < 1 - np.exp(-stop_rate * dt)]
        self.mode[stops] = STILL
        self.vel[stops] = 0.0
        self.since[stops] = t
        self.anchor[stops] = self.pos[stops]
        self.counted[stops] = False
        k = np.flatnonzero(obs & (self.mode == STILL))
        if len(k):
            rate = m.go_share / (np.maximum(t - self.since[k], 0.0) + m.go_time)
            if habits is not None:
                rate = rate * habits.factor("go", self.pos[k])
            p0 = 1 - np.exp(-rate * dt)
            # tried more often (and toward the measurements) where a measurement is near: a person
            # getting up would otherwise rarely have a particle starting the right way
            near = np.zeros((len(k), 0), dtype=bool)
            if targets is not None and len(targets):
                d = self.pos[k][:, None, :] - np.asarray(targets)[None, :, :]
                near = np.hypot(d[..., 0], d[..., 1]) < m.start_near
            boost = near.any(axis=1)
            q = np.where(boost, np.maximum(p0, m.start_proposal), p0)
            go = rng.random(len(k)) < q
            self.logw[k[go]] += np.log(p0[go] / q[go])
            self.logw[k[~go]] += np.log((1 - p0[~go]) / (1 - q[~go]))
            starts = k[go]
            angle = rng.uniform(0, 2 * math.pi, len(starts))
            near_go = near[go] if near.shape[1] else near[go]
            b = boost[go]
            if b.any():
                tg = np.asarray(targets)
                # half of the boosted starts head for a random nearby measurement (von Mises spread)
                steer = b & (rng.random(len(starts)) < 0.5)
                pick = np.argmax(np.where(near_go, rng.random(near_go.shape), -1.0), axis=1)
                rel = tg[pick] - self.pos[starts]
                aim = np.arctan2(rel[:, 1], rel[:, 0]) + rng.vonmises(0.0, m.start_kappa, len(starts))
                angle = np.where(steer, aim, angle)
                # weight: prior density of the direction (uniform) over the density it was drawn with
                rel_all = tg[None, :, :] - self.pos[starts][:, None, :]
                bearings = np.arctan2(rel_all[..., 1], rel_all[..., 0])
                vm = np.exp(m.start_kappa * np.cos(angle[:, None] - bearings)) / (2 * math.pi * np.i0(m.start_kappa))
                n_near = np.maximum(near_go.sum(axis=1), 1)
                dens = 0.5 / (2 * math.pi) + 0.5 * (vm * near_go).sum(axis=1) / n_near
                self.logw[starts[b]] += np.log((1 / (2 * math.pi)) / dens[b])
            speed = rng.uniform(*m.start_speed, len(starts))
            self.vel[starts] = np.stack([np.cos(angle), np.sin(angle)], axis=1) * speed[:, None]
            self.mode[starts] = WALK
            self.since[starts] = t
            self.anchor[starts] = self.pos[starts]
            self.counted[starts] = False

        # behind a door: the visit ends by the learned stays, coming out at a door of that place.
        # Rare per step, so tried more often than it happens and the weights corrected (exact).
        for place in np.unique(self.place):
            if place == OBSERVED or place == world.outside:
                continue
            doors = world.portals_of(int(place))
            if not doors:
                continue
            k = np.flatnonzero(self.place == place)
            region = world.places[int(place)]
            # by whole seconds of the visit: the stays are learned in seconds anyway
            ages = np.maximum(np.round(t - self.since[k]), 0.0)
            uniq, inv = np.unique(ages, return_inverse=True)
            hz = np.array([dwell.hazard(region, a, dt) for a in uniq])[inv]
            q = np.maximum(hz, m.exit_proposal)
            out = rng.random(len(k)) < q
            self.logw[k[out]] += np.log(np.maximum(hz[out], 1e-300) / q[out])
            self.logw[k[~out]] += np.log((1 - hz[~out]) / (1 - q[~out]))
            ko = k[out]
            which = rng.integers(0, len(doors), len(ko))
            watches = np.array([d[0] for d in doors])
            inwards = np.array([d[1] for d in doors])
            self.pos[ko] = watches[which] + rng.normal(0, 0.15, (len(ko), 2))
            self.vel[ko] = inwards[which] * m.exit_speed
            self.place[ko] = OBSERVED
            self.mode[ko] = WALK
            self.since[ko] = t
            self.counted[ko] = True
            self.last_hit[ko] = t  # just came in: seen like anybody walking in view

    # --------------------------------------------------------------- summary

    def place_probabilities(self, n_places: int) -> np.ndarray:
        return np.bincount(self.place.astype(int), weights=self.weights(), minlength=n_places)

    def heat(self, world, cell: float = 0.2) -> tuple:
        """Weight per cell of the observed area: (grid (nx, ny), x0, y0, cell)."""
        nx = int(math.ceil(world.nx * 0.1 / cell))
        ny = int(math.ceil(world.ny * 0.1 / cell))
        grid = np.zeros((nx, ny))
        obs = self.place == OBSERVED
        if obs.any():
            i = np.clip(((self.pos[obs, 0] - world.x0) / cell).astype(int), 0, nx - 1)
            j = np.clip(((self.pos[obs, 1] - world.y0) / cell).astype(int), 0, ny - 1)
            np.add.at(grid, (i, j), self.weights()[obs])
        return grid, world.x0, world.y0, cell
