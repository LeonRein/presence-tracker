"""Zone states from the confirmed tracks: what goes to Home Assistant."""

import math
from dataclasses import dataclass

from .model import Config
from .tracker import Track, Tracker
from .whereabouts import NOT_HOME, ROOM


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


def is_moving(tr: Track, now: float, lost_after: float) -> bool:
    if tr.lost(now, lost_after):
        return False
    vx, vy = tr.velocity()
    return tr.walk_prob > 0.5 and math.hypot(vx, vy) > 0.15


def evaluate(config: Config, tracker: Tracker) -> dict:
    """Zone id -> ZoneState for room and area zones, plus "_total" for the whole house.

    Every confirmed track is counted at its most probable whereabouts: at its position (rooms
    and areas there), or in the rooms of the region behind the door it went through. The
    probability of a zone is 1 - prod(1 - mass) over all tracks' mass in it."""
    p = config.params
    now = tracker.now
    zones = [z for z in config.zones if z.kind in ("room", "area")]
    states = {z.id: ZoneState() for z in zones}
    total = ZoneState()
    not_in = {z.id: 1.0 for z in zones}  # product of (1 - mass)
    for tr in tracker.confirmed():
        where = tr.where
        best = tr.place()
        if best in NOT_HOME:
            continue
        w = where.view(tr.real) if where is not None else {ROOM: 1.0}
        x, y = tr.position()
        room_mass = w.get(ROOM, 0.0)
        for z in zones:
            if z.contains(x, y) and room_mass > 0:
                not_in[z.id] *= 1 - room_mass
        if where is not None:
            for rid, mass in ((k, v) for k, v in w.items() if k in where.regions()):
                for room_id in config.regions.get(rid, {}).get("rooms", []):
                    if room_id in not_in:
                        not_in[room_id] *= 1 - mass
        if best != ROOM:
            total.count += 1
            total.still += 1
            rooms = config.regions.get(best, {}).get("rooms", [])
            if len(rooms) == 1 and rooms[0] in states:
                states[rooms[0]].count += 1
                states[rooms[0]].still += 1
            continue
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
    for zid, q in not_in.items():
        if q < 1.0:
            states[zid].probability = 1 - q
    states["_total"] = total
    return states
