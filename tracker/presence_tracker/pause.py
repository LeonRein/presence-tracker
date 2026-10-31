"""Lernen pausieren (MODEL.md 10, "Hintergrundaktivität"): while a switch in Home Assistant is on, the
app learns nothing - neither the LD2410C background and echo rate (4.3) nor the
destination map (6, "Ziel") or the calibration data (10). The tracking goes on as always.

What moves then is not what the learned maps are for: the vacuum robot (the LD2450s track it at 0.05-0.3
m/s for minutes), visitors, a cleaning person, a party. Node-RED switches it (e.g. vacuum not docked, until
2 min after it docked: the margin is Node-RED's, the app takes the switch as it is).

The switch is an MQTT switch (ha.py): Home Assistant publishes its commands retained on COMMAND (the
app gets the last one at every connect, also one sent while it was down), the app its state on STATE.
The pauses are intervals of the model's time (the frames' receive times): the app keeps the recent
ones with what it learned (pause.json) and puts them into an error report, the recordings keep the switch
(recording.py: the command; tools/record.py: both topics), so the replays pause where the app did.
"""

import json
import math

from .ha import PAUSE_COMMAND as COMMAND, PAUSE_STATE as STATE

TOPICS = (COMMAND, STATE)


def parse(payload) -> bool | None:
    """ON / OFF (any case, also true / false, 1 / 0) as a bool; None for anything else."""
    if isinstance(payload, bytes):
        payload = payload.decode(errors="replace")
    if isinstance(payload, bool):
        return payload
    text = str(payload).strip().lower()
    if text in ("on", "true", "1"):
        return True
    if text in ("off", "false", "0"):
        return False
    return None


class Pauses:
    """The intervals [start, end) in which nothing is learned (end None: still paused): the switch's
    (intervals) and, for the offline tools, ones known beforehand (known, add)."""

    def __init__(self, intervals=None):
        self.intervals = [[float(s), None if e is None else float(e)] for s, e in (intervals or [])]
        self.intervals.sort(key=lambda iv: iv[0])
        self.known = []
        self.changes = 0  # counts the changes (the tracker hands the pauses to its core after each)

    @property
    def on(self) -> bool:
        return bool(self.intervals) and self.intervals[-1][1] is None

    @property
    def since(self) -> float | None:
        """When the running pause began (None: none running)."""
        return self.intervals[-1][0] if self.on else None

    def set(self, on: bool, t: float) -> bool:
        """The switch at time t; True if that changed anything."""
        if on == self.on:
            return False
        if on:
            self.intervals.append([float(t), None])
        else:
            start = self.intervals[-1][0]
            self.intervals[-1][1] = max(float(t), start)
        self.changes += 1
        return True

    def add(self, start: float, end: float | None):
        """An interval known beforehand (tools: the vacuum's history, before the switch existed); the
        switch's are kept apart. Overlapping ones may stay so: the core pauses in any of them."""
        self.known.append([float(start), None if end is None else float(end)])
        self.changes += 1

    def between(self, t0: float, t1: float = math.inf) -> list:
        """The intervals that reach into [t0, t1], as [[start, end or None], ...]."""
        return sorted(([s, e] for s, e in self.intervals + self.known if s <= t1 and (e is None or e > t0)),
                      key=lambda iv: iv[0])

    def prune(self, before: float):
        """Forget the intervals that ended before this."""
        self.intervals = [iv for iv in self.intervals if iv[1] is None or iv[1] >= before]
        self.changes += 1

    def to_dict(self) -> dict:
        return {"intervals": [[s, e] for s, e in self.intervals]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Pauses":
        return cls((d or {}).get("intervals", []))


# ------------------------------------------------------------------ the offline tools

def load(path) -> Pauses:
    """Pauses known beforehand from a file {"intervals": [[start, end or None], ...]} (Unix time), e.g.
    the vacuum's history before the switch existed (tools/vacuum_history.py); no path: none."""
    p = Pauses()
    if path:
        with open(path) as f:
            for s, e in json.load(f).get("intervals", []):
                p.add(s, e)
    return p


def follow(pauses: Pauses, message: dict) -> bool:
    """A recorded message ({"t", "topic", "payload"}, tools/record.py): if it is the switch's (command
    or state), pauses follows it (as the app did) and True."""
    if message.get("topic") not in TOPICS:
        return False
    on = parse(message.get("payload"))
    if on is not None:
        pauses.set(on, float(message["t"]))
    return True
