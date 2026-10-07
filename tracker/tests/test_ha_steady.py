"""What goes to Home Assistant (ha.Discovery.steady): the states exactly as they are, the attributes held
so that the recorder writes a row only now and then."""

import random

from presence_tracker import ha
from presence_tracker.zones import ZoneState


def payload(**kw):
    return ZoneState(**kw).to_dict()


def test_states_pass_attributes_are_held():
    d = ha.Discovery(None)
    base = dict(count=1, moving=1, still=0, probability=0.9, c_enter=0.048, c_target=0.8)
    a = d.steady("flur", payload(**base, approaching=True, p_enter=0.30, eta=1.2, distance=1.1, person=1), t=0.0)
    assert a["approaching"] and a["p_enter"] == 0.3
    # a small move of the attributes a second later: held
    b = d.steady("flur", payload(**base, approaching=True, p_enter=0.35, eta=0.8, distance=0.8, person=1), t=1.0)
    assert b["p_enter"] == 0.3 and b["eta"] == a["eta"] and b["distance"] == a["distance"]
    # a big move, but within 10 s of the last change: still held
    c = d.steady("flur", payload(**base, approaching=True, p_enter=0.9, eta=0.2, distance=0.2, person=1), t=2.0)
    assert c["p_enter"] == 0.3
    # the state flips: at once, attributes new (off: one steady payload)
    e = d.steady("flur", payload(**base, approaching=False, p_enter=0.01, eta=None, distance=None, person=None), t=2.2)
    assert not e["approaching"] and e["p_enter"] == 0.0 and e["eta"] is None and e["person"] is None
    # counts and occupancy pass at once, whatever the attributes
    f = d.steady("flur", payload(**dict(base, count=2, moving=0, still=2, probability=0.92)), t=2.4)
    assert f["count"] == 2 and f["moving"] == 0 and f["still"] == 2 and f["occupied"]
    assert f["count_moving"] == 0  # its state changed: the count sensor's attributes are new
    g = d.steady("flur", payload(**dict(base, count=2, moving=1, still=1, probability=0.85)), t=3.0)
    assert g["moving"] == 1 and g["count_moving"] == 0 and g["probability"] == f["probability"]  # held a minute


def test_nothing_happening_is_one_payload():
    d = ha.Discovery(None)
    seen = set()
    for k, p in enumerate((0.0, 0.004, 0.01, 0.02, 0.0)):
        out = d.steady("kueche", payload(count=0, probability=0.0, p_enter=p, c_enter=0.048, p_target=p, c_target=0.8),
                       t=k * 0.2)
        seen.add(str(sorted(out.items())))
    assert len(seen) == 1


def test_the_states_are_never_held():
    """Whatever the attributes do, every state Home Assistant derives (occupancy, count, moving,
    wird betreten, Ziel) is the one of this moment."""
    rnd = random.Random(1)
    d = ha.Discovery(None)
    for k in range(3000):
        count = rnd.choice((0, 0, 1, 2))
        moving = rnd.randint(0, count)
        st = ZoneState(count=count, moving=moving, still=count - moving, probability=rnd.random(),
                       approaching=rnd.random() < 0.2, p_enter=rnd.random() * 0.3, c_enter=0.048,
                       eta=rnd.random() * 3, distance=rnd.random() * 3, person=rnd.choice((None, 1, 2)),
                       target=rnd.random() < 0.2, p_target=rnd.random(), c_target=0.8, target_weight=rnd.random(),
                       target_walks=rnd.random() * 20, target_from=rnd.choice((None, "a", "b")),
                       target_distance=rnd.random() * 3, target_eta=rnd.random() * 3, target_person=rnd.choice((None, 1)))
        raw = st.to_dict()
        sent = d.steady("z", raw, t=k * rnd.choice((0.1, 0.2, 5.0)))
        for key in ("count", "occupied", "moving", "still", "approaching", "target"):
            assert sent[key] == raw[key]
