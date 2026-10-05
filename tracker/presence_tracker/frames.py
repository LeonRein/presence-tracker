"""From an LD2450 frame to detections in the house frame (MODEL.md 4.1, preprocessing): the
targets turned into floor positions with their measurement error, and marked where they can't be a
person: behind a wall, in an interference zone, frozen (bit-identical repeats)."""

import math
from dataclasses import dataclass, field

import numpy as np

from .model import Config, SensorConfig


@dataclass
class Detection:
    sensor: str
    slot: int
    pos: np.ndarray  # house frame
    R: np.ndarray
    radial: np.ndarray  # unit vector sensor -> target on the floor
    speed: float  # radial speed on the floor, m/s, negative = approaching
    local: tuple  # raw LD2450 x, y in meters (sensor frame), for calibration
    ignored: bool = False  # inside an interference zone
    hidden: bool = False  # behind a wall or outside all rooms: a reflection
    stale: bool = False  # frozen or held: the LD2450 repeats itself, nothing new about the spot


@dataclass
class SensorRuntime:
    last_frame: float = -math.inf
    detections: list = field(default_factory=list)
    frame: dict | None = None
    frames: int = 0
    repeats: dict = field(default_factory=dict)  # slot -> ((x, y) in mm, frames in a row)
    # LD2410C, for the display: present (with the app's hold time), distance on the floor
    ld_present: bool = False
    ld_distance: float = 0.0
    ld_last_present: float = -math.inf
    move_gates: list | None = None  # energy per 0.75 m gate (engineering mode)
    still_gates: list | None = None


class SensorClock:
    """Frame time from the sensor's uptime, anchored to the receive time.

    The receive time jitters with WiFi; the uptime does not. The offset follows the fastest
    delivery seen (smallest receive - uptime) and creeps up by 1 ms per second so that it
    follows clock drift and a broker that got slower. Resets on a reboot or a jump.
    """

    def __init__(self):
        self.offset = None
        self.last_up = None

    def __call__(self, recv: float, uptime_ms: int | None) -> float:
        if uptime_ms is None:
            return recv
        up = uptime_ms / 1000
        candidate = recv - up
        if self.offset is None or up < self.last_up or abs(candidate - self.offset) > 2.0:
            self.offset = candidate
        else:
            self.offset = min(candidate, self.offset + 0.001 * (up - self.last_up))
        self.last_up = up
        return up + self.offset


def detections(config: Config, s: SensorConfig, frame: dict) -> list:
    p = config.params
    out = []
    for target in frame.get("targets", []):
        lx, ly = target["x"] / 1000, target["y"] / 1000
        if ly <= 0:
            continue
        wx, wy, ground, slant = s.to_world(lx, ly, p.target_height)
        dx, dy = wx - s.x, wy - s.y
        u = np.array([dx, dy]) / max(ground, 1e-3)
        # measurement error: along the line of sight and across it, both growing with the
        # distance (across faster: an angle error), worse toward the edge of the view. Not the
        # tiny frame-to-frame jitter (1-5 cm), but the real offset of 15-30 cm at 3-5 m
        # (which part of the body reflects, angle bias, calibration).
        az = math.degrees(math.atan2(lx, ly))
        sigma_r = p.range_sigma_base + p.range_sigma_slope * ground
        sigma_t = (p.lateral_sigma_base + p.lateral_sigma_slope * ground) * (1 + 0.5 * (az / 60) ** 2)
        rot = np.array([[u[0], -u[1]], [u[1], u[0]]])
        R = rot @ np.diag([sigma_r**2, sigma_t**2]) @ rot.T
        speed = target.get("speed", 0) / 1000 * (slant / ground if ground > 0 else 1)
        pos = np.array([wx, wy])
        ignored = any(z.contains(wx, wy) for z in config.zones_of("ignore"))
        out.append(Detection(s.id, target.get("slot", 0), pos, R, u, speed, (lx, ly), ignored,
                             config.hidden(s, pos, u, p.wall_margin)))
    return out


def mark_stale(config: Config, rt: SensorRuntime, frame: dict, dets: list):
    """The LD2450 sometimes keeps reporting a target with bit-identical coordinates for up to
    ~35 s after the person left. A real person, even sitting still, changes the millimeter
    values in nearly every frame (99 % of identical runs are at most 2 frames long)."""
    repeats = {}
    by_slot = {d.slot: d for d in dets}
    for target in frame.get("targets", []):
        slot, xy = target.get("slot", 0), (target["x"], target["y"])
        last = rt.repeats.get(slot)
        n = last[1] + 1 if last and last[0] == xy else 1
        repeats[slot] = (xy, n)
        if n >= config.params.stale_frames and slot in by_slot:
            by_slot[slot].stale = True
    rt.repeats = repeats
