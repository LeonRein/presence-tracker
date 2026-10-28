"""The fixtures of 0.38.1 (README.md one directory up), made with its Python oracles. Runs on 29c2fe1 only:

    git worktree add W 29c2fe1 && cd W/tracker && uv sync \
        && uv run maturin develop --uv --release -m core/presence-py/Cargo.toml
    OPENBLAS_NUM_THREADS=1 PT_CONFIGS=DIR uv run python THIS OUT

writes people.json, ghosts.json, destinations.json, ld2410.json, core_reflections.json into OUT, and the private
configurations' core_reflections_0381.json into PT_CONFIGS (never into the repository: it is public)."""

import hashlib
import json
import math
import os
import pathlib
import sys

import numpy as np

sys.path[:0] = [".", "tests"]
from presence_tracker import ld2410  # noqa: E402
from presence_tracker.destination import DestinationMap, Geometry, vel_interp  # noqa: E402
from presence_tracker.filter import Tracker  # noqa: E402
from presence_tracker.filtermodel import Model  # noqa: E402
from presence_tracker.frames import Frame  # noqa: E402
from presence_tracker.gauss import Gauss  # noqa: E402
from presence_tracker.ghostmap import GhostMap  # noqa: E402
from presence_tracker.hidden import Hidden  # noqa: E402
from presence_tracker.sim import simulate  # noqa: E402

from test_core_build import configs  # noqa: E402
from test_destination import routine  # noqa: E402
from test_entering import two_rooms  # noqa: E402
from test_existence import _nook, _nook_config  # noqa: E402
from test_filter import flat_config  # noqa: E402
from test_frames import _floor, room_config, sim_sensors  # noqa: E402
from test_restart import saved  # noqa: E402

OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def write(name, data, path=OUT, indent=None):
    (path / name).write_text(json.dumps(data, indent=indent, separators=None if indent else (",", ":")) + "\n")


# ------------------------------------------------------------------ people.json (hidden.Hidden.to_dict)
people = {}
crowd = Tracker(_nook_config(), start=0.0, people=["outside"])  # a sitter in the nook nobody sees, r = 1
h = Hidden(crowd.tiles)
for c in _nook(crowd.tiles):
    h.still[:, :, c] = crowd.shapes.stay_prior(ongoing=True)
h.r = 1.0
h._normalize()
people["nook"] = saved(crowd, [h])
crowd = Tracker(flat_config(entry=True), start=0.0, people=["outside"])  # somebody sitting at (3, 2.5)
h = Hidden(crowd.tiles)
h.still[:, :, crowd.tiles.cell_near((3.0, 2.5))] = crowd.shapes.stay_prior(ongoing=True)
h._normalize()
people["sitter"] = saved(crowd, [h])
crowd = Tracker(flat_config(), start=0.0)  # two standing at (3, 3), an amplitude 0.3 measured by a track
m, sh, tl = crowd.m, crowd.shapes, crowd.tiles
g = Gauss.source(1, np.array([3.0, 3.0]), 0.01, m, sh)
g.logw = np.log(np.array([1.0 - 1e-9, 1e-9]))
a = int(np.argmin(abs(sh.amp - 0.3)))
ka = np.zeros_like(g.ka)
ka[:, a] = g.kw
g.ka = ka
people["measured"] = saved(crowd, [g.to_tiles(tl), g.to_tiles(tl)])
people["measured_level"] = a
write("people.json", people)

# ------------------------------------------------------------------ ghosts.json (ghostmap.GhostMap)
ghosts = {}
tr = Tracker(room_config())
gm = GhostMap.from_dict(tr.learned()["ghost_map"])
for _ in range(3):
    gm.add_birth("a", np.array([2.0, 2.0]), 1.0)
gm.add_watch("a", 2.5 * 3600)
ghosts["room_few"] = gm.to_dict()
for _ in range(27):
    gm.add_birth("a", np.array([2.0, 2.0]), 1.0)
gm.add_watch("a", 21.5 * 3600)
ghosts["room_many"] = gm.to_dict()
gm = GhostMap.from_dict(tr.learned()["ghost_map"])
gm.add_birth("a", (2.0, 2.0), 1.0)
gm.prior_time /= 24
ghosts["room_other_prior"] = gm.to_dict()
gm = GhostMap.from_dict(tr.learned()["ghost_map"])
gm.add_watch("a", 86400.0)
gm.add_birth("a", np.array([[2.2, 2.6]]), 12.0)
ghosts["room_spot"] = gm.to_dict()
ghosts["room_spot_rate"] = float(gm.rate("a", np.array([[2.2, 2.6]]))[0])
crowd = Tracker(flat_config(entry=True), start=0.0, people=["outside", "outside"])
gm = GhostMap.from_dict(crowd.learned()["ghost_map"])
for sid in ("a", "b"):
    gm.add_birth(sid, (4.5, 3.5), 200.0)
ghosts["flat_seat"] = gm.to_dict()
spot = _floor(tr.config, (0.6, 4.0))
gm = GhostMap.from_dict(tr.learned()["ghost_map"])
for _ in range(30):
    gm.add_birth("a", spot[0], 1.0)
gm.add_watch("a", 86400.0)
gm.add_watch("b", 86400.0)
gm.add_birth("b", (3.0, 2.5), 5.0)
ghosts["room_source"] = gm.to_dict()
ghosts["room_source_spot"] = [float(x) for x in spot[0]]
write("ghosts.json", ghosts)

# ------------------------------------------------------------------ destinations.json (destination.Geometry,
# DestinationMap): the geometry of test_entering.two_rooms, and a map with the walks of two trips desk -> sofa
# and back, written by the Python map, with its lookups
tr = Tracker(two_rooms(), start=0.0, people=["observed"])
geo = Geometry(tr)
cells, cw = geo.around(3.5, 2.5)
dest = {"fingerprint": geo.fingerprint(), "n": int(geo.n), "around": [[int(c) for c in cells], [float(w) for w in cw]]}
for name, v in (("ahead", (1.0, 0.0)), ("back", (-1.0, 0.0))):
    vc, vw = vel_interp(*v)
    dest[name] = [[int(c) for c in vc], [float(w) for w in vw]]
person = routine(2)
nxt = 0.0
for t, sid, frame in simulate([person], sim_sensors(two_rooms()), person.waypoints[-1][0] + 1,
                              walls=two_rooms().wall_segments):
    tr.process_frame(sid, t, Frame.of(frame))
    if t >= nxt:
        tr.step(t)
        nxt = t + 0.2
dm = DestinationMap.from_dict(tr.learned()["dest_map"])
dest["map"] = dm.to_dict()
dest["map_lookup"] = {k: [float(x) for x in dm.lookup(np.array(cells), np.array(cw), *v)]
                      for k, v in (("ahead", (1.0, 0.0)), ("back", (-1.0, 0.0)))}
write("destinations.json", dest)

# ------------------------------------------------------------------ ld2410.json (ld2410.Background): two sensors,
# each with its level, its anchor, echoes counted; one held after "Tracks zurücksetzen"
tr = Tracker(room_config())
m = Model()
prior = ld2410.prior(m)
bg = ld2410.Background(prior, m.ld_prior_time, m.ld_forget, m.ld_echo_rate, m.ld_echo_prior_time, m.ld_anchor_forget,
                       m.ld_anchor_quiet)
bg.use(tr.config)
cells16 = np.arange(16)
bg.load_dict({**bg.to_dict(),
              "num": {"a": list(prior * 3600.0 * (1.5 + 0.01 * cells16)), "b": list(prior * 1800.0 * 0.8)},
              "den": {"a": 3600.0, "b": 1800.0},
              "num_a": {"a": list(prior * 86400.0 * 1.2), "b": list(prior * 40000.0 * (0.9 + 0.005 * cells16))},
              "den_a": {"a": 86400.0, "b": 40000.0},
              "echoes": {"a": 2.25, "b": 0.125}, "quiet": {"a": 5400.0, "b": 1234.5}, "held": {"b": 42.5}})
ld = {"background": bg.to_dict(),
      "b": {s: [float(x) for x in bg.b(s)] for s in ("a", "b")},
      "anchor": {s: [float(x) for x in bg.anchor(s)] for s in ("a", "b")},
      "rate": {s: float(bg.rate(s)) for s in ("a", "b")}}
write("ld2410.json", ld)


# ------------------------------------------------------------------ core_reflections.json (model.Config.hidden):
# per configuration and sensor a sha1 of the rule on a grid of points 0.37 m apart around the rooms
def reflections(config) -> dict:
    rooms = config.zones_of("room")
    if not rooms or not config.sensors:
        return {}
    x0, y0 = min(z.geometry.bounds()[0] for z in rooms) - 1, min(z.geometry.bounds()[1] for z in rooms) - 1
    x1, y1 = max(z.geometry.bounds()[2] for z in rooms) + 1, max(z.geometry.bounds()[3] for z in rooms) + 1
    points = [(x, y) for x in np.arange(x0, x1, 0.37) for y in np.arange(y0, y1, 0.37)]
    out = {}
    for s in config.sensors:
        us = []
        for x, y in points:
            d = math.hypot(x - s.x, y - s.y)
            us.append(((x - s.x) / d, (y - s.y) / d) if d > 0 else (1.0, 0.0))
        hidden = [config.hidden(s, p, u, config.params.wall_margin) for p, u in zip(points, us)]
        out[s.id] = hashlib.sha1(bytes(hidden)).hexdigest()[:16]
    return out


private = os.environ.get("PT_CONFIGS")
repo, mine = {}, {}
for name, config in configs():
    (mine if private and (pathlib.Path(private) / name).exists() else repo)[name] = reflections(config)
write("core_reflections.json", repo)
if private:
    write("core_reflections_0381.json", mine, pathlib.Path(private), indent=1)
print("written", sorted(p.name for p in OUT.iterdir()), "repo", len(repo), "private", len(mine))
