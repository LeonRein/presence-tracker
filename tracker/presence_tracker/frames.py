"""LD2450 frames in the shell (MODEL.md 4.1): the core reads them (presence_core: the payload checked once
into a Frame, the frame time from the uptime, the detections and the sensor's own tracks); here what the
web UI shows of a sensor's last frame."""

import math
from dataclasses import dataclass, field

import numpy as np

import presence_core

# a frame checked against what the firmware sends: Frame.parse(payload), Frame.of(dict) (ValueError why)
Frame = presence_core.Frame
# the frame time of a board's frames from its uptime (one per board): clock(recv, frame)
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
    frames: int = 0
    # LD2410C, for the display: present (with the app's hold time), distance on the floor
    ld_present: bool = False
    ld_distance: float = 0.0
    ld_last_present: float = -math.inf
    move_gates: list | None = None  # energy per 0.75 m gate (engineering mode)
    still_gates: list | None = None

