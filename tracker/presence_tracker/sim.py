"""Simulated sensors and people, for tests and a demo mode without hardware.

Imitates the MQTT frames of the ESPHome firmware, including the LD2450's quirks:
a lost target coasted for 15 frames with its last speed (MODEL.md 4.1), noise, an error that wanders slowly (each sensor sees a person a bit elsewhere), at most 3 targets, still people dropping out, occasional ghosts, and the LD2410C
as MODEL.md 4.3 describes it: its energies per gate Gamma-distributed about the background plus what
the people in its beam put in, correlated over the time the filter assumes, capped at 100; its
flags the firmware's thresholds on them. Like the firmware, a sensor with nothing to report (no
target, both flags off) sends only a heartbeat every 5 s (MODEL.md 4.4).
"""

import math
import random
from dataclasses import dataclass, field

import numpy as np

from . import ld2410
from .filtermodel import Model
from .model import SensorConfig

# the firmware's default thresholds (moving gates 0-8, still gates 2-8)
THRESHOLDS = np.array([50, 50, 40, 30, 20, 15, 15, 15, 15, 40, 40, 30, 30, 20, 20, 20], dtype=float)


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
    speed_noise: float = 0.03  # m/s: the reported speed varies from frame to frame (three equal ones mean coasting)
    bias: float = 0.0  # m: per person, where this sensor's target sits on them wanders by about this much
    bias_time: float = 1.0  # s, ... this slowly
    still_dropout: float = 0.0  # chance per second that a still person is dropped (stays dropped for still_gap s)
    still_gap: float = 20.0
    ghost_rate: float = 0.0  # LD2450 ghosts per minute
    reflections: tuple = ()  # ((t0, t1, x, y), ...): a fixed reflection its LD2450 reports then (not the LD2410C)
    ld2410: bool = True  # the LD2410C's energies in the frames
    resolution: float = 0.0  # m, people closer than this to each other come out as one target
    blind_to: tuple = ()  # indices of people this sensor doesn't see from blind_after on (hidden behind someone)
    blind_after: float = 0.0
    blind_until: float = math.inf  # ... until then
    ld_background: object = None  # (16,) what its LD2410C sees without anybody; None: the model's prior
    idle: float = 5.0  # s: without anything to report (no target, both LD2410C flags off) the firmware sends
                       # only a heartbeat this often, the first such frame at once (0: every frame)
    seq: int = 0
    _sent: tuple = (-math.inf, True)  # when the last frame was sent, and whether it was empty
    _dropped: dict = field(default_factory=dict)
    _ghost_until: float = -1.0
    _ghost_pos: tuple = (0.0, 0.0)
    _bias: dict = field(default_factory=dict)
    _coast: dict = field(default_factory=dict)  # person -> (local x, y, vx, vy, raw speed, frames left)
    _ld_block: tuple = (-1.0, 1.0, 1.0)  # until when, the common gain of the moving / still cells
    _ld_echo: tuple = (-1.0, None)  # an echo source: until when, what it puts in (MODEL.md 4.3)
    _ld_lag: object = None  # what the people put into the still cells lately (they lag)
    _ld_gain: list | None = None  # per cell: the Ornstein-Uhlenbeck processes of its energy's Gamma factor


def simulate(people: list, sensors: list, duration: float, rate: float = 11.0, seed: int = 1, walls: list = ()):
    """Yield (t, sensor_id, frame) in time order, as the firmware sends them (SimSensor.idle). walls:
    sight-blocking segments (Config.wall_segments)."""
    rng = random.Random(seed)
    dt = 1 / rate
    t = 0.0
    while t < duration:
        for k, s in enumerate(sensors):
            ts = t + k * dt / len(sensors)
            frame = _frame(s, people, ts, dt, rng, walls)
            ld = frame["ld2410"]
            empty = not frame["targets"] and not ld.get("moving") and not ld.get("still")
            if empty and s._sent[1] and ts - s._sent[0] < s.idle:
                continue
            s._sent = (ts, empty)
            frame["seq"] = s.seq
            s.seq += 1
            yield ts, s.config.id, frame
        t += dt


def _frame(s: SimSensor, people: list, t: float, dt: float, rng: random.Random, walls) -> dict:
    c = s.config
    targets = []
    for i, person in enumerate(people):
        if i in s.blind_to and s.blind_after <= t < s.blind_until:
            continue
        pos = person.position(t)
        if pos is None or not c.sees(pos[0], pos[1], walls):
            # lost: the LD2450 coasts the target with its last speed, slowing down, for 15 frames
            co = s._coast.get(i)
            if co is not None and co[5] > 0:
                lx, ly, vx_, vy_, raw, left = co
                lx, ly = lx + vx_ * dt, ly + vy_ * dt
                s._coast[i] = (lx, ly, 0.8 * vx_, 0.8 * vy_, raw, left - 1)
                targets.append((math.hypot(lx, ly), {"x": round(lx * 1000), "y": round(ly * 1000), "speed": raw,
                                                     "resolution": 360}))
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
        if s.bias:
            a = math.exp(-dt / s.bias_time)
            bx, by = s._bias.get(i, (rng.gauss(0, s.bias), rng.gauss(0, s.bias)))
            q = s.bias * math.sqrt(1 - a * a)
            bx, by = a * bx + rng.gauss(0, q), a * by + rng.gauss(0, q)
            s._bias[i] = (bx, by)
            lx, ly = lx + bx, ly + by
        ground = math.hypot(lx, ly)
        slant = math.hypot(ground, c.height - person.height)
        k = slant / ground
        ux, uy = (pos[0] - c.x) / ground, (pos[1] - c.y) / ground
        radial = vx * ux + vy * uy
        tgt = {
            "x": round((lx * k + rng.gauss(0, s.noise)) * 1000),
            "y": round((ly * k + rng.gauss(0, s.noise)) * 1000),
            "speed": round((radial * ground / slant + rng.gauss(0, s.speed_noise)) * 1000 / 10) * 10,
            "resolution": 360,
        }
        targets.append((ground, tgt))
        lvx, lvy = c.to_local(pos[0] + vx, pos[1] + vy)
        s._coast[i] = (tgt["x"] / 1000, tgt["y"] / 1000, lvx - lx, lvy - ly, tgt["speed"] or 10, 15)
    if s.ghost_rate and rng.random() < s.ghost_rate / 60 * dt:
        s._ghost_until = t + rng.uniform(0.2, 1.0)
        s._ghost_pos = (rng.uniform(-2, 2), rng.uniform(0.5, 5))
    if t < s._ghost_until:
        gx, gy = s._ghost_pos
        targets.append((math.hypot(gx, gy), {"x": round(gx * 1000), "y": round(gy * 1000), "speed": 0, "resolution": 360}))
    for t0, t1, x, y in s.reflections:
        if t0 <= t <= t1:
            lx, ly = c.to_local(x, y)
            targets.append((math.hypot(lx, ly), {"x": round((lx + rng.gauss(0, s.noise)) * 1000),
                                                 "y": round((ly + rng.gauss(0, s.noise)) * 1000),
                                                 "speed": 0, "resolution": 360}))
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

    # LD2410C (MODEL.md 4.3): energy = (background + what the people in its beam put in) x a Gamma
    # factor (mean 1, shape alpha) correlated over the time tau: a chi-square with 2 alpha degrees
    # of freedom from as many Ornstein-Uhlenbeck processes (their squares decorrelate with 2 / tau)
    m = Model()
    put = np.zeros(ld2410.CELLS)
    for person in people:
        p = person.position(t)
        if p is None or not c.sees(p[0], p[1], walls):
            continue
        lx, ly = c.to_local(*p)
        if ly <= 0:
            continue
        angle = math.degrees(math.atan2(abs(lx), ly))
        slant = math.hypot(math.hypot(lx, ly) * c.scale, c.height - person.height)
        walking = math.hypot(*person.velocity(t)) > 0.3
        s_still, s_walk = ld2410.expected(m, [angle], [slant], [True])
        put = put + (s_walk if walking else s_still)[0]
    # echo sources: begin at the model's prior rate, live Erlang(stages), the profile of a standing
    # person on the axis at a random slant, times a random amplitude
    if t >= s._ld_echo[0] and rng.random() < m.ld_echo_rate * dt:
        life = sum(rng.expovariate(m.ld_echo_stages / m.ld_echo_life) for _ in range(m.ld_echo_stages))
        lo, hi, _ = m.ld_echo_ranges
        src = ld2410.expected(m, [0.0], [rng.uniform(lo, hi)], [True])[0][0] * rng.choice(m.ld_echo_amps)
        s._ld_echo = (t + life, src)
    if t < s._ld_echo[0]:
        put = put + s._ld_echo[1]
    # the still energies follow with the time constant ld_memory, the moving ones at once
    k = math.exp(-dt / m.ld_memory)
    s._ld_lag = put if s._ld_lag is None else k * s._ld_lag + (1 - k) * put
    bg = ld2410.prior(m) if s.ld_background is None else np.asarray(s.ld_background, dtype=float)
    mu = bg + np.concatenate([put[:9], s._ld_lag[9:]])
    # each second all cells of a kind share a gain: 1/u ~ Gamma(kappa, kappa), now and then a burst
    if t >= s._ld_block[0]:
        def gain(kappa):
            bk, bu, bw = m.ld_burst
            if rng.random() < bw:
                return 1.0 / max(rng.gammavariate(bk, 1.0 / (bk * bu)), 1e-3)
            return 1.0 / max(rng.gammavariate(kappa, 1.0 / kappa), 1e-3)
        s._ld_block = (t + m.ld_every, gain(m.ld_gain[0]), gain(m.ld_gain[1]))
    mu = mu * np.concatenate([np.full(9, s._ld_block[1]), np.full(7, s._ld_block[2])])
    alpha = ld2410.cell_values(m, *m.ld_shape)
    tau = ld2410.cell_values(m, *m.ld_tau)
    if s._ld_gain is None:
        s._ld_gain = [[rng.gauss(0, 1) for _ in range(int(round(2 * a)))] for a in alpha]
    gain = []
    for j, z in enumerate(s._ld_gain):
        rho = math.exp(-dt / (2 * tau[j]))
        for i in range(len(z)):
            z[i] = rho * z[i] + math.sqrt(1 - rho * rho) * rng.gauss(0, 1)
        gain.append(sum(v * v for v in z) / len(z))
    e = np.minimum(np.round(mu * np.array(gain)), 100).astype(int)
    over = e >= THRESHOLDS
    moving, still = bool(over[:9].any()), bool(over[9:].any())
    md = int(round((int(np.argmax(e[:9])) + 0.5) * 750, -1)) if moving else 0
    sd = int(round((int(np.argmax(e[9:])) + 2.5) * 750, -1)) if still else 0
    move_gates = [int(v) for v in e[:9]]
    still_gates = [0, 0] + [int(v) for v in e[9:]]
    return {
        "seq": s.seq,
        "uptime_ms": int(t * 1000),
        "targets": targets,
        "ld2410": ({"moving": moving, "still": still, "moving_distance": md, "moving_energy": max(move_gates) if moving else 0,
                    "still_distance": sd, "still_energy": max(still_gates) if still else 0,
                    "move_gates": move_gates, "still_gates": still_gates} if s.ld2410 else {}),
    }
