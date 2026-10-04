"""Where a person is when no sensor sees them.

A confirmed track is a person somewhere in the flat, or it never was a person of its own. Its
probability mass is spread over places whose sum stays 1:

  room     in the observed area, at the Kalman position (sitting still, dropped by the LD2450;
           having moved a bit is the Kalman filter's business: its uncertainty grows)
  <region> went through a door into a region without a sensor: a closed room (balcony,
           kitchen) or the open hallway region (with the stairs: leaving the house is in there).
           "outside" is the region behind an entry zone drawn in the observed area.
  dead     never a person of its own: a ghost, or a second track of someone who has one.
           Starts at 1 - existence when the track is confirmed. Nothing observes it, so it wins
           whenever every place a person could be is refuted.

A person never just vanishes, so mass only moves between these places by the way people move:
someone in the room goes through a door only by walking there, and a walker in view of a sensor
is seen with its detection probability. Per door:

  went there = P(walking toward that door) * P(not detected anywhere along the way)

Someone who was walking toward a door when lost (one-shot, at the loss) and someone sitting who
gets up (start_rate) contribute; the part of them that should have been seen on the way is
refuted (removed, then renormalized). Inside a region nothing is observed, but visits end by the
learned stay durations: a person coming out is seen at the door (that doesn't happen: refuted)
or missed (missed_return: back in the room). Evidence about the room itself (no detection
although the LD2450 would have re-detected a sitter by now, LD2410C energy) comes from the
tracker as tempered likelihood ratios.

The hypotheses only hold weights and times; the Kalman state stays with the track.
"""

ROOM, DEAD, OUTSIDE = "room", "dead", "outside"
PLACES = (ROOM, DEAD)  # everything else is a region id
NOT_HOME = (DEAD, OUTSIDE)


class Whereabouts:
    def __init__(self, params, existence: float = 1.0):
        self.p = params
        self.w = {ROOM: existence, DEAD: 1.0 - existence}  # region ids are added as needed
        self.t_in = {}  # region -> time the visit started (mass first got there)
        self.blind = {}  # region -> seconds in which no sensor watched its door
        self.visited = set()  # regions that were the most probable whereabouts at some point

    # ------------------------------------------------------------- queries

    def room(self) -> float:
        return self.w[ROOM]

    def dead(self) -> float:
        return self.w[DEAD]

    def region(self, rid: str) -> float:
        return self.w.get(rid, 0.0)

    def real(self) -> float:
        """Probability that this is a person at home (not a duplicate, not outside)."""
        return 1.0 - sum(self.w.get(k, 0.0) for k in NOT_HOME)

    def view(self, real: float | None = None) -> dict:
        """The weights with the probability of being a person at home replaced by `real` (the
        tracker's count of residents and guests shifts it): home places scale by real / r,
        the others by (1 - real) / (1 - r)."""
        if real is None:
            return dict(self.w)
        r = self.real()
        home = real / r if r > 1e-9 else 0.0
        away = (1 - real) / (1 - r) if r < 1 - 1e-9 else 0.0
        out = {k: v * (away if k in NOT_HOME else home) for k, v in self.w.items()}
        if r <= 1e-9:
            out[ROOM] = real
        if r >= 1 - 1e-9:
            out[DEAD] = out.get(DEAD, 0.0) + 1 - real
        return out

    def best(self, real: float | None = None) -> tuple:
        w = self.view(real)
        k = max(w, key=w.get)
        return k, w[k]

    def regions(self) -> dict:
        return {k: v for k, v in self.w.items() if k not in PLACES and v > 0}

    # ------------------------------------------------------------ dynamics

    def go(self, t: float, share: float, through: list):
        """A share of the room mass walked off toward the doors: through = [(region, prior,
        unseen)], prior = P(that door | walking), unseen = P(not detected on the way there).
        Whoever went is removed from the room; only the unseen part arrives behind the door,
        the seen part is refuted by the missing detections."""
        moving = self.w[ROOM] * share
        if moving <= 0:
            return
        for rid, prior, unseen in through:
            if prior <= 0:
                continue
            self.w[ROOM] -= moving * prior
            arrived = moving * prior * unseen
            if arrived > 0:
                self.w[rid] = self.w.get(rid, 0.0) + arrived
                if self.w[rid] >= self.p.flow_threshold:
                    self.t_in.setdefault(rid, t)
        self.normalize()

    def end_visits(self, t: float, dt: float, dwell, watched: dict):
        """Visits to regions end: the person comes out. Seen at the door: we'd have a detection
        there, which didn't happen (refuted). Missed (missed_return, rising with the time the
        door was unwatched): back in the room unseen."""
        p = self.p
        for rid in list(self.regions()):
            t_in = self.t_in.get(rid)
            if t_in is None:
                continue
            if not watched.get(rid, True):
                self.blind[rid] = self.blind.get(rid, 0.0) + dt
            since = max(t - t_in, 0.0)
            ending = self.w[rid] * dwell.hazard(rid, since, dt)  # share of visits of that age ending now
            m = p.missed_return
            if since > 0:
                m += (1 - m) * min(self.blind.get(rid, 0.0) / since, 1.0)
            self.w[rid] -= ending
            self.w[ROOM] += ending * m
        self.normalize()

    def normalize(self):
        s = sum(self.w.values())
        if s > 0:
            for k in self.w:
                self.w[k] /= s
        best = max(self.w, key=self.w.get)
        if best not in PLACES:
            self.visited.add(best)

    # ------------------------------------------------------------- evidence

    def update(self, key: str, ratio: float, weight: float):
        """Tempered Bayes update of one place against all others: its weight is multiplied by
        ratio ** weight, then everything is renormalized."""
        if key not in self.w or self.w[key] <= 0 or ratio == 1.0:
            return
        self.w[key] *= max(ratio, 1e-9) ** weight
        self.normalize()

    def arrive(self, t: float, dwell, at_door) -> list:
        """The person is measured again in the room. A region that was the most probable
        whereabouts meanwhile ends: measured first at that region's door, it was a real visit
        and its duration is learned; measured at their old place mid-room, they probably sat
        there all along (nothing learned). at_door(region) -> bool. Returns [(region, duration)]."""
        ended = []
        if self.w[ROOM] >= 0.5:
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
        this one over): the visit ended and is learned; everything collapses onto the room."""
        duration = None
        if self.t_in.get(region) is not None:
            duration = t - self.t_in[region]
            if region in self.visited:
                dwell.learn(region, duration)
        self.collapse()
        return duration

    def duplicate(self, region: str, amount: float):
        """Someone came out of `region` and is tracked by another track: this amount of this
        track's mass in there was that very person, i.e. this track was their second one."""
        moved = min(self.w.get(region, 0.0), amount)
        if moved > 0:
            self.w[region] -= moved
            self.w[DEAD] += moved
            self.normalize()

    def lost(self, duplicate: float):
        """Just lost: being measured says nothing about being someone's second track; that
        probability (duplicate, from the pair evidence) comes back into "never a person"."""
        if self.w[DEAD] < duplicate:
            self.w[ROOM] -= duplicate - self.w[DEAD]
            self.w[DEAD] = duplicate

    def collapse(self):
        """Seen again for sure: everything but "never a person" collapses onto the room (being
        measured says where a person is, not whether this track is a person of its own)."""
        dead = self.w[DEAD]
        for k in self.w:
            self.w[k] = 0.0
        self.w[ROOM], self.w[DEAD] = 1.0 - dead, dead
        self.t_in.clear()
        self.blind.clear()
        self.visited.clear()

    def to_dict(self) -> dict:
        return {k: round(v, 3) for k, v in self.w.items() if v >= 0.005}
