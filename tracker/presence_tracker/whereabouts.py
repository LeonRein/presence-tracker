"""Where a person is when no sensor sees them.

A confirmed track is somewhere; its probability mass is spread over hypotheses whose sum stays 1:

  here     at the Kalman position, undetected (sitting still, dropped by the LD2450)
  near     moved unseen inside the observed area, within walking reach of the last position
  <portal> went through that door: into a room without a sensor (closed: balcony, kitchen),
           into an open region (hallway with the stairs: people come and go) or outside
  gone     out of our knowledge: left the house, or we lost them for good
  dead     was never a person: a ghost or a duplicate that got confirmed. Starts at
           1 - existence when the track is confirmed; nothing observes it, so it wins
           whenever "here" and "near" are refuted and no door was within reach

While the person is unseen, mass leaks from `here` to the reachable portals and to `near`: fast
for someone who was walking, slowly for someone who sat. Evidence then moves mass around (Bayes,
tempered like the existence probability): a sensor that sees the spot well and reports nothing
pushes `here` (and `near`) down; LD2410C energy at the right distance pushes `here` up. Inside
an unobserved region nothing is observed, but visits end: by the learned stay durations, the
person comes out again, seen or unseen (mass to `gone`). A person seen coming out is a new
track at the door that, once confirmed, takes this track over (same id): a ghost at a glass
door never moves anybody. Measurements only ever attach to `here` directly.

The hypotheses only hold weights and times; the Kalman state stays with the track and belongs
to `here`.
"""

import math

import numpy as np

HERE, NEAR, GONE, DEAD = "here", "near", "gone", "dead"
ABSENT = (GONE, DEAD, "outside")  # not at home


class Whereabouts:
    def __init__(self, params, existence: float = 1.0):
        self.p = params
        self.w = {HERE: existence, NEAR: 0.0, GONE: 0.0, DEAD: 1.0 - existence}  # portal region ids are added as needed
        self.t_in = {}  # region -> time the mass started flowing there (the person left when last seen)
        self.blind = {}  # region -> seconds in which no sensor watched its door
        self.visited = set()  # regions that were the most probable whereabouts at some point
        self.t_last_seen = None
        self.hits_since_loss = 0

    # ------------------------------------------------------------- queries

    def here(self) -> float:
        return self.w[HERE]

    def region(self, rid: str) -> float:
        return self.w.get(rid, 0.0)

    def absent(self) -> float:
        """Mass that is not at home: gone, never real, or outside."""
        return sum(self.w.get(k, 0.0) for k in ABSENT)

    def best(self) -> tuple:
        k = max(self.w, key=self.w.get)
        return k, self.w[k]

    def regions(self) -> dict:
        return {k: v for k, v in self.w.items() if k not in (HERE, NEAR, GONE, DEAD) and v > 0}

    # ------------------------------------------------------------ dynamics

    def leak(self, dt: float, walking: bool, portals: list, t_last_seen: float):
        """Prior flow while unseen: here -> near (the person is not where we last saw them) and
        near -> the doors within reach. portals: [(region, prior_weight)]. A region's visit
        starts when the person was last seen."""
        p = self.p
        self.t_last_seen = t_last_seen
        rate = p.leak_walking if walking else p.leak_still
        if self.w[HERE] > 0:
            moved = self.w[HERE] * (1 - math.exp(-rate * dt))
            self.w[HERE] -= moved
            self.w[NEAR] += moved
        total = sum(wt for _, wt in portals)
        if total > 0 and self.w[NEAR] > 0:
            moved = self.w[NEAR] * (1 - math.exp(-p.leak_near * dt))
            self.w[NEAR] -= moved
            for rid, wt in portals:
                share = moved * wt / total
                if share > 0 and self.w.get(rid, 0.0) < p.flow_threshold <= self.w.get(rid, 0.0) + share:
                    self.t_in.setdefault(rid, t_last_seen)
                self.w[rid] = self.w.get(rid, 0.0) + share

    def end_visits(self, t: float, dt: float, dwell, watched: dict):
        """Visits to unobserved regions end: the person comes out. Seen (we'd have a detection at
        the door: no mass moves here, the association does that) or unseen (mass to gone). A
        return that should have been seen but wasn't counts against the hypothesis."""
        p = self.p
        for rid in list(self.regions()):
            t_in = self.t_in.get(rid)
            if t_in is None:
                continue
            if not watched.get(rid, True):
                self.blind[rid] = self.blind.get(rid, 0.0) + dt
            since = max(t - t_in, 0.0)
            h = dwell.hazard(rid, since, dt)  # share of visits of that age ending now
            m = p.missed_return
            if since > 0:
                m += (1 - m) * min(self.blind.get(rid, 0.0) / since, 1.0)
            if rid == "outside":
                m = 1.0
            ending = self.w[rid] * h
            self.w[rid] -= ending
            self.w[GONE] += ending * m  # the (1 - m) seen-but-not-seen part dies: renormalize
            # and slowly we simply lose track of people behind doors (a return we missed, a
            # duplicate track): otherwise the long tail of the stay prior freezes them in there
            forgotten = self.w[rid] * (1 - math.exp(-dt / p.region_forget_time))
            self.w[rid] -= forgotten
            self.w[GONE] += forgotten
        self.normalize()

    def normalize(self):
        s = sum(self.w.values())
        if s > 0:
            for k in self.w:
                self.w[k] /= s
        best = max(self.w, key=self.w.get)
        if best not in (HERE, NEAR, GONE, DEAD):
            self.visited.add(best)
        # mass may also reach a region by renormalization: its visit starts then at the latest
        for k, v in self.w.items():
            if k not in (HERE, NEAR, GONE, DEAD) and v >= self.p.flow_threshold and k not in self.t_in and self.t_last_seen is not None:
                self.t_in[k] = self.t_last_seen

    # ------------------------------------------------------------- evidence

    def update(self, key: str, ratio: float, weight: float):
        """Tempered Bayes update of one hypothesis against all others: its weight is multiplied
        by ratio ** weight, then everything is renormalized."""
        if key not in self.w or self.w[key] <= 0 or ratio == 1.0:
            return
        self.w[key] *= max(ratio, 1e-9) ** weight
        self.normalize()

    def arrive(self, t: float, dwell, at_door) -> list:
        """The person is seen again at their place. A region that was the most probable
        whereabouts meanwhile ends: seen again at that region's door, it was a real visit and
        its duration is learned; seen again at their old place mid-room, they probably sat
        there all along (nothing learned). at_door(region) -> bool. Returns [(region, duration)]."""
        ended = []
        if self.w[HERE] >= 0.5:
            for rid in list(self.t_in):
                if self.w.get(rid, 0.0) < 0.5:
                    duration = t - self.t_in.pop(rid)
                    self.blind.pop(rid, None)
                    if rid in self.visited:
                        if at_door(rid):
                            dwell.learn(rid, duration)
                        ended.append((rid, duration))
                    self.visited.discard(rid)
        return ended

    def returned(self, region: str, t: float, dwell) -> float | None:
        """The person came back out of `region` for sure (a confirmed track at its door took
        this one over): the visit ended and is learned; everything collapses onto here."""
        duration = None
        if self.t_in.get(region) is not None:
            duration = t - self.t_in[region]
            if region in self.visited:
                dwell.learn(region, duration)
        self.reset_here()
        return duration

    def dissolve(self, region: str, share: float):
        """Someone came out of `region` and was attributed to another track: with probability
        `share` it was this person, whose identity then lives on in that track: to gone."""
        moved = self.w.get(region, 0.0) * share
        if moved > 0:
            self.w[region] -= moved
            self.w[GONE] += moved
            self.normalize()

    def reset_here(self):
        """Seen again for sure: everything collapses onto here."""
        for k in self.w:
            self.w[k] = 0.0
        self.w[HERE] = 1.0
        self.w[DEAD] = 0.0
        self.t_in.clear()
        self.blind.clear()
        self.visited.clear()

    def to_dict(self) -> dict:
        return {k: round(v, 3) for k, v in self.w.items() if v >= 0.005}
