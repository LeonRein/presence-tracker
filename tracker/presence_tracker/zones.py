"""Zone states: what goes to Home Assistant (computed by Tracker.zone_states)."""

from dataclasses import dataclass


@dataclass
class ZoneState:
    count: int = 0
    moving: int = 0
    still: int = 0
    approaching: bool = False
    eta: float | None = None  # s until the first approaching track enters
    probability: float | None = None  # rooms without a sensor: someone is in there

    @property
    def occupied(self) -> bool:
        return self.count > 0

    def to_dict(self) -> dict:
        return {"count": self.count, "occupied": self.occupied, "moving": self.moving,
                "still": self.still, "approaching": self.approaching,
                "eta": None if self.eta is None else round(self.eta, 2),
                # in 5 % steps: Home Assistant gets a new state only now and then
                **({} if self.probability is None else {"probability": round(self.probability * 20) / 20})}
