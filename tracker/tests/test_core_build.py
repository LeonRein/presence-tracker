"""The core builds the world itself (docs/rust-core.md 6): for every configuration at hand - those of the
tests, and private ones in the directory PT_CONFIGS - its plan, raster, tiles, sight and LD2410C tiles, and
its rule of what is a reflection on a grid of points, are what 0.38.1 built. There they were checked against
the Python tracker's build (the plan, raster and tiles exactly, the numbers up to rounding) and its rule
(model.Config.hidden); what is kept of that is a digest per part and configuration (fixtures/core_build.json,
core_reflections.json, made by fixtures/make/; the private ones' in PT_CONFIGS). The plan the shell, the
calibration and the tools read (filter.plan: regions, observed and closed rooms) is the core's build."""

import hashlib
import importlib.util
import json
import math
import os
import pathlib
import platform

import numpy as np
import pytest

import presence_core
from presence_tracker.filter import Tracker, plan
from presence_tracker.model import Config

from fixture import FIXTURES, fixture
from test_existence import _nook_config
from test_filter import flat_config

pytestmark = pytest.mark.hooks  # build_export, hidden_at
from test_frames import room_config
from test_roomseen import open_plan

PRIVATE = os.environ.get("PT_CONFIGS")
_spec = importlib.util.spec_from_file_location("core_build", FIXTURES / "make" / "core_build.py")
core_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core_build)


def configs():
    """(name, config, its digests: {part: sha1}, {sensor: sha1 of its reflections})."""
    out = [("flat", flat_config()), ("flat entry", flat_config(entry=True)), ("room", room_config()),
           ("nook", _nook_config()), ("open plan", open_plan())]
    digests = [fixture("core_build.json"), fixture("core_reflections.json")]
    if PRIVATE:
        for path in sorted(pathlib.Path(PRIVATE).glob("*.json")):
            d = json.loads(path.read_text())
            if "sensors" in d and "zones" in d:
                out.append((path.name, Config.from_dict(d)))
        for k, name in enumerate(("core_build_0381.json", "core_reflections_0381.json")):
            digests[k] = {**digests[k], **json.loads((pathlib.Path(PRIVATE) / name).read_text())}
    return [(name, config, digests[0][name], digests[1][name]) for name, config in out]


def _outline(z) -> list:
    """A room's corners (a circle by 32 of them), for the bounds of the grid (as model.Shape had them until
    0.46: the digests are of that grid)."""
    if z.shape == "circle":
        return [(z.center[0] + z.radius * math.cos(2 * math.pi * i / 32), z.center[1] + z.radius * math.sin(2 * math.pi * i / 32))
                for i in range(32)]
    return [tuple(p) for p in z.points]


def reflections(config) -> dict:
    """Per sensor a sha1 of the rule of reflections (presence_core.hidden_at) on a grid of points 0.37 m
    apart around the rooms, each looked at from the sensor."""
    rooms = [z for z in config.zones if z.kind == "room"]
    if not rooms or not config.sensors:
        return {}
    xs = [x for z in rooms for x, _ in _outline(z)]
    ys = [y for z in rooms for _, y in _outline(z)]
    x0, y0, x1, y1 = min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1
    points = [(x, y) for x in np.arange(x0, x1, 0.37) for y in np.arange(y0, y1, 0.37)]
    out = {}
    for si, s in enumerate(config.sensors):
        us = []
        for x, y in points:
            d = math.hypot(x - s.x, y - s.y)
            us.append(((x - s.x) / d, (y - s.y) / d) if d > 0 else (1.0, 0.0))
        hidden = presence_core.hidden_at(json.dumps(config.to_dict()), si, points, us, config.params.wall_margin)
        out[s.id] = hashlib.sha1(bytes(hidden)).hexdigest()[:16]
    return out


CONFIGS = configs()
IDS = [name for name, *_ in CONFIGS]


@pytest.mark.parametrize("name, config, build, reflected", CONFIGS, ids=IDS)
def test_the_core_builds_the_world_0381_built(name, config, build, reflected):
    if platform.machine() not in ("x86_64", "AMD64"):  # the digests are bits: of amd64 (x86-64-v3), as built
        pytest.skip("the digests are of amd64's floating point")
    got = core_build.digests(config, "{}", Tracker(config).tiles()["id"])
    assert sorted(got) == sorted(build)
    assert [k for k in build if got[k] != build[k]] == []  # the parts that differ
    assert reflections(config) == reflected
    # the zones of the outputs are the home's, each by its own name (an outside room among them shifts nothing)
    home = [z.id for z in config.zones if z.kind == "area" or z.kind == "room" and not z.entry]
    assert set(Tracker(config).zone_states()) - {"_total"} == set(home) == {z[0] for z in plan(config)["home"]}


@pytest.mark.parametrize("name, config, build, reflected", CONFIGS, ids=IDS)
def test_the_plan_view_is_the_cores_build(name, config, build, reflected):
    core = presence_core.build_export(json.dumps(config.to_dict()), "{}")
    view = plan(config)
    assert [tuple(r) for r in core["regions"]] == [(rid, r["name"], r["rooms"], r["exits"])
                                                    for rid, r in view["regions"].items()]
    assert all(r["open"] == (r["exits"] > 0) for r in view["regions"].values())
    assert view["observed"] == core["observed_rooms"] and view["own_rooms"] == core["own_rooms"]
    assert len(view["closed"]) == core["closed_rooms"]
    assert view["places"] == ["observed", *view["regions"], "outside"]
    assert view["areas"] == [z.id for z in config.zones if z.kind == "area"]
    tr = Tracker(config)
    assert tr.plan == view and tr.rooms == view["observed"] and tr.places == view["places"]
