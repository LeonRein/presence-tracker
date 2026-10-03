"""Simulated sensors and people, for tests and a demo mode without hardware.

Imitates the MQTT frames of the ESPHome firmware, including the LD2450's quirks:
noise, at most 3 targets, still people dropping out, occasional ghosts, and the LD2410C
seeing the LD2450's interference every ~7 s.
"""

import math
import random
from dataclasses import dataclass, field

from .model import SensorConfig


@dataclass
class Person:
    waypoints: list  # [(t, x, y), ...], linear in between; before the first / after the last: absent
    height: float = 1.0

    def position(self, t: float):
        w = self.waypoints
        if t < w[0][0] or t > w[-1][0]:
            return None
        for (t0, x0, y0), (t1, x1, y1) in zip(w, w[1:]):
            if t0 <= t <= t1:
                a = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                return x0 + a * (x1 - x0), y0 + a * (y1 - y0)
        return w[-1][1], w[-1][2]

    def velocity(self, t: float, h: float = 0.05):
        a, b = self.position(t - h), self.position(t + h)
        if a is None or b is None:
            return 0.0, 0.0
        return (b[0] - a[0]) / (2 * h), (b[1] - a[1]) / (2 * h)


@dataclass
class SimSensor:
    config: SensorConfig
    noise: float = 0.07
    still_dropout: float = 0.0  # chance per second that a still person is dropped (stays dropped for still_gap s)
    still_gap: float = 20.0
    ghost_rate: float = 0.0  # LD2450 ghosts per minute
    ld2410_ghost_period: float = 7.0
    resolution: float = 0.0  # m, people closer than this to each other come out as one target
    blind_to: tuple = ()  # indices of people this sensor doesn't see from blind_after on (hidden behind someone)
    blind_after: float = 0.0
    seq: int = 0
    _dropped: dict = field(default_factory=dict)
    _ghost_until: float = -1.0
    _ghost_pos: tuple = (0.0, 0.0)


def simulate(people: list, sensors: list, duration: float, rate: float = 11.0, seed: int = 1, walls: list = ()):
    """Yield (t, sensor_id, frame) in time order. walls: sight-blocking segments (Config.wall_segments)."""
    rng = random.Random(seed)
    dt = 1 / rate
    t = 0.0
    while t < duration:
        for k, s in enumerate(sensors):
            ts = t + k * dt / len(sensors)
            yield ts, s.config.id, _frame(s, people, ts, dt, rng, walls)
        t += dt


def _frame(s: SimSensor, people: list, t: float, dt: float, rng: random.Random, walls) -> dict:
    c = s.config
    targets = []
    for i, person in enumerate(people):
        if i in s.blind_to and t >= s.blind_after:
            continue
        pos = person.position(t)
        if pos is None or not c.sees(pos[0], pos[1], walls):
            continue
        vx, vy = person.velocity(t)
        speed = math.hypot(vx, vy)
        if speed < 0.05:
            until = s._dropped.get(i)
            if until is not None and t < until:
                continue
            if rng.random() < s.still_dropout * dt:
                s._dropped[i] = t + s.still_gap
                continue
        else:
            s._dropped.pop(i, None)
        lx, ly = c.to_local(*pos)
        ground = math.hypot(lx, ly)
        slant = math.hypot(ground, c.height - person.height)
        k = slant / ground
        ux, uy = (pos[0] - c.x) / ground, (pos[1] - c.y) / ground
        radial = vx * ux + vy * uy
        targets.append((ground, {
            "x": round((lx * k + rng.gauss(0, s.noise)) * 1000),
            "y": round((ly * k + rng.gauss(0, s.noise)) * 1000),
            "speed": round(radial * ground / slant * 1000 / 10) * 10,
            "resolution": 360,
        }))
    if s.ghost_rate and rng.random() < s.ghost_rate / 60 * dt:
        s._ghost_until = t + rng.uniform(0.2, 1.0)
        s._ghost_pos = (rng.uniform(-2, 2), rng.uniform(0.5, 5))
    if t < s._ghost_until:
        gx, gy = s._ghost_pos
        targets.append((math.hypot(gx, gy), {"x": round(gx * 1000), "y": round(gy * 1000), "speed": 0, "resolution": 360}))
    targets.sort(key=lambda item: item[0])
    if s.resolution:
        # the radar can't separate people close together: one target in between
        merged = []
        for r, tgt in targets:
            other = next((m for m in merged if math.hypot(m[1]["x"] - tgt["x"], m[1]["y"] - tgt["y"]) < s.resolution * 1000), None)
            if other is None:
                merged.append((r, tgt))
            else:
                other[1]["x"] = (other[1]["x"] + tgt["x"]) // 2
                other[1]["y"] = (other[1]["y"] + tgt["y"]) // 2
        targets = merged
    targets = [tgt for _, tgt in targets[:3]]
    for slot, tgt in enumerate(targets, 1):
        tgt["slot"] = slot

    # LD2410C: sees everyone in its field, plus the LD2450 interference every few seconds
    near = [math.hypot(*c.to_local(*p)) for p in (person.position(t) for person in people)
            if p is not None and c.sees(p[0], p[1], walls)]
    moving = still = False
    md = sd = 0
    if near:
        still = True
        sd = round(min(near) * 1000, -1)
    elif s.ld2410_ghost_period:
        # as recorded: moving 0.1 s, then moving+still 1 s (the radar's own hold time is 0)
        phase = t % s.ld2410_ghost_period
        episode = int(t // s.ld2410_ghost_period)
        dist = round(random.Random(episode).uniform(0.5, 5.0) * 1000, -1)
        moving = phase < 1.1
        still = 0.1 <= phase < 1.1
        md = dist if moving else 0
        sd = dist if still else 0
    s.seq += 1
    return {
        "seq": s.seq,
        "uptime_ms": int(t * 1000),
        "targets": targets,
        "ld2410": {"moving": moving, "still": still, "moving_distance": md, "moving_energy": 50 if moving else 0,
                   "still_distance": sd, "still_energy": 40 if still else 0},
    }
