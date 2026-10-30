"""MODEL.md 6 "Personen": one assignment of people to places. Every number of people is the display's, everybody
who is sure enough counted once at their most probable place, and occupied is that a room has anybody: rooms,
regions, the house and the light fit by construction, and a room is empty once they left it."""

from presence_tracker.filter import Tracker
from presence_tracker.frames import Frame
from presence_tracker.model import Config

ROOMS = ("a", "b", "c")


def three_rooms() -> Config:
    """Three rooms in a row without a wall between them, 2 m deep each; one sensor sees all of them."""
    def rect(zid, y0):
        return {"id": zid, "name": zid.upper(), "kind": "room", "shape": "rect", "points": [[0, y0], [3, y0 + 2]]}

    def wall(a, b, kind="wall"):
        return {"points": [list(a), list(b)], "kind": kind}
    return Config.from_dict({
        "sensors": [{"id": "s", "name": "a", "x": 1.5, "y": 0.05, "heading": 90, "placed": True}],
        "zones": [rect(z, 2 * k) for k, z in enumerate(ROOMS)],
        "walls": [wall((0, 0), (3, 0)), wall((3, 0), (3, 6)), wall((3, 6), (0, 6)), wall((0, 6), (0, 0)),
                  wall((0, 2), (3, 2), "divider"), wall((0, 4), (3, 4), "divider")],
    })


def test_a_walk_through_three_rooms_counts_only_the_room_they_are_in():
    """The owner: alone through the flat, and afterwards several rooms on 1. The numbers of a room were held
    for 10 s by its own LD2450 (seen_hold, gone) and came from each room's own distribution."""
    tr = Tracker(three_rooms(), start=0.0)
    walked = []
    for k in range(120):  # 0.5 m/s from 1 m to 5 m, then standing there
        t = 0.1 * k
        y = min(1.0 + max(t - 3.0, 0.0) * 0.5, 5.0)
        moving = 3.0 < t < 11.0
        target = {"slot": 1, "x": 7 if k % 2 else -7, "y": round((y - 0.05) * 1000), "speed": 50 + k % 3 if moving else 0}
        tr.process_frame("s", t, Frame.of({"uptime_ms": 100 * k, "targets": [target]}))
        tr.step(t)
        states = tr.zone_states()
        counts = [states[z].count for z in ROOMS]
        people = [p for p in tr.persons() if p["counted"]]
        assert sum(counts) == states["_total"].count == len(people), (t, counts, people)
        for z, n in zip(ROOMS, counts):
            assert states[z].moving + states[z].still == n and states[z].occupied == (n > 0)
        if not people:  # the first frames
            continue
        assert len(people) == 1
        room = int(y // 2)
        if abs(y - 2 * room) > 0.5 and abs(y - 2 * room - 2) > 0.5:  # not at a border
            walked.append(ROOMS[room])
            assert counts == [int(k == room) for k in range(3)], (t, y, counts)
            assert people[0]["room"] == ROOMS[room]
    assert set(walked) == set(ROOMS)


def test_nobody_known_occupies_nothing_whatever_the_unknown_ones_may_do():
    tr = Tracker(three_rooms(), start=0.0)
    states = tr.zone_states()
    assert all(0 < states[z].probability < 1 for z in ROOMS)  # P(somebody there): only information
    assert not any(states[z].occupied or states[z].count for z in ROOMS)
    assert states["_total"].count == 0 and not states["_total"].occupied


def test_a_config_saved_with_the_old_settings_loads_without_them():
    d = three_rooms().to_dict()
    d["params"]["seen_hold"] = 10
    assert "seen_hold" not in Config.from_dict(d).to_dict()["params"]
