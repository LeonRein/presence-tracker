"""Zone states: what goes to Home Assistant (computed by Tracker.zone_states)."""

from dataclasses import dataclass


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
    probability: float | None = None  # P(somebody is in there), where the filter knows it
    decided: bool | None = None  # occupied by the decision on the probability (MODEL.md 6)

    @property
    def occupied(self) -> bool:
        return self.decided if self.decided is not None else self.count > 0

    def to_dict(self) -> dict:
        # in steps (5 %, 0.1 s, 0.1 m): Home Assistant gets a new state only now and then; who
        # enters only while it is worth mentioning
        near = self.p_enter is not None and self.p_enter >= 0.05
        return {"count": self.count, "occupied": self.occupied, "moving": self.moving,
                "still": self.still, "approaching": self.approaching,
                "p_enter": None if self.p_enter is None else round(self.p_enter * 20) / 20,
                "eta": round(self.eta, 1) if near and self.eta is not None else None,
                "distance": round(self.distance, 1) if near and self.distance is not None else None,
                "person": self.person if near else None,
                **({} if self.probability is None else {"probability": round(self.probability * 20) / 20})}
