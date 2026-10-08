"""The stairwell is outside (MODEL.md 2): public space beyond the flat's door. Who walks into it has
left the flat; nobody is counted, shown or published there."""

import asyncio
import json

from presence_tracker import ha
from presence_tracker.filter import Tracker
from presence_tracker.sim import Person, simulate

from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def test_who_walks_out_through_the_stairwell_door_is_outside():
    # known to be out; comes in through the flat's door, sits a while, walks out into the stairwell
    # and stays there
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.1, 2.5), FLUR_DOOR, (-1.5, 4.2), start=3,
                    pauses={3: 30, 5: 60}))
    crowd = Tracker(config, start=0.0, people=["outside"])
    t_in = a.waypoints[4][0] - 5  # sitting
    t_out = a.waypoints[6][0] + 30  # 30 s after the stairwell door
    seen = {}
    for t, sid, frame in simulate([a], sim_sensors(config), t_out, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        crowd.check()
        if "in" not in seen and t >= t_in:
            seen["in"] = crowd.place_distribution()
    crowd.step(t_out)
    out = crowd.place_distribution()
    assert "flur" not in out and set(out) == {"observed", "balkon", "outside", "_house"}
    assert 1 - seen["in"]["observed"][0] > 0.9, seen["in"]["observed"]  # in the flat
    assert out["_house"][0] > 0.9, out["_house"]  # left it: nobody at home
    assert out["observed"][0] > 0.95 and out["balkon"][0] > 0.95
    assert crowd.zone_states()["_total"].count == 0
    # who left: their record expires out there, where no sensor can test it (MODEL.md 5.5); they do
    # not become an unknown person
    # out of the house who comes back at the rate of coming home for a day
    ppp_out = max(hy.ppp.out for hy in crowd.hyps)
    assert ppp_out < 0.05, ppp_out
    # the person is out (or forgotten, MODEL.md 5.5): not in the flat
    for d in crowd.persons():
        assert max(d["places"], key=d["places"].get) == "outside", d["places"]


def test_nobody_is_shown_or_published_in_the_stairwell():
    config = flat_config(entry=True)
    crowd = Tracker(config, start=0.0, people=["outside", "anywhere"])
    states = crowd.zone_states()
    assert "flur" not in states and {"wohn", "balkon", "_total"} <= set(states)
    snap = crowd.snapshot()
    assert "flur" not in snap["regions"] and "flur" not in crowd.count_distribution()
    assert crowd.rooms == ["wohn"]

    sent = []

    async def publish(topic, payload, retain):
        sent.append((topic, payload, retain))

    disc = ha.Discovery(publish)
    asyncio.run(disc.sync(config.zones))
    assert sent and not any("flur" in t for t, _, _ in sent)
    assert any("presence_tracker_wohn_count" in t for t, _, _ in sent)
    # the broker still holds the stairwell's entities from a run before (a room then): they are
    # removed with their state; the wanted ones stay
    sent.clear()
    stale = "homeassistant/sensor/presence_tracker/presence_tracker_flur_count/config"
    kept = next(t for t in disc.wanted if "wohn_count" in t)
    assert disc.retained(stale, json.dumps({"state_topic": f"{ha.PREFIX}/zone/flur/state"}).encode())
    assert disc.retained(kept, disc.wanted[kept].encode())
    assert disc.retained(stale, b"")  # our own clearing comes back: nothing more to do
    assert not disc.retained("presence/a/frame", b"{}")
    asyncio.run(disc.states(states))
    cleared = {t for t, p, r in sent if p == "" and r}
    assert cleared == {stale, f"{ha.PREFIX}/zone/flur/state"}
    assert not any(t == kept for t, _, _ in sent)
