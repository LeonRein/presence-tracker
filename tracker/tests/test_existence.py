"""Nobody is invented for long (MODEL.md 1.3, 5.5): a track that is no person leaves nobody behind,
somebody coming back from a room without a sensor is the one who went there, a sitter the sensors
do not see for minutes stays, and no strip of a room is out of every sensor's sight."""

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate

from test_filter import BALCONY_DOOR, FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def in_house(crowd) -> float:
    """E[people in the house]."""
    d = crowd.place_distribution()["_house"]
    return float(sum(k * p for k, p in enumerate(d)))


def known(crowd) -> float:
    """E[known people that exist]."""
    w = crowd.hyp_weights()
    return float(sum(wi * sum(getattr(o, "r", 1.0) for o in h.people()) for wi, h in zip(w, crowd.hyps)))


def test_a_reflection_leaves_nobody_behind():
    # one person known to be out; a fixed reflection that only sensor a's LD2450 reports, for a
    # minute and a half: whatever it was taken for meanwhile, minutes later nobody is in the room
    # and nobody more is in the house
    config = flat_config(entry=True)
    sensors = sim_sensors(config)
    sensors[0].reflections = ((60.0, 150.0, 4.5, 3.5),)
    crowd = Tracker(config, start=0.0, people=["outside"])
    before = None
    for t, sid, frame in simulate([], sensors, 450, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        crowd.check()
        if before is None and t > 50:
            before = in_house(crowd)
    assert 1 - crowd.count_distribution()["wohn"][0] < 0.05
    assert in_house(crowd) < before + 0.05, (before, in_house(crowd))


def test_somebody_back_from_the_balcony_is_who_went_there():
    # nothing known; somebody comes in, sits, spends two minutes on the balcony and comes back:
    # one person, not a second one beside a first who stays out there
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.05, 2.5), BALCONY_DOOR, (7.0, 2.0), (7.05, 2.0),
                    BALCONY_DOOR, (4.0, 1.5), (4.05, 1.5), start=5, pauses={3: 60, 6: 120, 9: 60}))
    crowd = Tracker(config, start=0.0)
    before = 1 - crowd.place_distribution()["balkon"][0]  # unknown people out there (nothing known)
    for t, sid, frame in simulate([a], sim_sensors(config), a.waypoints[-1][0] - 1, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    after = 1 - crowd.place_distribution()["balkon"][0]
    assert 1 - crowd.count_distribution()["wohn"][0] > 0.95
    assert after < before + 0.02, (before, after)
    assert known(crowd) < 1.1, known(crowd)


def test_a_sitter_no_ld2450_sees_for_a_while_stays():
    # nothing known; somebody comes in and sits; ten seconds later neither LD2450 sees them for 90 s
    # (longer than any gap of the LD2450 alone on real sitters, 85 s; with the LD2410C at most 18 s,
    # MODEL.md 5.5), only the LD2410C's energies (4.2 m away) say somebody is there: the light stays
    # on throughout
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 3.0), (3.05, 3.0), start=5, pauses={3: 300}))
    sensors = sim_sensors(config)
    sit = a.waypoints[3][0]
    for s in sensors:
        s.blind_to, s.blind_after, s.blind_until = (0,), sit + 10, sit + 100
    crowd = Tracker(config, start=0.0)
    low = 1.0
    for t, sid, frame in simulate([a], sensors, sit + 160, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if t > sit + 10:
            low = min(low, 1 - crowd.count_distribution()["wohn"][0])
    assert low > config.params.light_cost / (config.params.light_cost + 1.0), low  # the light stays on
    assert known(crowd) < 1.1, known(crowd)


def test_a_person_in_a_walls_shadow_is_partly_seen():
    # a wall stub from (3, 2.5) to (3, 5) shades the room's upper right part from sensor a: a person
    # just inside the shadow is partly in sight (where their track would sit), far inside not, and
    # nobody behind a wall gets sight through it
    d = flat_config().to_dict()
    d["walls"].append({"points": [[3.0, 2.5], [3.0, 5.0]], "kind": "wall"})
    crowd = Tracker(Config.from_dict(d), start=0.0)
    si = crowd.sidx["a"]
    g = crowd._g(si, np.array([[2.5, 4.0], [3.15, 3.0], [4.5, 4.5], [7.0, 2.0]]))
    assert g[0] > 0.9  # in plain sight
    assert 0.1 < g[1] < 0.9  # just inside the shadow
    assert g[2] < 0.05  # far inside
    assert g[3] == 0.0  # on the balcony, behind the wall
    off = Tracker(Config.from_dict(d), start=0.0, model=_without_spread())
    assert off._g(off.sidx["a"], np.array([[3.15, 3.0]]))[0] == 0.0  # a point's sight: a sharp edge


def _without_spread():
    from presence_tracker.filtermodel import Model
    m = Model()
    m.sight_spread = False
    return m


def _nook_config():
    """flat_config with a nook behind a wall stub (x 3-6, y 4-5) that neither sensor sees, nor
    its LD2410C: a blind spot, entered from the room at x < 3."""
    d = flat_config(entry=True).to_dict()
    d["walls"].append({"points": [[3.0, 4.0], [6.0, 4.0]], "kind": "wall"})
    return Config.from_dict(d)


def _nook(tl):
    return [tl.cell_near((x, 4.5)) for x in (4.5, 5.0, 5.5, 5.8)]


def test_the_nook_is_unobserved():
    # how well the best sensor sees a person in each tile (MODEL.md 5.5): 0 in the nook, 1 in
    # plain view, in between at the nook's opening
    crowd = Tracker(_nook_config(), start=0.0, people=["outside"])
    tl = crowd.tiles
    o = tl.observed(range(len(crowd.sensors)))
    assert all(o[c] < 0.02 for c in _nook(tl)), [o[c] for c in _nook(tl)]
    assert o[tl.cell_near((3.0, 2.0))] > 0.95 and o[tl.cell_near((1.0, 4.5))] > 0.95
    assert 0.1 < o[tl.cell_near((3.6, 4.5))] < 0.9


def _hold_in_nook(r, minutes):
    """Somebody in the nook who exists with probability r, standing; then for this long no sensor
    measures anything. P(somebody in the room) afterwards."""
    config = _nook_config()
    crowd = Tracker(config, start=0.0, people=["outside"])
    h = crowd.hyps[0].hidden[0]
    h.out = 0.0
    for c in _nook(crowd.tiles):
        h.still[:, :, c] = crowd.shapes.stay_prior(ongoing=True)
    h.r = r
    h._normalize()
    for t, sid, frame in simulate([], sim_sensors(config), 60 * minutes, rate=2.0, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
    return 1 - crowd.count_distribution()["wohn"][0]


def test_nobody_stays_in_a_blind_spot_without_support():
    # somebody known for sure is in the nook nobody sees, and no sensor measures anything: nothing
    # could ever contradict them there, and the long stays (MODEL.md 3.1) would hold them for hours
    # (2 % of stays last more than an hour). Their record expires where nothing can test it (MODEL.md
    # 5.5): ten minutes later the room is dark
    assert _hold_in_nook(1.0, 10) < 0.05


def test_a_sitter_nothing_supports_for_two_minutes_stays():
    # nothing known; somebody comes in and sits in plain view. Then for two minutes - longer than
    # any gap between supporting measurements of real sitters (18.2 s, MODEL.md 5.5) - neither
    # LD2450 sees them, and there is no LD2410C: nothing supports them, nothing that would see them
    # if they walked away saw anything. The light stays on: in view, only the measurements decide
    # (until 0.22 their existence faded with 2 min, e^(-120/120) = 0.37 < 2/3)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 3.0), (3.05, 3.0), start=5, pauses={3: 400}))
    sensors = sim_sensors(config, ld2410=False)
    sit = a.waypoints[3][0]
    for s in sensors:
        s.blind_to, s.blind_after, s.blind_until = (0,), sit + 30, sit + 150
    crowd = Tracker(config, start=0.0)
    low = 1.0
    for t, sid, frame in simulate([a], sensors, sit + 150, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if t > sit + 30:
            low = min(low, 1 - crowd.count_distribution()["wohn"][0])
    assert low > config.params.light_cost / (config.params.light_cost + 1.0), low


def test_a_ghost_beside_a_blind_spot_leaves_nobody_behind():
    # one person known to be out; a fixed reflection right at the nook's opening that only sensor
    # a's LD2450 reports, for a minute and a half: whatever it was taken for meanwhile (a person who
    # then went into the nook nobody sees), twenty minutes later nobody is in the room and nobody
    # more is in the house
    config = _nook_config()
    sensors = sim_sensors(config)
    sensors[0].reflections = ((60.0, 150.0, 3.3, 4.5),)
    crowd = Tracker(config, start=0.0, people=["outside"])
    before = None
    worst = 0.0
    for t, sid, frame in simulate([], sensors, 150 + 1200, rate=4.0, walls=config.wall_segments):
        crowd.process_frame(sid, t, frame)
        if before is None and t > 50:
            before = in_house(crowd)
        if t > 150 + 300:
            worst = max(worst, 1 - crowd.count_distribution()["wohn"][0])
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    assert worst < c, worst  # dark five minutes after the reflection
    assert 1 - crowd.count_distribution()["wohn"][0] < 0.05
    assert in_house(crowd) < before + 0.05, (before, in_house(crowd))

