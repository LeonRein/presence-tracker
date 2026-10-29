"""The plan as the model reads it (MODEL.md 2): the editor (static/js/floorplan.js, test_ui_floorplan.py) finds the
rooms between the walls and puts doors on walls; the model takes the finished map: a door opens its wall for
the radar, a room boundary blocks nothing, an entry room is outside."""

import json

import pytest

import presence_core

from presence_tracker.filter import plan
from presence_tracker.model import Config

OUTER = [{"points": [a, b], "kind": "wall"} for a, b in [([0, 0], [6, 0]), ([6, 0], [6, 4]), ([6, 4], [0, 4]), ([0, 4], [0, 0])]]


def wall(*pts, kind="wall"):
    return {"points": [list(p) for p in pts], "kind": kind}


# the two rooms of OUTER split at x = 3, as the editor finds them
ROOMS = [{"id": "l", "name": "Links", "kind": "room", "shape": "polygon", "points": [[0, 0], [3, 0], [3, 4], [0, 4]],
          "anchor": [1.5, 2.0], "entry": False},
         {"id": "r", "name": "Rechts", "kind": "room", "shape": "polygon", "points": [[3, 0], [6, 0], [6, 4], [3, 4]],
          "anchor": [4.5, 2.0], "entry": False}]


def test_the_server_takes_the_rooms_as_the_editor_made_them():
    c = Config.from_dict({"walls": [*OUTER, wall((3, 0), (3, 4))], "zones": ROOMS})
    assert [z.to_dict() for z in c.zones] == ROOMS
    # walls without rooms: none (the editor finds them, not the server)
    assert Config.from_dict({"walls": OUTER}).zones == []


def test_a_door_opens_its_wall_for_the_radar():
    walls = [*OUTER, wall((3, 0), (3, 4))]
    geometry = Config.from_dict({"walls": walls, "zones": ROOMS,
                                 "doors": [{"id": "d", "x": 3, "y": 2, "width": 0.9}]}).geometry()
    # the door is open for the radar, the rest of the wall is not
    assert geometry.in_sight((2, 2), [(4, 2), (4, 1)]) == [True, False]


def test_a_divider_blocks_nothing():
    walls = [*OUTER, wall((3, 0), (3, 4), kind="divider")]
    assert Config.from_dict({"walls": walls, "zones": ROOMS}).geometry().in_sight((2, 1), [(4, 1)]) == [True]


@pytest.mark.hooks
def test_entry_rooms_are_outside():
    # an entry room (the stairwell) is public space beyond the flat's door: no room of the home and
    # no region without a sensor; the door into it is a door to the outside (MODEL.md 2)
    c = Config.from_dict({"walls": [*OUTER, wall((3, 0), (3, 4))], "zones": ROOMS, "doors": [{"id": "d", "x": 3.0, "y": 2.0}],
                          "sensors": [{"id": "s", "x": 0.05, "y": 0.05, "heading": 45, "placed": True}]})
    assert set(plan(c)["regions"]) == {"r"}
    d = c.to_dict()
    next(z for z in d["zones"] if z["id"] == "r")["entry"] = True
    c = Config.from_dict(d)
    p = plan(c)
    assert not p["regions"] and p["closed"] == ["r"]
    assert p["observed"] == ["l"] and [z[0] for z in p["home"] if z[2]] == ["l"]
    # the one way out of the observed area leads outside (places: observed, outside)
    core = presence_core.build_export(c.model_json(), "{}")
    assert [q[0] for q in core["portals"]] == [p["places"].index("outside")]
    # what a sensor sees in there is nobody of the home
    assert presence_core.hidden_at(c.model_json(), 0, [(3.5, 2.0), (2.5, 2.0)], [(1.0, 0.0)] * 2, 0.4) == [True, False]
