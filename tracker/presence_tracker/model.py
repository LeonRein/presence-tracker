"""The configuration: map, sensors, zones and tracker parameters, stored as one JSON file.

Its meaning is the core's (docs/rust-core.md 1): it checks a configuration and brings it into its one form
(presence_core.canonical: what it leaves out filled in with the core's values, presence_core.config_defaults,
which the web UI's editor also gives every new element). The app stores exactly that form, hands it back to the
web UI and reads the file through the core again: there is no second version, and no value here. Python reads
of it only what it connects: the sensors' ids (MQTT topics). The web UI's own parts (the background image, the
rooms' label points) the core keeps as they came and the model never reads (model_part).

All lengths in meters, angles in degrees, in the house frame (the axes of the Dobby map).
"""

import copy
import json
import math
import pathlib

import presence_core

from .util import atomic_write


def problem(e: presence_core.ConfigError) -> dict:
    """The core's problem with a configuration (ConfigError.args[0], a ConfigProblem) for the web UI, which words
    it (static/js/util.js problemText): part, name, field, reason, limits ([low, high, low excluded, high
    excluded] or None), value."""
    p = e.args[0]
    return {"part": p.part, "name": p.name, "field": p.field, "reason": p.reason,
            "limits": None if p.limits is None else list(p.limits), "value": p.value}


def _plain(v):
    """A configuration for the core's JSON: a number that is no number (NaN, inf; JSON has none) as its name."""
    if isinstance(v, float) and not math.isfinite(v):
        return repr(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def model_part(d: dict) -> dict:
    """What of a configuration the model reads: all but the background image and the rooms' label points (the
    web UI's alone)."""
    out = {k: v for k, v in d.items() if k != "background"}
    out["zones"] = [{k: v for k, v in z.items() if k != "anchor"} for z in d.get("zones", [])]
    return out


def derive(d: dict) -> dict:
    """The model's quantities of the configuration d the web UI shows (the core's: presence_core.derive): the
    plan (with the zones of the home and each door's opening), the thresholds, the numbers the editor reads
    (door_on_wall, ld2410_gate), the parameters only the outputs read."""
    return json.loads(presence_core.derive(json.dumps(model_part(d))))


def limits() -> dict:
    """The limits of the numbers for the web UI (the core's): per group key -> [low, high, low excluded,
    high excluded]."""
    return presence_core.config_limits()


def defaults() -> dict:
    """What a configuration may leave out, with the values the model takes then, and what the web UI's editor
    gives every new element (the core's): {"params", "sensor", "door", "wall"}."""
    return json.loads(presence_core.config_defaults())


class Config:
    """A configuration in the core's form (from_dict), as the dict `data`: "sensors", "zones", "walls", "doors",
    "params" (and "background", the web UI's). Nothing here gives or reads a value of it but the sensors' ids."""

    def __init__(self, data: dict):
        self.data = data

    @property
    def sensor_ids(self) -> list:
        """The sensors' ids (their MQTT names), in the configuration's order."""
        return [s["id"] for s in self.data["sensors"]]

    @classmethod
    def from_dict(cls, d) -> "Config":
        """The configuration as stored or sent, in the core's form (presence_core.canonical): checked, what it
        leaves out filled in; presence_core.ConfigError (a ValueError) with the first problem (problem(e) for
        the web UI). The map comes finished from the web UI's editor: the rooms between the walls, the doors on
        their walls. (A circle without a center passed until 0.18 and failed every tick after, also after every
        restart.)"""
        return cls(json.loads(presence_core.canonical(json.dumps(_plain(d)))))

    @classmethod
    def load(cls, path: pathlib.Path) -> "Config":
        """The stored configuration through the core (none stored: the empty one); ConfigError if the core
        refuses it."""
        return cls.from_dict(json.loads(path.read_text()) if path.exists() else {})

    def to_dict(self) -> dict:
        """The stored form, a copy (an edit of it does not change this configuration)."""
        return copy.deepcopy(self.data)

    def model_json(self) -> str:
        """The configuration as the core reads it (JSON): all but the web UI's parts."""
        return json.dumps(model_part(self.data))

    def save(self, path: pathlib.Path):
        atomic_write(path, json.dumps(self.data, indent=1, ensure_ascii=False).encode())
