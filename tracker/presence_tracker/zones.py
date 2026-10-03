"""Zone states from the confirmed tracks: what goes to Home Assistant."""

import math
from dataclasses import dataclass

from .model import Config
from .tracker import Track, Tracker


@dataclass
class ZoneState:
    count: int = 0
    moving: int = 0
    still: int = 0
    approaching: bool = False
    eta: float | None = None  # s until the first approaching track enters

    @property
    def occupied(self) -> bool:
        return self.count > 0

    def to_dict(self) -> dict:
        return {"count": self.count, "occupied": self.occupied, "moving": self.moving,
                "still": self.still, "approaching": self.approaching,
                "eta": None if self.eta is None else round(self.eta, 2)}


def is_moving(tr: Track, now: float, lost_after: float) -> bool:
    if tr.lost(now, lost_after):
        return False
    vx, vy = tr.velocity()
    return tr.walk_prob > 0.5 and math.hypot(vx, vy) > 0.15


def evaluate(config: Config, tracker: Tracker) -> dict:
    """Zone id -> ZoneState for room and area zones, plus "_total" for the whole house."""
    p = config.params
    now = tracker.now
    zones = [z for z in config.zones if z.kind in ("room", "area")]
    states = {z.id: ZoneState() for z in zones}
    total = ZoneState()
    for tr in tracker.confirmed():
        x, y = tr.position()
        moving = is_moving(tr, now, p.lost_after)
        total.count += 1
        total.moving += moving
        total.still += not moving
        vx, vy = tr.velocity()
        speed = math.hypot(vx, vy)
        for z in zones:
            # hysteresis: once inside, a track stays until it is clearly out
            margin = p.zone_hysteresis if z.id in tr.zones else -p.zone_hysteresis
            if z.contains(x, y, margin):
                tr.zones.add(z.id)
                st = states[z.id]
                st.count += 1
                st.moving += moving
                st.still += not moving
                continue
            tr.zones.discard(z.id)
            if not moving or speed < p.approach_min_speed:
                continue
            steps = max(1, int(p.lead_time / 0.1))
            for k in range(1, steps + 1):
                tau = p.lead_time * k / steps
                if z.contains(x + vx * tau, y + vy * tau):
                    st = states[z.id]
                    st.approaching = True
                    st.eta = tau if st.eta is None else min(st.eta, tau)
                    break
    # closed rooms without a sensor (balcony, kitchen): counted in and out at their door
    for rid, region in config.regions.items():
        n = len(tracker.region_people.get(rid, []))
        if region["open"] or not n:
            continue
        total.count += n
        total.still += n
        for room_id in region["rooms"]:
            if room_id in states and len(region["rooms"]) == 1:
                states[room_id].count += n
                states[room_id].still += n
    states["_total"] = total
    return states
