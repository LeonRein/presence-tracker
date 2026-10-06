"""The LD2450's own tracks (MODEL.md 4.1): the sensor is a tracker itself, and what it reports is
decoded here into its tracks - no inference about the world, only about the format.

- The three slots are compacted (when slot 1 ends, slot 2 moves up), so a slot number is no track
  id. The positions of one target are continuous from frame to frame (99 % move less than 0.1 m),
  so targets are linked to the previous frame's by proximity in the sensor's own coordinates.
- When the LD2450 loses its target it keeps the track: a moving one is coasted with its last
  speed (slowing down smoothly), a still one is repeated bit-identically (frozen). Measured
  (5.10., 16 h): this happens 0.5 (still) to 0.9 (walking) times per second; 97-98 % of these
  phases end with the target found again, mostly within 0.1-0.3 s; otherwise the track expires
  after 1.25 s (coasting) or 35.5 s (frozen). Coasted and frozen frames are no measurement.
- Targets behind a wall, outside all rooms or at the mount (Detection.hidden) carry no
  information; a track is born at its first visible measured frame.

Per frame `update` returns the events of this sensor's tracks: born, measured (continued or found
again), lost (coasting or frozen from now on, with the kind and the held position), held (still
lost, the held position), ended (the target is gone from the frames).
"""

import math
from dataclasses import dataclass, field

GATE = 0.6  # m between two frames of one target in the sensor's coordinates (6.7 m/s at 11 Hz)
COAST_RUN = 3  # frames with the same nonzero raw speed: the sensor is coasting a lost target
LOST = 6.0  # s without a frame: data lost, every track is over (the idle heartbeat is 5 s)
COAST, FROZEN = "coast", "frozen"


@dataclass
class Raw:
    """One target of the sensor as it reports it, linked from frame to frame."""
    xy: tuple  # raw mm, for the bit-identical test
    local: tuple  # m, sensor frame
    speed: int  # raw
    run: int = 1  # frames in a row with this nonzero speed
    seg: int | None = None  # track id once born (visible and measured)
    lost: str | None = None  # COAST / FROZEN while the sensor holds it without measuring


@dataclass
class Events:
    born: list = field(default_factory=list)  # (seg, det)
    measured: list = field(default_factory=list)  # (seg, det), continued or found again
    lost: list = field(default_factory=list)  # (seg, kind, det)
    held: list = field(default_factory=list)  # (seg, det): still lost
    ended: list = field(default_factory=list)  # seg
    data_lost: bool = False


class SensorTracks:
    def __init__(self, sensor_id: str, ids):
        self.sensor = sensor_id
        self.ids = ids  # shared counter: track ids are unique over all sensors
        self.raw: list[Raw] = []
        self.last = -math.inf

    def update(self, t: float, frame: dict, detections: list) -> Events:
        """detections: frames.detections() of this frame (with .slot, .local, .hidden)."""
        ev = Events()
        by_slot = {d.slot: d for d in detections}
        targets = [(tg, by_slot[tg.get("slot", 0)]) for tg in frame.get("targets", []) if tg.get("slot", 0) in by_slot]
        if t - self.last > LOST:
            ev.ended += [r.seg for r in self.raw if r.seg is not None]
            ev.data_lost = bool(self.raw) and self.last > -math.inf
            self.raw = []
        self.last = t
        pairs = sorted((math.hypot(r.local[0] - d.local[0], r.local[1] - d.local[1]), i, j)
                       for i, r in enumerate(self.raw) for j, (_, d) in enumerate(targets))
        used_r, used_t, match = set(), set(), {}
        for dist, i, j in pairs:
            if dist > GATE or i in used_r or j in used_t:
                continue
            used_r.add(i)
            used_t.add(j)
            match[j] = i
        for i, r in enumerate(self.raw):
            if i not in used_r and r.seg is not None:
                ev.ended.append(r.seg)
        raw = []
        for j, (tg, d) in enumerate(targets):
            xy, speed = (tg["x"], tg["y"]), tg.get("speed", 0)
            if j in match:
                r = self.raw[match[j]]
                frozen = xy == r.xy
                r.run = r.run + 1 if speed == r.speed and speed != 0 else 1
                kind = FROZEN if frozen else COAST if r.run >= COAST_RUN else None
                r.xy, r.local, r.speed = xy, d.local, speed
            else:
                r = Raw(xy, d.local, speed)
                kind = None
            raw.append(r)
            if r.seg is None:
                if kind is None and not d.hidden:
                    r.seg = next(self.ids)
                    ev.born.append((r.seg, d))
                continue
            if kind is not None:
                if r.lost is None:
                    ev.lost.append((r.seg, kind, d))
                else:
                    ev.held.append((r.seg, d))
                r.lost = kind
            else:
                r.lost = None
                if not d.hidden:
                    ev.measured.append((r.seg, d))
        self.raw = raw
        return ev
