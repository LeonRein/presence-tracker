"""The core builds the world itself (docs/rust-core.md 6): for every configuration at hand - those of the
tests, the repository's devdata, and private ones in the directory PT_CONFIGS - its plan, raster, tiles,
sight and LD2410C tiles, and its rule of what is a reflection on a grid of points, are what 0.38.1 built.
There they were checked against the Python tracker's build (the plan, raster and tiles exactly, the
numbers up to rounding) and its rule (model.Config.hidden); what is kept of that is a digest per
configuration (fixtures/core_build.json, core_reflections.json; the private ones' in PT_CONFIGS)."""

import hashlib
import json
import math
import os
import pathlib

import numpy as np
import pytest

import presence_core
from presence_tracker.filter import SILENT_AFTER, Tracker
from presence_tracker.model import Config

from fixture import fixture
from test_existence import _nook_config
from test_filter import flat_config
from test_frames import room_config
from test_roomseen import open_plan

PRIVATE = os.environ.get("PT_CONFIGS")


def configs():
    """(name, config, the digests it must have: (build, reflections))."""
    out = [("flat", flat_config()), ("flat entry", flat_config(entry=True)), ("room", room_config()),
           ("nook", _nook_config()), ("open plan", open_plan())]
    for path in sorted(pathlib.Path(__file__).parent.parent.glob("devdata/*.json")):
        out.append((path.name, Config.from_dict(json.loads(path.read_text()))))
    digests = [fixture("core_build.json"), fixture("core_reflections.json")]
    if PRIVATE:
        for path in sorted(pathlib.Path(PRIVATE).glob("*.json")):
            d = json.loads(path.read_text())
            if "sensors" in d and "zones" in d:
                out.append((path.name, Config.from_dict(d)))
        for k, name in enumerate(("core_build_0381.json", "core_reflections_0381.json")):
            digests[k] = {**digests[k], **json.loads((pathlib.Path(PRIVATE) / name).read_text())}
    return [(name, config, digests[0][name], digests[1][name]) for name, config in out]


def build_digest(config) -> str:
    """sha1 of what the core builds (presence_core.build_export) and of the tiles' fingerprint."""
    core = presence_core.build_export(json.dumps(config.to_dict()), "{}", float(SILENT_AFTER))
    h = hashlib.sha1()
    h.update(json.dumps({k: v for k, v in core.items() if not isinstance(v, np.ndarray) and k != "sensors"},
                        sort_keys=True, default=str).encode())
    for k in sorted(core):
        if isinstance(core[k], np.ndarray):
            h.update(k.encode() + np.ascontiguousarray(core[k], dtype=float).tobytes())
    for s in core["sensors"]:
        for k in sorted(s):
            v = s[k]
            h.update(k.encode() + (b"-" if v is None else np.ascontiguousarray(v, dtype=float).tobytes()))
    h.update(Tracker(config).tiles()["id"].encode())
    return h.hexdigest()


def reflections_digest(config) -> str:
    """sha1 of the rule of reflections (presence_core.hidden_at) per sensor on a grid of points 0.37 m
    apart around the rooms, each looked at from the sensor."""
    rooms = config.zones_of("room")
    if not rooms or not config.sensors:
        return ""
    x0, y0 = min(z.geometry.bounds()[0] for z in rooms) - 1, min(z.geometry.bounds()[1] for z in rooms) - 1
    x1, y1 = max(z.geometry.bounds()[2] for z in rooms) + 1, max(z.geometry.bounds()[3] for z in rooms) + 1
    points = [(x, y) for x in np.arange(x0, x1, 0.37) for y in np.arange(y0, y1, 0.37)]
    h = hashlib.sha1()
    for si, s in enumerate(config.sensors):
        us = []
        for x, y in points:
            d = math.hypot(x - s.x, y - s.y)
            us.append(((x - s.x) / d, (y - s.y) / d) if d > 0 else (1.0, 0.0))
        got = presence_core.hidden_at(json.dumps(config.to_dict()), si, points, us, config.params.wall_margin)
        h.update(bytes(got))
    return h.hexdigest()


@pytest.mark.parametrize("name, config, build, reflections", configs(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_core_builds_the_world_0381_built(name, config, build, reflections):
    assert build_digest(config) == build
    assert reflections_digest(config) == reflections
    # the zones of the outputs are the home's, each by its own name (an outside room among them shifts nothing)
    assert set(Tracker(config).zone_states()) - {"_total"} == {z.id for z in config.home_zones("room", "area")}
