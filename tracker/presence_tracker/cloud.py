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
MIN_PER_PLACE = 16  # particles every place with any weight keeps through resampling


class Motion:
    """The motion model's numbers (MODEL.md 3.1, 3.2). Priors; learned later."""

    # measured (5.10., 4.6 h of LD2450 target tracks): velocity changes over 2-3 s like 0.6 m/s^2
    # per sqrt(s). Until 0.6.17 assumed 1.0: a walk's direction was soon forgotten, and somebody
    # walking out of a door stayed in front of it in the model (5.10., 16:44). MODEL.md 3.1
    walk_noise = 0.6  # m/s^2 per sqrt(s): random changes of a walker's velocity
    max_speed = 2.0  # m/s
    still_noise = 0.02  # m per sqrt(s): a standing person sways
    stop_rate = 0.5  # 1/s: a walker stops (the measured, rising rate was worse: MODEL.md 3.1)
    go_share, go_time = 0.58, 5.6  # standing -> walking at rate go_share / (time standing + go_time)
    start_speed = (0.6, 1.3)  # m/s, a walker that just started
    exit_speed = 0.8  # m/s, walking into the room from a door
    exit_proposal = 0.01  # per step: share of the particles behind a door tried at the door (importance sampling)
    start_proposal = 0.02  # per step: share of the standing particles near a measurement tried as starting to walk
    start_near = 2.0  # m: "near"
    start_kappa = 4.0  # concentration of the tried directions around the measurements
    # into and out of the house (the residents: out for hours, then back through a way in)
    arrive_rate = 1 / (4 * 3600)  # 1/s per way in: somebody out of the house comes back
    arrive_proposal = 0.005  # per step: share of the particles outside tried as coming in
    leave_rate = 1 / (2 * 3600)  # 1/s: somebody in a place with a way out (stairs) leaves the house


class Cloud:
    def __init__(self, n: int, n_sensors: int, rng: np.random.Generator):
        self.n = n
        self.rng = rng
        self.place = np.zeros(n, dtype=np.int16)
        self.pos = np.zeros((n, 2))
        self.vel = np.zeros((n, 2))
        self.mode = np.full(n, STILL, dtype=np.int8)
        self.since = np.zeros(n)  # time the particle entered its mode
        self.entered = np.zeros(n)  # time it entered its place (the stays behind doors count from it)
        self.last_hit = np.zeros((n, n_sensors))  # time of the last detection per sensor
        # where each sensor's target sits on this person right now, in its errors (range, lateral) -
        # it wanders slowly (MODEL.md 4.1): mean, and variance relative to that of the wandering
        self.bias = np.zeros((n, n_sensors, 2))
        self.bias_var = np.ones((n, n_sensors))
        self.logw = np.full(n, -math.log(n))

    # ------------------------------------------------------------------ setup

    @classmethod
    def at_place(cls, n, n_sensors, rng, place: int, t: float, world=None) -> "Cloud":
        c = cls(n, n_sensors, rng)
        c.place[:] = place
        if world is not None and place != world.outside:
            c.pos = world.sample(place, n, rng)
        c.since[:] = t
        c.entered[:] = t
        c.last_hit[:] = t
        return c

    @classmethod
    def anywhere(cls, n, n_sensors, rng, world, t: float) -> "Cloud":
        """Nothing known (start without a saved state): a third standing anywhere in the observed
        area, a third behind the doors, a third not in the house."""
        c = cls(n, n_sensors, rng)
        regions = [i for i in range(1, len(world.places))]
        c.pos = world.sample(OBSERVED, n, rng)
        behind = rng.random(n) < 2 / 3 if regions else np.zeros(n, dtype=bool)
        if regions:
            c.place[behind] = np.array(regions)[rng.integers(0, len(regions), int(behind.sum()))]
            for pl in np.unique(c.place[behind]):
                k = np.flatnonzero(c.place == pl)
                if pl != world.outside:
                    c.pos[k] = world.sample(int(pl), len(k), rng)
        c.since[:] = t
        c.entered[:] = t
        c.last_hit[:] = t  # watched from now on: not being seen counts from the start
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
        """Resampling when a few particles carry nearly all weight, as in the mixture particle
        filter (Vermaak, Doucet, Perez 2003; MODEL.md 5): each place is a component. Its total
        weight - how probable it is that the person is there - stays exactly what Bayes says, and
        the particles are drawn anew only within it, each place keeping at least MIN_PER_PLACE.
        Resampled as one cloud, a place of small weight lost all its particles: the possibility
        "not here" was gone for good, and a person nothing measured any more could never leave."""
        if self.ess() > self.n / 2:
            return
        places = np.unique(self.place)
        groups = [np.flatnonzero(self.place == p) for p in places]
        # in logs per place: a place may weigh less than exp(-745) of the rest and still count
        logmass = np.array([self.logw[g].max() + math.log(np.exp(self.logw[g] - self.logw[g].max()).sum()) for g in groups])
        mass = np.exp(logmass - logmass.max())
        mass /= mass.sum()
        reserve = min(MIN_PER_PLACE, self.n // len(groups))
        counts = np.full(len(groups), reserve)
        free = self.n - counts.sum()
        share = mass * free
        counts += np.floor(share).astype(int)
        rest = self.n - counts.sum()
        if rest > 0:
            counts[np.argsort(-(share - np.floor(share)))[:rest]] += 1
        idx, logw = [], []
        for g, k, lm in zip(groups, counts, logmass):
            w = np.exp(self.logw[g] - self.logw[g].max())
            u = (self.rng.random() + np.arange(k)) / k
            idx.append(g[np.minimum(np.searchsorted(np.cumsum(w / w.sum()), u), len(g) - 1)])
            logw.append(np.full(k, lm - math.log(k)))
        idx = np.concatenate(idx)
        for name in ("place", "pos", "vel", "mode", "since", "entered", "last_hit", "bias", "bias_var"):
            setattr(self, name, getattr(self, name)[idx].copy())
        self.logw = np.concatenate(logw)
        self.normalize()

    # ---------------------------------------------------------------- motion

    def predict(self, t: float, dt: float, world, dwell, m: Motion = Motion, targets=None):
        """targets: positions (k, 2) of the current measurements; they only steer which rare moves
        are tried (getting up toward them), the weights stay exact."""
        if dt <= 0:
            return
        rng = self.rng
        # everybody in the house has a position, also where no sensor sees (MODEL.md 2)
        obs = self.place != world.outside
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
            # through a door into another place: walking (a sway doesn't change rooms). Out of a
            # place without a sensor also unseen when its stay ends (below)
            own = self.place[moved]
            ok = ~blocked & (target >= 0) & ((target == own) | walking)
            go = moved[ok]
            self.pos[go] = new[go]
            through = go[target[ok] != own[ok]]
            self.place[through] = target[ok][target[ok] != own[ok]]
            self.entered[through] = t
            # into a wall: stopped there
            stop = moved[~ok]
            self.vel[stop] = 0.0

        # mode changes: walkers stop, standing people start walking (the longer they stand, the
        # less likely)
        obs = self.place != world.outside
        k = np.flatnonzero(obs & (self.mode == WALK))
        stop_rate = m.stop_rate
        stops = k[rng.random(len(k)) < 1 - np.exp(-stop_rate * dt)]
        self.mode[stops] = STILL
        self.vel[stops] = 0.0
        self.since[stops] = t
        k = np.flatnonzero(obs & (self.mode == STILL))
        if len(k):
            rate = m.go_share / (np.maximum(t - self.since[k], 0.0) + m.go_time)
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

        # into the house and out of it: from outside into a place with a way in (or in at an entry
        # of the observed area) at the arrival rate - rare, so tried more often and the weights
        # corrected (exact); from such a place out at the leaving rate
        k = np.flatnonzero(self.place == world.outside)
        ways = list(world.open_places)
        entries = world.portals_of(world.outside)
        n_ways = len(ways) + len(entries)
        if len(k) and n_ways:
            p0 = 1 - math.exp(-m.arrive_rate * n_ways * dt)
            # tried more often only while enough particles stay: tried away, the place would lose all
            # its particles and with them its weight (the mixture components, resample())
            q = max(p0, m.arrive_proposal) if len(k) > 4 * MIN_PER_PLACE else p0
            go = rng.random(len(k)) < q
            self.logw[k[go]] += math.log(p0 / q)
            self.logw[k[~go]] += math.log((1 - p0) / (1 - q))
            kg = k[go]
            which = rng.integers(0, n_ways, len(kg))
            into = which < len(ways)
            self.place[kg[into]] = np.array(ways, dtype=np.int16)[which[into]] if ways else 0
            self.since[kg[into]] = t
            self.entered[kg[into]] = t
            for pl in np.unique(self.place[kg[into]]):
                ki = kg[into][self.place[kg[into]] == pl]
                self.pos[ki] = world.sample(int(pl), len(ki), rng)
                self.mode[ki] = STILL
                self.vel[ki] = 0.0
            ke = kg[~into]
            if len(ke):
                e = which[~into] - len(ways)
                self.pos[ke] = np.array([entries[i][0] for i in e]) + rng.normal(0, 0.15, (len(ke), 2))
                self.vel[ke] = np.array([entries[i][1] for i in e]) * m.exit_speed
                self.place[ke] = OBSERVED
                self.mode[ke] = WALK
                self.since[ke] = t
                self.entered[ke] = t
                self.last_hit[ke] = t
        if world.open_places:
            k = np.flatnonzero(np.isin(self.place, world.open_places))
            out = k[rng.random(len(k)) < 1 - math.exp(-m.leave_rate * dt)]
            self.place[out] = world.outside
            self.entered[out] = t

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
            ages = np.maximum(np.round(t - self.entered[k]), 0.0)
            uniq, inv = np.unique(ages, return_inverse=True)
            hz = np.array([dwell.hazard(region, a, dt) for a in uniq])[inv]
            q = np.maximum(hz, m.exit_proposal) if len(k) > 4 * MIN_PER_PLACE else hz
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
            self.entered[ko] = t
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
