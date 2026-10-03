import pytest

from presence_tracker.floorplan import faces, sight_segments, sync_rooms
from presence_tracker.geometry import line_of_sight
from presence_tracker.model import Config

OUTER = [{"points": [a, b], "kind": "wall"} for a, b in [([0, 0], [6, 0]), ([6, 0], [6, 4]), ([6, 4], [0, 4]), ([0, 4], [0, 0])]]


def area(poly):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def wall(*pts, kind="wall"):
    return {"points": [list(p) for p in pts], "kind": kind}


def test_two_rooms_with_a_door():
    walls = [*OUTER, wall((3, 0), (3, 4))]
    rooms = faces(walls)
    assert sorted(round(area(r), 3) for r in rooms) == [12, 12]
    segs = sight_segments(walls, [{"id": "d", "x": 3, "y": 2, "width": 0.9}])
    # the door is open for the radar, the rest of the wall is not
    assert line_of_sight((2, 2), (4, 2), segs)
    assert not line_of_sight((2, 1), (4, 1), segs)


def test_gap_merges_rooms():
    rooms = faces([*OUTER, wall((3, 0), (3, 1.5)), wall((3, 2.5), (3, 4))])
    assert [round(area(r), 3) for r in rooms] == [24]


def test_divider_splits_rooms_but_blocks_nothing():
    walls = [*OUTER, wall((3, 0), (3, 4), kind="divider")]
    assert sorted(round(area(r), 3) for r in faces(walls)) == [12, 12]
    assert line_of_sight((2, 1), (4, 1), sight_segments(walls, []))


def test_wall_ends_just_short_of_another_wall_still_close_the_room():
    rooms = faces([*OUTER, wall((3, 0.02), (3, 3.98))])
    assert sorted(round(area(r), 1) for r in rooms) == [12, 12]


def test_loose_wall_and_crossing_walls():
    # a stub inside a room changes nothing; two crossing walls make four rooms
    assert len(faces([*OUTER, wall((1, 1), (2, 1))])) == 1
    rooms = faces([*OUTER, wall((3, 0), (3, 4)), wall((0, 2), (6, 2))])
    assert sorted(round(area(r), 3) for r in rooms) == [6, 6, 6, 6]


def test_diagonal_wall():
    rooms = faces([*OUTER, wall((2, 0), (4, 4))])
    assert sorted(round(area(r), 3) for r in rooms) == [12, 12]


def test_rooms_keep_names_when_walls_move():
    zones = sync_rooms([], [*OUTER, wall((3, 0), (3, 4))])
    left = next(z for z in zones if z["anchor"][0] < 3)
    right = next(z for z in zones if z["anchor"][0] > 3)
    left["name"], right["name"], right["entry"] = "Wohnen", "Treppe", True
    zones.append({"id": "sofa", "name": "Sofa", "kind": "area", "shape": "rect", "points": [[0.5, 0.5], [1, 1]]})

    moved = sync_rooms(zones, [*OUTER, wall((3.6, 0), (3.6, 4))])
    by_name = {z["name"]: z for z in moved}
    assert set(by_name) == {"Wohnen", "Treppe", "Sofa"}
    assert by_name["Wohnen"]["id"] == left["id"] and area(by_name["Wohnen"]["points"]) == pytest.approx(14.4)
    assert by_name["Treppe"]["entry"] is True

    # wall removed: one room left, it keeps one of the names; the area zone stays
    merged = sync_rooms(moved, OUTER)
    assert [z["name"] for z in merged if z["kind"] == "room"] in (["Wohnen"], ["Treppe"])
    assert any(z["id"] == "sofa" for z in merged)

    # a new wall makes a new room with a fresh name
    split = sync_rooms(merged, [*OUTER, wall((3, 0), (3, 4))])
    names = sorted(z["name"] for z in split if z["kind"] == "room")
    assert len(names) == 2 and "Raum 1" in names and set(names) & {"Wohnen", "Treppe"}


def test_entry_rooms_count_as_entry_zones():
    c = Config.from_dict({"walls": [*OUTER, wall((3, 0), (3, 4))]})
    rooms = c.zones_of("room")
    assert len(rooms) == 2 and not c.entry_zones
    d = c.to_dict()
    d["zones"][0]["entry"] = True
    assert len(Config.from_dict(d).entry_zones) == 1
