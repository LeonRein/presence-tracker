"""The editor's map (static/js: floorplan.js, util.js), run in Node; skipped without it. The rooms are the closed
areas between the walls (as the Python editor of 0.43.0 found them: compared on every test and dev
configuration, 655 cases, when the room finding came back to the web UI in 0.48.0); doors stay on their walls
while walls move (how near counts as on a wall: the core's); a trail breaks where it would cross a wall outside a
door; the background image's layers are checked here, never by the model."""

import json
import pathlib
import shutil
import subprocess

import presence_core
import pytest

from presence_tracker.model import Config

JS = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

OUTER = [{"points": [a, b], "kind": "wall"} for a, b in [([0, 0], [6, 0]), ([6, 0], [6, 4]), ([6, 4], [0, 4]), ([0, 4], [0, 0])]]


def js(expr: str, **values):
    """The JSON of a JavaScript expression over floorplan.js and util.js, with values as constants."""
    consts = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in values.items())
    script = (f"import * as fp from {json.dumps((JS / 'floorplan.js').as_uri())};\n"
              f"import * as util from {json.dumps((JS / 'util.js').as_uri())};\n{consts}"
              f"console.log(JSON.stringify({expr}));")
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def on_wall():
    """m: a door this near a wall is on it (the core's, as the web UI reads it: derived.plan.door_on_wall)."""
    return json.loads(presence_core.derive(Config.from_dict({"walls": OUTER}).model_json()))["plan"]["door_on_wall"]


def wall(*pts, kind="wall"):
    return {"points": [list(p) for p in pts], "kind": kind}


def areas(walls):
    a = js("fp.faces(walls).map(r => r.reduce((s, p, i) => s + p[0] * r[(i + 1) % r.length][1] - r[(i + 1) % r.length][0] * p[1], 0) / 2)",
           walls=walls)
    return sorted(round(x, 3) for x in a)


def test_walls_make_rooms():
    assert areas([*OUTER, wall((3, 0), (3, 4))]) == [12, 12]
    # a gap merges them, a wall just short of another still closes, a stub changes nothing
    assert areas([*OUTER, wall((3, 0), (3, 1.5)), wall((3, 2.5), (3, 4))]) == [24]
    assert len(areas([*OUTER, wall((3, 0.02), (3, 3.98))])) == 2
    assert areas([*OUTER, wall((1, 1), (2, 1))]) == [24]
    # crossing walls make four rooms, a diagonal two; a divider closes a room like a wall
    assert areas([*OUTER, wall((3, 0), (3, 4)), wall((0, 2), (6, 2))]) == [6, 6, 6, 6]
    assert areas([*OUTER, wall((2, 0), (4, 4))]) == [12, 12]
    assert areas([*OUTER, wall((3, 0), (3, 4), kind="divider")]) == [12, 12]


def test_rooms_keep_names_ids_and_entry_when_walls_move():
    zones = js("fp.syncRooms([], walls, (() => { let n = 0; return () => `r${n++}`; })())", walls=[*OUTER, wall((3, 0), (3, 4))])
    assert [z["id"] for z in zones] == ["r0", "r1"] and {z["name"] for z in zones} == {"Raum 1", "Raum 2"}
    left = next(z for z in zones if z["anchor"][0] < 3)
    right = next(z for z in zones if z["anchor"][0] > 3)
    left["name"], right["name"], right["entry"] = "Wohnen", "Treppe", True
    zones.append({"id": "sofa", "name": "Sofa", "kind": "area", "shape": "rect", "points": [[0.5, 0.5], [1, 1]]})
    # the same walls: the same zones
    assert js("fp.syncRooms(zones, walls)", zones=zones, walls=[*OUTER, wall((3, 0), (3, 4))]) == zones

    moved = js("fp.syncRooms(zones, walls)", zones=zones, walls=[*OUTER, wall((3.6, 0), (3.6, 4))])
    by_name = {z["name"]: z for z in moved}
    assert set(by_name) == {"Wohnen", "Treppe", "Sofa"} and moved[-1]["id"] == "sofa"
    assert by_name["Wohnen"]["id"] == left["id"] and by_name["Treppe"]["entry"] is True

    # wall removed: one room left, it keeps one of the names; put back, a new room "Raum 1" with a new id
    merged = js("fp.syncRooms(zones, walls)", zones=moved, walls=OUTER)
    assert [z["name"] for z in merged if z["kind"] == "room"] in (["Wohnen"], ["Treppe"])
    split = js("fp.syncRooms(zones, walls)", zones=merged, walls=[*OUTER, wall((3, 0), (3, 4))])
    rooms = [z for z in split if z["kind"] == "room"]
    assert len(rooms) == 2 and any(z["name"] == "Raum 1" and z["id"] not in ("r0", "r1") and z["id"] for z in rooms)


def test_doors_stay_on_their_walls_while_walls_move():
    c = {"walls": [*OUTER, wall((3, 0), (3, 4))], "doors": [{"id": "d", "x": 3.0, "y": 1.0, "width": 0.9},
                                                            {"id": "e", "x": 6.0, "y": 2.0, "width": 0.9}]}
    out = js("""(() => {
      const placed = util.doorsOnWalls(c, onWall);
      c.walls[4].points = [[3.5, 0], [2.5, 4]];   // the middle wall turned
      c.walls[1].points = [[6, 0], [7, 4]];       // and the right one
      util.followDoors(c, placed);
      return c.doors.map(d => { const pl = util.doorWall(c.walls, d); return util.distToSegment([d.x, d.y], pl.a, pl.b); });
    })()""", c=c, onWall=on_wall())
    assert all(d < 1e-3 for d in out)


def test_a_trail_breaks_only_through_a_wall():
    # the middle wall x = 3 with a door of 0.9 m at y = 1, the outer walls; the door's opening the core's (as the
    # web UI has it: derived.plan.doors), cut at the wall's end: a door of 0.9 m at y = 3.8 opens 3.35-4 m along
    # the wall from (3, 0)
    c = {"walls": [*OUTER, wall((3, 0), (3, 4))], "doors": [{"id": "d", "x": 3.0, "y": 1.0, "width": 0.9},
                                                            {"id": "e", "x": 3.0, "y": 3.8, "width": 0.9}]}
    openings = json.loads(presence_core.derive(Config.from_dict(c).model_json()))["plan"]["doors"]
    assert [o["wall"] for o in openings] == [4, 4]
    assert abs(openings[1]["t0"] - 3.35) < 1e-12 and openings[1]["t1"] == 4.0
    crosses = js("[[[2.5, 1], [3.5, 1.2]], [[2.5, 3], [3.5, 3]], [[1, 1], [2, 3]], [[5.5, 2], [6.5, 2]], [[2.9, 3.9], "
                 "[3.1, 3.9]], [[2.9, 3.2], [3.1, 3.2]]].map(([a, b]) => util.crossesWall(c.walls, openings, a, b))",
                 c=c, openings=openings)
    assert crosses == [False, True, False, True, False, True]


def test_the_layers_of_the_background_image_are_checked_here():
    ok = {"layers": [{"x": 0, "y": 0, "scale": 1.02, "rotation": 0, "opacity": 0.5}]}
    assert js("util.layersError(bg)", bg=ok) == ""
    assert "Maßstab des Bildes" in js("util.layersError(bg)", bg={"layers": [{"scale": 11}]})
    assert "Deckkraft" in js("util.layersError(bg)", bg={"layers": [{"opacity": 2}]})
    assert js("util.layersError(bg)", bg={"layers": [5]}) == "Bildebene ist kein Objekt."


def test_the_editor_makes_new_elements_from_the_cores_templates():
    """A new sensor and a new door have the values of the core's templates (GET /api/config "defaults"), none of
    the editor's own: one source of every value (0.49: the editor's sensors looked along +y, the server's +x)."""
    defaults = json.loads(presence_core.config_defaults())
    script = (f"import * as store from {json.dumps((JS / 'store.js').as_uri())};\n"
              f"import * as tools from {json.dumps((JS / 'tools.js').as_uri())};\n"
              f"store.state.config = {{sensors: [], zones: [], walls: {json.dumps(OUTER)}, doors: [], "
              f"params: {{}}, background: {{layers: []}}}};\n"
              f"store.state.defaults = {json.dumps(defaults)};\n"
              "const view = { s: 50, renderOverlay() {} };\n"
              "const sensor = new tools.PlaceSensorTool('presence-neu'); sensor.view = view;\n"
              "sensor.down([1.234, 2.345], null, { shiftKey: true });\n"
              "const door = new tools.DoorTool(); door.view = view;\n"
              "door.down([3, 0.01], null, {});\n"
              "console.log(JSON.stringify(store.state.config));")
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    c = json.loads(r.stdout)
    [s] = c["sensors"]
    assert {k: s[k] for k in defaults["sensor"] if k != "placed"} == \
        {k: v for k, v in defaults["sensor"].items() if k != "placed"}
    assert (s["id"], s["name"], s["x"], s["y"], s["placed"]) == ("presence-neu", "neu", 1.234, 2.345, True)
    [d] = c["doors"]
    assert d["width"] == defaults["door"]["width"] and d["y"] == 0
    # and the server takes them as they are (the core's form of them is themselves)
    assert Config.from_dict(c).data["sensors"][0] == s
