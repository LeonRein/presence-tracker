"""LD2450 frames in the shell (MODEL.md 4.1): the core reads them (presence_core: the payload checked,
the frame time from the uptime, the detections and the sensor's own tracks); here what the web UI shows
of a sensor's last frame."""

import json
import math
from dataclasses import dataclass, field

import numpy as np

import presence_core

# the frame time of a board's frames from its uptime (one per board): clock(recv, uptime_ms)
SensorClock = presence_core.SensorClock


@dataclass
class Detection:
    """A target of a sensor's last frame as the core read it (the web UI, the tools)."""
    pos: np.ndarray  # house frame
    local: tuple  # raw LD2450 x, y in meters (sensor frame)
    speed: float  # radial speed on the floor, m/s, negative = approaching
    hidden: bool = False  # behind a wall, outside all rooms or at the mount: a reflection
    stale: bool = False  # coasted or frozen: the LD2450 holds a lost target, no measurement

    @classmethod
    def of(cls, d: dict) -> "Detection":
        return cls(np.array(d["pos"]), d["local"], d["speed"], d["hidden"], d["stale"])


@dataclass
class SensorRuntime:
    last_frame: float = -math.inf
    detections: list = field(default_factory=list)
    frame: dict | None = None
    frames: int = 0
    # LD2410C, for the display: present (with the app's hold time), distance on the floor
    ld_present: bool = False
    ld_distance: float = 0.0
    ld_last_present: float = -math.inf
    move_gates: list | None = None  # energy per 0.75 m gate (engineering mode)
    still_gates: list | None = None
    # LD2410C for the filter (MODEL.md 4.3): when its energies came last, and what they were
    ld_t: float = -math.inf
    ld_e: object = None
    ld_target_t: float = -math.inf  # the last frame whose LD2450 had something in sight (Background anchor)


def parse_frame(payload: bytes) -> dict:
    """A frame as MQTT brings it (presence/<id>/frame), checked before anything uses it (the core's
    check: presence_core.check_frame). ValueError (why) for anything else - a wrong payload under the
    prefix (another device, a test, a firmware error) stopped the app until 0.18."""
    try:
        frame = json.loads(payload)
    except ValueError as e:
        raise ValueError(f"no JSON ({e})") from None
    presence_core.check_frame(frame)
    return frame
