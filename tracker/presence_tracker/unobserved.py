"""How long people stay in rooms without a sensor (balcony, kitchen, the hallway with the stairs),
MODEL.md 3.3.

S(t): share of visits to a region that last longer than t, log-normal with an assumed median and
spread (wide: fetching something and cooking are different kinds of visits). Separate values for
open regions (the way out, often the bedroom: hours are normal). Not learned: what a forward filter
knows about when somebody went in and came out is a probability, and counting its verdicts as
durations would learn its own errors; the values are to be estimated over the evidence (MODEL.md 7).
"""

import math


class Dwell:
    def __init__(self, params, open_regions=()):
        self.p = params
        self.open = set(open_regions)  # regions with the bedroom or the way out: stays of hours

    def survival(self, region: str, dt: float) -> float:
        """Share of visits that last longer than dt."""
        if dt <= 0:
            return 1.0
        p = self.p
        median, spread = (p.dwell_median_open, p.dwell_spread_open) if region in self.open else (p.dwell_median, p.dwell_spread)
        return 0.5 * math.erfc((math.log(dt) - math.log(median)) / (spread * math.sqrt(2)))

    def _quantile(self, region: str, share: float) -> float:
        """Duration that this share of visits stays below."""
        p = self.p
        median, spread = (p.dwell_median_open, p.dwell_spread_open) if region in self.open else (p.dwell_median, p.dwell_spread)
        z = math.sqrt(2) * _erfinv(2 * share - 1)
        return median * math.exp(spread * z)

    def stats(self, region: str) -> dict:
        return {"median": round(self._quantile(region, 0.5)), "p90": round(self._quantile(region, 0.9))}


def _erfinv(y: float) -> float:
    """Inverse error function (Newton on math.erf)."""
    x = 0.0
    for _ in range(60):
        x -= (math.erf(x) - y) / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x
