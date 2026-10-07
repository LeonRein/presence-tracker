"""Zone states: what goes to Home Assistant (computed by Tracker.zone_states)."""

from dataclasses import dataclass


def _q(x: float | None, step: float):
    return None if x is None else round(round(x / step) * step, 2)


def _p(p: float, c: float | None) -> float:
    """A probability in 5-% steps, near its threshold c (within 0.1) in 1-% steps."""
    if c is not None and abs(p - c) < 0.1:
        return round(p, 2)
    return round(round(p * 20) / 20, 2)


@dataclass
class ZoneState:
    count: int = 0
    moving: int = 0
    still: int = 0
    approaching: bool = False  # p_enter above its threshold (MODEL.md 6)
    p_enter: float | None = None  # P(somebody walking enters the zone within the look-ahead)
    eta: float | None = None  # s until the one who most probably enters is in, at their speed
    distance: float | None = None  # m they still have to walk
    person: int | None = None  # their id in the display
    c_enter: float | None = None  # threshold of p_enter
    # "Ziel" (MODEL.md 6): somebody walking goes here next - the walkers' motion and the learned map
    target: bool = False
    p_target: float | None = None  # the probability that decides (on at c_target)
    c_target: float | None = None
    target_weight: float = 0.0  # weight of the learned map against the motion (0: the motion alone)
    target_walks: float = 0.0  # learned walks behind it
    target_from: str | None = None  # the room of the walker who most probably goes here
    target_distance: float | None = None  # m from them to the door
    target_eta: float | None = None  # s at their speed
    target_person: int | None = None  # their id in the display
    probability: float | None = None  # P(somebody is in there), where the filter knows it
    decided: bool | None = None  # occupied by the decision on the probability (MODEL.md 6)

    @property
    def occupied(self) -> bool:
        return self.decided if self.decided is not None else self.count > 0

    def to_dict(self) -> dict:
        # coarse, so that Home Assistant records a new row only now and then (every change of an
        # attribute is one): probabilities in 5-% steps (1 % near the threshold), times in 0.5 s,
        # distances in 0.5 m; with nobody about to come (off, the probability below half its
        # threshold) one steady payload: 0 and the rest empty
        enter = self.p_enter is not None and (self.approaching or self.p_enter >= 0.5 * (self.c_enter or 0.0)
                                               and self.p_enter >= 0.01)
        tgt = self.p_target is not None and (self.target or self.p_target >= 0.5 * (self.c_target or 0.0)
                                              and self.p_target >= 0.01)
        return {"count": self.count, "occupied": self.occupied, "moving": self.moving,
                "still": self.still, "approaching": self.approaching,
                "p_enter": None if self.p_enter is None else _p(self.p_enter, self.c_enter) if enter else 0.0,
                "eta": _q(self.eta, 0.5) if enter else None,
                "distance": _q(self.distance, 0.5) if enter else None,
                "person": self.person if enter else None,
                "target": self.target,
                "p_target": None if self.p_target is None else _p(self.p_target, self.c_target) if tgt else 0.0,
                "target_from": self.target_from if tgt else None,
                "target_distance": _q(self.target_distance, 0.5) if tgt else None,
                "target_eta": _q(self.target_eta, 0.5) if tgt else None,
                "target_person": self.target_person if tgt else None,
                "target_walks": round(self.target_walks) if tgt else None,
                "target_source": (("karte" if self.target_weight >= 0.5 else "bewegung") if tgt else None),
                "target_weight": _q(self.target_weight, 0.1) if tgt else None,
                **({} if self.probability is None else {"probability": _p(self.probability, None)})}
