"""The core builds the world itself (docs/rust-core.md 6): for every configuration at hand - those of the
tests, and private ones in the directory PT_CONFIGS - its plan, raster, tiles, sight and LD2410C tiles, and
its rule of what is a reflection on a grid of points, are what 0.38.1 built. There they were checked against
the Python tracker's build (the plan, raster and tiles exactly, the numbers up to rounding) and its rule
(model.Config.hidden); what is kept of that is a digest per part and configuration (fixtures/core_build.json,
core_reflections.json, made on 29c2fe1: fixtures/README.md; the private ones' in PT_CONFIGS; after an intended
change of the build anew by running this file, `digests`). The plan the shell, the
calibration and the tools read (filter.plan: regions, observed and closed rooms) is the core's build."""

import hashlib
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

from fixture import fixture
from test_existence import _nook_config
from test_filter import flat_config

pytestmark = pytest.mark.hooks  # build_export, hidden_at
from test_frames import room_config
from test_roomseen import open_plan

PRIVATE = os.environ.get("PT_CONFIGS")


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
    if z["shape"] == "circle":
        return [(z["center"][0] + z["radius"] * math.cos(2 * math.pi * i / 32), z["center"][1] + z["radius"] * math.sin(2 * math.pi * i / 32))
                for i in range(32)]
    return [tuple(p) for p in z["points"]]


def reflections(config) -> dict:
    """Per sensor a sha1 of the rule of reflections (presence_core.hidden_at) on a grid of points 0.37 m
    apart around the rooms, each looked at from the sensor."""
    rooms = [z for z in config.data["zones"] if z["kind"] == "room"]
    if not rooms or not config.data["sensors"]:
        return {}
    xs = [x for z in rooms for x, _ in _outline(z)]
    ys = [y for z in rooms for _, y in _outline(z)]
    x0, y0, x1, y1 = min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1
    points = [(x, y) for x in np.arange(x0, x1, 0.37) for y in np.arange(y0, y1, 0.37)]
    out = {}
    for si, s in enumerate(config.data["sensors"]):
        us = []
        for x, y in points:
            d = math.hypot(x - s["x"], y - s["y"])
            us.append(((x - s["x"]) / d, (y - s["y"]) / d) if d > 0 else (1.0, 0.0))
        hidden = presence_core.hidden_at(json.dumps(config.to_dict()), si, points, us, config.data["params"]["wall_margin"])
        out[s["id"]] = hashlib.sha1(bytes(hidden)).hexdigest()[:16]
    return out


def digests(config) -> dict:
    """{key: sha1 of it} of what the core builds of the configuration (presence_core.build_export with the core's
    defaults: the plan, the raster, the tiles, per sensor its sight and LD2410C) and the tiles' fingerprint."""
    core = presence_core.build_export(json.dumps(config.to_dict()), "{}")

    def sha(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()[:16]

    def of(v) -> str:
        if v is None:
            return sha(b"-")
        if isinstance(v, np.ndarray):
            return sha(np.ascontiguousarray(v, dtype=float).tobytes())
        return sha(json.dumps(v, sort_keys=True, default=str).encode())
    out = {k: of(v) for k, v in core.items() if k != "sensors"}
    for si, s in enumerate(core["sensors"]):
        out.update({f"sensor {si} {k}": of(v) for k, v in s.items()})
    out["fingerprint"] = Tracker(config).tiles()["id"]
    return out


CONFIGS = configs()
IDS = [name for name, *_ in CONFIGS]


@pytest.mark.parametrize("name, config, build, reflected", CONFIGS, ids=IDS)
def test_the_core_builds_the_world_0381_built(name, config, build, reflected):
    if platform.machine() not in ("x86_64", "AMD64"):  # the digests are bits: of amd64 (x86-64-v3), as built
        pytest.skip("the digests are of amd64's floating point")
    got = digests(config)
    assert sorted(got) == sorted(build)
    assert [k for k in build if got[k] != build[k]] == []  # the parts that differ
    assert reflections(config) == reflected
    # the zones of the outputs are the home's, each by its own name (an outside room among them shifts nothing)
    home = [z["id"] for z in config.data["zones"] if z["kind"] == "area" or z["kind"] == "room" and not z["entry"]]
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
    assert view["areas"] == [z["id"] for z in config.data["zones"] if z["kind"] == "area"]
    tr = Tracker(config)
    assert tr.plan == view and tr.rooms == view["observed"] and tr.places == view["places"]


if __name__ == "__main__":
    # OPENBLAS_NUM_THREADS=1 PT_CONFIGS=DIR PYTHONPATH=. uv run python tests/test_core_build.py OUT: the digests anew,
    # after an intended change of the build (as 0.53.0); OUT/core_build.json, the private ones into PT_CONFIGS
    import sys
    repo, mine = {}, {}
    for name, config, *_ in CONFIGS:
        (mine if PRIVATE and (pathlib.Path(PRIVATE) / name).exists() else repo)[name] = digests(config)
    (pathlib.Path(sys.argv[1]) / "core_build.json").write_text(json.dumps(repo, separators=(",", ":")) + "\n")
    if PRIVATE:
        (pathlib.Path(PRIVATE) / "core_build_0381.json").write_text(json.dumps(mine, indent=1) + "\n")
    print("repo", len(repo), "private", len(mine))
