"""How long people stay in rooms without a sensor (balcony, bedroom, a hallway without one),
MODEL.md 3.3.

S(t): share of visits to a region that last longer than t, log-normal with an assumed median and
spread (wide: fetching something and cooking are different kinds of visits). One form for every
region: the outside (the stairwell, MODEL.md 2) is no region, so no region holds the hours of
somebody who left. Not learned: what a forward filter knows about when somebody went in and came
out is a probability, and counting its verdicts as durations would learn its own errors; the values
are to be estimated over the evidence (MODEL.md 7).
"""

import math


class Dwell:
    def __init__(self, params):
        self.p = params

    def survival(self, region: str, dt: float) -> float:
        """Share of visits that last longer than dt."""
        if dt <= 0:
            return 1.0
        p = self.p
        return 0.5 * math.erfc((math.log(dt) - math.log(p.dwell_median)) / (p.dwell_spread * math.sqrt(2)))

    def _quantile(self, region: str, share: float) -> float:
        """Duration that this share of visits stays below."""
        p = self.p
        z = math.sqrt(2) * _erfinv(2 * share - 1)
        return p.dwell_median * math.exp(p.dwell_spread * z)

    def stats(self, region: str) -> dict:
        return {"median": round(self._quantile(region, 0.5)), "p90": round(self._quantile(region, 0.9))}


def _erfinv(y: float) -> float:
    """Inverse error function (Newton on math.erf)."""
    x = 0.0
    for _ in range(60):
        x -= (math.erf(x) - y) / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x
