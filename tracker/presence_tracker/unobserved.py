"""People in closed rooms without a sensor (balcony, kitchen with one door).

Nobody sees them in there; we only see them go through the door. Instead of a hard counter, every
visit is a hypothesis whose probability fades with time. Three cases for a visit:

  never went in     the track was just lost at the door             1 - p0
  still inside      went in and stays longer than t so far          p0 * S(t)
  came out unseen   went in, came out, and we missed the return     p0 * (1 - S(t)) * m

Given that no return was seen, P(inside) = p0 S / (1 - p0 + p0 (S + (1 - S) m)).

p0: how sure it was that the person went in (walking toward the door or not).
S(t): share of visits to this room that last longer than t: the learned visit durations (those
     that ended with a return), plus a wide log-normal prior that counts like a few visits. Taken
     straight from the data, not fitted: fetching something and cooking are different kinds of
     visits, and a single fitted curve would declare the cook gone after a few minutes.
m:   chance to miss a return. Small while a sensor watches the door, 1 while none does (e.g. the
     sensor went offline): for a visit, m grows with the share of its time the door was unwatched.
"""

import math
from dataclasses import dataclass

MAX_SAMPLES = 300


@dataclass
class Visit:
    t_in: float
    p0: float
    blind: float = 0.0  # seconds since t_in in which no sensor watched the door


class Occupancy:
    def __init__(self, params):
        self.p = params
        self.visits: dict[str, list[Visit]] = {}
        self.dwell: dict[str, list[float]] = {}  # learned visit durations per region, s
        self.changed = False  # dwell has new data (for saving)

    # ------------------------------------------------------------ the model

    def survival(self, region: str, dt: float) -> float:
        """Share of visits that last longer than dt."""
        if dt <= 0:
            return 1.0
        p = self.p
        prior = 0.5 * math.erfc((math.log(dt) - math.log(p.dwell_median)) / (p.dwell_spread * math.sqrt(2)))
        samples = self.dwell.get(region, [])
        longer = sum(d > dt for d in samples)
        return (p.dwell_prior_weight * prior + longer) / (p.dwell_prior_weight + len(samples))

    def prob(self, region: str, v: Visit, t: float) -> float:
        dt = max(t - v.t_in, 0.0)
        s = self.survival(region, dt)
        m = self.p.missed_return
        if dt > 0:
            m += (1 - m) * min(v.blind / dt, 1.0)
        return v.p0 * s / (1 - v.p0 + v.p0 * (s + (1 - s) * m))

    def probs(self, region: str, t: float) -> list:
        return [self.prob(region, v, t) for v in self.visits.get(region, [])]

    def best(self, region: str, t: float) -> float:
        return max(self.probs(region, t), default=0.0)

    # ------------------------------------------------------------- events

    def went_in(self, region: str, t: float, p0: float):
        self.visits.setdefault(region, []).append(Visit(t, p0))

    def came_back(self, region: str, t: float):
        """Someone came out: the most probable visit ends; its duration is learned."""
        visits = self.visits.get(region)
        if not visits:
            return
        v = max(visits, key=lambda v: self.prob(region, v, t))
        visits.remove(v)
        samples = self.dwell.setdefault(region, [])
        samples.append(round(t - v.t_in, 1))
        del samples[:-MAX_SAMPLES]
        self.changed = True

    def undo_last(self, region: str):
        """The track that 'went in' was picked up again right away: it was only lost at the door."""
        visits = self.visits.get(region)
        if visits:
            visits.pop()

    def step(self, t: float, dt: float, watched: dict):
        """watched: region -> a sensor that is online sees its door. Forgets faded visits."""
        for region, visits in self.visits.items():
            if not watched.get(region, True):
                for v in visits:
                    v.blind += dt
            visits[:] = [v for v in visits if self.prob(region, v, t) >= self.p.forget_prob]

    # ------------------------------------------------------------- output

    def _quantile(self, region: str, share: float) -> float:
        """Duration that this share of visits stays below."""
        lo, hi = 0.0, math.log(24 * 3600.0)
        for _ in range(40):
            mid = (lo + hi) / 2
            if 1 - self.survival(region, math.exp(mid)) < share:
                lo = mid
            else:
                hi = mid
        return math.exp(hi)

    def stats(self, region: str) -> dict:
        return {"visits": len(self.dwell.get(region, [])), "median": round(self._quantile(region, 0.5)),
                "p90": round(self._quantile(region, 0.9))}
