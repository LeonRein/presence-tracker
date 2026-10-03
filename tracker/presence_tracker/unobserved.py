"""How long people stay in rooms without a sensor (balcony, kitchen, the hallway with the stairs).

S(t): share of visits to a region that last longer than t: the learned durations of visits
that ended with a seen return, plus a wide log-normal prior that counts like a few visits.
Taken straight from the data, not fitted: fetching something and cooking are different kinds
of visits, and a single fitted curve would declare the cook gone after a few minutes.

The tracker's whereabouts (whereabouts.py) use the hazard, the share of visits of a given age
that end in the next dt, to let a "went into that room" hypothesis fade.
"""

import math

MAX_SAMPLES = 300


class Dwell:
    def __init__(self, params, open_regions=()):
        self.p = params
        self.open = set(open_regions)  # regions with the bedroom or the way out: stays of hours
        self.dwell: dict[str, list[float]] = {}  # learned visit durations per region, s
        self.changed = False  # dwell has new data (for saving)

    def survival(self, region: str, dt: float) -> float:
        """Share of visits that last longer than dt."""
        if dt <= 0:
            return 1.0
        p = self.p
        median, spread = (p.dwell_median_open, p.dwell_spread_open) if region in self.open else (p.dwell_median, p.dwell_spread)
        prior = 0.5 * math.erfc((math.log(dt) - math.log(median)) / (spread * math.sqrt(2)))
        samples = self.dwell.get(region, [])
        longer = sum(d > dt for d in samples)
        return (p.dwell_prior_weight * prior + longer) / (p.dwell_prior_weight + len(samples))

    def hazard(self, region: str, age: float, dt: float) -> float:
        """Share of the visits that lasted `age` so far and end within the next dt."""
        s0 = self.survival(region, age)
        if s0 <= 1e-9:
            return 1.0
        return min(max(1.0 - self.survival(region, age + dt) / s0, 0.0), 1.0)

    def learn(self, region: str, duration: float):
        samples = self.dwell.setdefault(region, [])
        samples.append(round(max(duration, 1.0), 1))
        del samples[:-MAX_SAMPLES]
        self.changed = True

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
