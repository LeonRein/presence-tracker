"""The core builds the world itself (docs/rust-core.md 6, phase 6): for every configuration at hand -
those of the tests, the repository's devdata, and private ones in the directory PT_CONFIGS - its plan,
raster and tiles are the Python tracker's exactly, its numbers up to rounding (BLAS, numpy's sums), and
its rule of what is a reflection (model.Config.hidden) says the same on a grid of points."""

import json
import math
import os
import pathlib

import numpy as np
import pytest

import presence_core
from presence_tracker import roomseen
from presence_tracker.filter import Tracker
from presence_tracker.model import Config

from test_existence import _nook_config
from test_filter import flat_config
from test_frames import room_config
from test_roomseen import open_plan


def configs():
    out = [("flat", flat_config()), ("flat entry", flat_config(entry=True)), ("room", room_config()),
           ("nook", _nook_config()), ("open plan", open_plan())]
    for path in sorted(pathlib.Path(__file__).parent.parent.glob("devdata/*.json")):
        out.append((path.name, Config.from_dict(json.loads(path.read_text()))))
    private = os.environ.get("PT_CONFIGS")
    for path in sorted(pathlib.Path(private).glob("*.json")) if private else ():
        d = json.loads(path.read_text())
        if "sensors" in d and "zones" in d:
            out.append((path.name, Config.from_dict(d)))
    return out


@pytest.mark.parametrize("name, config", configs(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_core_builds_the_world_python_builds(name, config):
    tr = Tracker(config)
    py = tr._export()
    core = presence_core.build_export(json.dumps(config.to_dict()), tr._model(), float(roomseen.SILENT_AFTER))
    # the plan: exactly
    assert [tuple(r) for r in core["regions"]] == [(rid, r["name"], r["rooms"], r["exits"])
                                                    for rid, r in config.regions.items()]
    assert core["observed_rooms"] == tr.rooms
    assert core["own_rooms"] == [roomseen.own_room(s, config.observed_rooms()) for s in config.sensors]
    assert [tuple(p) for p in core["portals"]] == [(p, float(w[0]), float(w[1])) for p, w, _ in tr.world.portals]
    assert core["closed_rooms"] == len(config.closed_rooms)
    # the raster and the tiles: exactly
    w = tr.world
    assert (core["x0"], core["y0"], core["nx"], core["ny"]) == (float(w.x0), float(w.y0), int(w.nx), int(w.ny))
    assert np.array_equal(core["walls"], np.ravel(py["walls"]))
    assert np.array_equal(core["rooms"], np.ravel(py["rooms"]))
    assert (core["n"], core["R"], core["A"]) == (py["n"], py["R"], py["A"])
    for k in ("fine", "tile_room", "point_tile", "exits", "entries"):
        assert list(core[k]) == list(py[k]), k
    for k in ("points", "centers", "tile_var", "area", "generator", "door_rates", "widths"):
        assert np.array_equal(core[k], np.ravel(py[k])), k
    # the numbers: up to rounding
    def close(a, b, tol=1e-12):
        a, b = np.asarray(a, dtype=float).ravel(), np.asarray(b, dtype=float).ravel()
        if a.shape != b.shape:
            return False
        same = a == b  # also the infinite distances without walls
        scale = max(float(np.max(np.abs(b[np.isfinite(b)]), initial=0.0)), 1.0)
        with np.errstate(invalid="ignore"):
            return bool(np.all(same | (np.abs(np.where(same, 0.0, a - b)) <= tol * scale)))
    for k in ("wall_dist", "walk_t", "into_t", "region_rates", "region_start", "go", "go_w", "go_w_ongoing", "kappa",
              "kappa_w", "amp", "ka_w", "amp_given_kappa"):
        assert close(core[k], py[k]), k
    for c, p in zip(core["sensors"], py["sensors"]):
        assert all(close(c[k], p[k]) for k in ("grid", "observed", "ld_los", "ld_still", "ld_walk"))
        assert list(c["ld_idx"]) == list(p["ld_idx"])
        assert (c["ld_view"] is None) == (p["ld_view"] is None)


@pytest.mark.parametrize("name, config", configs(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_core_tells_reflections_as_python_does(name, config):
    rooms = config.zones_of("room")
    if not rooms or not config.sensors:
        return
    x0, y0 = min(z.geometry.bounds()[0] for z in rooms) - 1, min(z.geometry.bounds()[1] for z in rooms) - 1
    x1, y1 = max(z.geometry.bounds()[2] for z in rooms) + 1, max(z.geometry.bounds()[3] for z in rooms) + 1
    points = [(x, y) for x in np.arange(x0, x1, 0.37) for y in np.arange(y0, y1, 0.37)]
    margin = config.params.wall_margin
    for si, s in enumerate(config.sensors):
        us = []
        for x, y in points:
            d = math.hypot(x - s.x, y - s.y)
            us.append(((x - s.x) / d, (y - s.y) / d) if d > 0 else (1.0, 0.0))
        got = presence_core.hidden_at(json.dumps(config.to_dict()), si, points, us, margin)
        want = [config.hidden(s, p, u, margin) for p, u in zip(points, us)]
        assert got == want, (s.id, [p for p, a, b in zip(points, got, want) if a != b][:5])
