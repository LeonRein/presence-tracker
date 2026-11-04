"""Zone states: what goes to Home Assistant (the core's, Core.zone_states, copied field for field by
Tracker.zone_states; here only how they are rounded for Home Assistant)."""

import math
from dataclasses import dataclass


def _q(x: float | None, step: float):
    return None if x is None or not math.isfinite(x) else round(round(x / step) * step, 2)


def _p(p: float) -> float | None:
    """A probability in 5-% steps (None if it is no number: never raises, app.App.tick checks)."""
    return round(round(p * 20) / 20, 2) if math.isfinite(p) else None


@dataclass
class ZoneState:
    count: int = 0
    moving: int = 0
    still: int = 0
    # "Ziel" (MODEL.md 6): somebody walking goes here next - from the learned destination map
    target: bool = False
    p_target: float | None = None  # the probability that decides (on at c_target)
    c_target: float | None = None
    target_walks: float = 0.0  # learned walks behind it
    target_from: str | None = None  # the room of the walker who most probably goes here
    target_distance: float | None = None  # m from them to the door (an area: to its nearest part)
    target_eta: float | None = None  # s at their speed
    target_person: int | None = None  # their id in the display
    probability: float | None = None  # P(somebody is in there) over all hypotheses, where the filter knows it
    # False: the room's own sensors all went silent (the core's silent.rs), Home Assistant gets "unavailable"
    # (ha.py); not in to_dict, the state goes out as always
    available: bool = True
    occupied: bool = False  # somebody counted there (MODEL.md 6)

    def finite(self) -> bool:
        """Its probabilities are numbers (a NaN shows as "nobody there": NaN > c is false)."""
        return all(p is None or math.isfinite(p) for p in (self.probability, self.p_target))

    def to_dict(self) -> dict:
        # coarse, so that Home Assistant records a new row only now and then (every change of an
        # attribute is one): probabilities in 5-% steps, times in 0.5 s, distances in 0.5 m; with
        # nobody about to come (off, the probability below half its threshold) 0 and the rest empty.
        # What Home Assistant gets is held further (ha.Discovery.steady)
        tgt = self.p_target is not None and (self.target or self.p_target >= 0.5 * (self.c_target or 0.0)
                                              and self.p_target >= 0.01)
        return {"count": self.count, "occupied": self.occupied,
                "moving": self.moving, "still": self.still,
                "target": self.target,
                "p_target": None if self.p_target is None else _p(self.p_target) if tgt else 0.0,
                "target_from": self.target_from if tgt else None,
                "target_distance": _q(self.target_distance, 0.5) if tgt else None,
                "target_eta": _q(self.target_eta, 0.5) if tgt else None,
                "target_person": self.target_person if tgt else None,
                "target_walks": round(self.target_walks) if tgt else None,
                **({} if self.probability is None else {"probability": _p(self.probability)})}
