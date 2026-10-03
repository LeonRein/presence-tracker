import pytest

from presence_tracker.floorplan import faces, merge_collinear, normalize_walls, sight_segments, sync_rooms
from presence_tracker.geometry import line_of_sight
from presence_tracker.model import Config

OUTER = {"points": [[0, 0], [6, 0], [6, 4], [0, 4], [0, 0]], "kind": "wall"}


def area(poly):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def wall(*pts, kind="wall"):
    return {"points": [list(p) for p in pts], "kind": kind}


def test_two_rooms_with_a_door():
    walls = [OUTER, wall((3, 0), (3, 4))]
    rooms = faces(walls)
    assert sorted(round(area(r), 3) for r in rooms) == [12, 12]
    segs = sight_segments(walls, [{"id": "d", "x": 3, "y": 2, "width": 0.9}])
    # the door is open for the radar, the rest of the wall is not
    assert line_of_sight((2, 2), (4, 2), segs)
    assert not line_of_sight((2, 1), (4, 1), segs)


def test_gap_merges_rooms():
    rooms = faces([OUTER, wall((3, 0), (3, 1.5)), wall((3, 2.5), (3, 4))])
    assert [round(area(r), 3) for r in rooms] == [24]


def test_divider_splits_rooms_but_blocks_nothing():
    walls = [OUTER, wall((3, 0), (3, 4), kind="divider")]
    assert sorted(round(area(r), 3) for r in faces(walls)) == [12, 12]
    assert line_of_sight((2, 1), (4, 1), sight_segments(walls, []))


def test_wall_ends_just_short_of_another_wall_still_close_the_room():
    rooms = faces([OUTER, wall((3, 0.02), (3, 3.98))])
    assert sorted(round(area(r), 1) for r in rooms) == [12, 12]


def test_loose_wall_and_crossing_walls():
    # a stub inside a room changes nothing; two crossing walls make four rooms
    assert len(faces([OUTER, wall((1, 1), (2, 1))])) == 1
    rooms = faces([OUTER, wall((3, 0), (3, 4)), wall((0, 2), (6, 2))])
    assert sorted(round(area(r), 3) for r in rooms) == [6, 6, 6, 6]


def test_diagonal_wall():
    rooms = faces([OUTER, wall((2, 0), (4, 4))])
    assert sorted(round(area(r), 3) for r in rooms) == [12, 12]


def test_rooms_keep_names_when_walls_move():
    zones = sync_rooms([], [OUTER, wall((3, 0), (3, 4))])
    left = next(z for z in zones if z["anchor"][0] < 3)
    right = next(z for z in zones if z["anchor"][0] > 3)
    left["name"], right["name"], right["entry"] = "Wohnen", "Treppe", True
    zones.append({"id": "sofa", "name": "Sofa", "kind": "area", "shape": "rect", "points": [[0.5, 0.5], [1, 1]]})

    moved = sync_rooms(zones, [OUTER, wall((3.6, 0), (3.6, 4))])
    by_name = {z["name"]: z for z in moved}
    assert set(by_name) == {"Wohnen", "Treppe", "Sofa"}
    assert by_name["Wohnen"]["id"] == left["id"] and area(by_name["Wohnen"]["points"]) == pytest.approx(14.4)
    assert by_name["Treppe"]["entry"] is True

    # wall removed: one room left, it keeps one of the names; the area zone stays
    merged = sync_rooms(moved, [OUTER])
    assert [z["name"] for z in merged if z["kind"] == "room"] in (["Wohnen"], ["Treppe"])
    assert any(z["id"] == "sofa" for z in merged)

    # a new wall makes a new room with a fresh name
    split = sync_rooms(merged, [OUTER, wall((3, 0), (3, 4))])
    names = sorted(z["name"] for z in split if z["kind"] == "room")
    assert len(names) == 2 and "Raum 1" in names and set(names) & {"Wohnen", "Treppe"}


def test_old_config_keeps_its_hand_drawn_rooms():
    old = {"walls": [[[0, 0], [6, 0], [6, 4], [0, 4], [0, 0]], [[3, 0], [3, 1.5]]],
           "zones": [{"id": "a", "name": "A", "kind": "room", "shape": "rect", "points": [[0.1, 0.1], [2.9, 3.9]]}]}
    c = Config.from_dict(old)
    assert c.rooms_from_walls is False
    assert c.zones[0].points == [[0.1, 0.1], [2.9, 3.9]]
    assert c.walls[0]["kind"] == "wall"
    # switched over: the rooms come from the walls, the old room keeps its name
    c2 = Config.from_dict({**c.to_dict(), "rooms_from_walls": True})
    rooms = c2.zones_of("room")
    assert [z.name for z in rooms] == ["A"]
    assert rooms[0].geometry.area() == pytest.approx(24)


def test_entry_rooms_count_as_entry_zones():
    c = Config.from_dict({"walls": [OUTER, wall((3, 0), (3, 4))]})
    rooms = c.zones_of("room")
    assert len(rooms) == 2 and not c.entry_zones
    d = c.to_dict()
    d["zones"][0]["entry"] = True
    assert len(Config.from_dict(d).entry_zones) == 1


def test_polylines_become_segments():
    walls = normalize_walls([[[0, 0], [2, 0], [4, 0], [4, 3]], {"points": [[0, 1], [0, 2]], "kind": "divider"}])
    # the straight-through point at (2, 0) is dropped, the corner at (4, 0) splits
    assert walls == [{"points": [[0, 0], [4, 0]], "kind": "wall"}, {"points": [[4, 0], [4, 3]], "kind": "wall"},
                     {"points": [[0, 1], [0, 2]], "kind": "divider"}]


def test_merge_straight_pieces_but_not_at_junctions():
    walls = [wall((0, 0), (2, 0)), wall((2, 0), (3, 0)), wall((3, 0), (5, 0)),  # one straight wall in 3 pieces
             wall((3, 0), (3, 2)),  # ... but a wall meets at x = 3
             wall((5, 0), (5, 2)),  # corner
             wall((0, 3), (1, 3), kind="divider"), wall((1, 3), (2, 3))]  # different kinds stay apart
    merged = merge_collinear(walls)
    pts = sorted(tuple(map(tuple, w["points"])) for w in merged)
    assert ((0, 0), (3, 0)) in pts and ((3, 0), (5, 0)) in pts
    assert len(merged) == 6


def test_old_config_walls_are_joined_once():
    old = {"version": 2, "walls": [[[0, 0], [3, 0]], [[3, 0], [6, 0]], [[6, 0], [6, 4], [0, 4], [0, 0]]],
           "rooms_from_walls": True}
    c = Config.from_dict(old)
    assert len(c.walls) == 4 and all(len(w["points"]) == 2 for w in c.walls)
    # version 3 configs are taken as they are: deliberately split walls stay split
    again = Config.from_dict({**c.to_dict(), "walls": c.walls + [wall((0, 2), (1, 2)), wall((1, 2), (2, 2))]})
    assert len(again.walls) == 6
