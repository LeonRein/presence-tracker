"""What the app learns online must not learn the filter's own errors back (MODEL.md 4.3, 10 "Lernen
aus dem eigenen Urteil"): the LD2410C background is learned by online EM with the energies that were
capped taken for what they were, not where an echo source holds the energy; the echo sources' rate
only where nobody is in view and judged without itself; the destination map is output only
(test_destination.py)."""

import numpy as np

from presence_tracker import ld2410
from presence_tracker.filter import Tracker
from presence_tracker.filtermodel import Model
from presence_tracker.sim import Person, SimSensor, simulate

from test_filter import FLUR_DOOR, flat_config
from test_frames import walk


def _frames(mu, seconds, seed=1):
    """Stats of `seconds` s of frames whose energies are Gamma(alpha, mean mu) (independent, every
    0.1 s), capped as the firmware caps them."""
    m = Model()
    alpha = ld2410.cell_values(m, *m.ld_shape)
    rng = np.random.default_rng(seed)
    st = ld2410.Stats()
    for _ in range(int(seconds * 10)):
        e = np.minimum(np.round(rng.gamma(alpha, mu / alpha)), 100.0)
        st.add(e, 0.1)
    return st, alpha


def test_capped_energies_count_for_what_they_were():
    # somebody 1.8 m in front of the sensor puts 160 into a still gate whose background is 5: the
    # firmware reports 100 in most frames. Until 0.20 those counted as 100, and the background's share
    # of them made it learn 3 instead of 5 (simulation: 5.0 -> 2.2 in an hour, and an energy of 5
    # after the person left looked like somebody)
    m = Model()
    b = ld2410.prior(m)
    mu = b.copy()
    mu[9] += 160.0
    st, alpha = _frames(mu, 3600)
    assert st.t_cens[9] > 0.5 * st.time
    bg = ld2410.Background(b, m.ld_prior_time, m.ld_forget)
    bg.learn("a", b, mu[None, :], np.ones(1), st, alpha)
    learned = bg.b("a")
    assert abs(learned[9] / b[9] - 1) < 0.1, learned[9]
    assert np.all(np.abs(learned / b - 1) < 0.1)
    # the old share of a capped 100
    old = (m.ld_prior_time * b[9] + b[9] / mu[9] * (st.t_e[9] + 100 * st.t_cens[9])) / (m.ld_prior_time + st.time)
    assert old < 0.7 * b[9]


def test_no_background_is_learned_while_something_is_seen_or_an_echo_source_holds_the_energy():
    m = Model()
    b = ld2410.prior(m)
    st, alpha = _frames(b * 3, 600)
    bg = ld2410.Background(b, m.ld_prior_time, m.ld_forget)
    bg.learn("a", b, b[None, :], np.ones(1), st, alpha, off=0.0)
    assert np.allclose(bg.b("a"), b)
    seen = ld2410.Stats()  # the same frames with a target of the LD2450 in sight
    seen.add(3 * b, 600.0, True)
    bg.learn("a", b, b[None, :], np.ones(1), seen, alpha)
    assert np.allclose(bg.b("a"), b)
    bg.learn("a", b, b[None, :], np.ones(1), st, alpha, off=1.0)
    assert bg.b("a")[0] > 1.5 * b[0]


def test_the_echo_rate_counts_where_nobody_is_and_without_itself():
    m = Model()
    bg = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget, 1e-4, 3600.0)
    bg.learn_echoes("a", 0.5, 1e-4, nobody=0.0, seconds=3600.0)  # somebody in view: nothing
    assert abs(bg.rate("a") / 1e-4 - 1) < 1e-12
    bg.learn_echoes("a", 0.5, 1e-3, nobody=1.0, seconds=3600.0)
    # judged with 10 times the prior's rate: odds 1 -> 0.1 with the prior's
    assert abs(bg.echoes["a"] - 1 / 11) < 1e-9
    assert abs(bg.rate("a") - (0.36 + 1 / 11) / 7200) < 1e-12


def _sit(seat, minutes, leave=0.0):
    """Somebody comes in and sits at seat for minutes; the LD2450s see them for about a second every
    15 minutes (still_dropout). Returns the tracker, P(occupied) every 10 min while they sit, and the
    largest P(occupied) from 20 s after they left."""
    config = flat_config(entry=True)
    pts = [(-1.0, 4.0), FLUR_DOOR, seat, (seat[0] + 0.05, seat[1])] + ([FLUR_DOOR, (-1.0, 4.0)] if leave else [])
    p = Person(walk(*pts, start=5, pauses={3: minutes * 60}))
    tr = Tracker(config, start=0.0, people=["outside"])
    sensors = [SimSensor(s, still_dropout=1.0, still_gap=900.0) for s in config.sensors]
    sat, gone = p.waypoints[3][0], p.waypoints[-1][0]
    occupied, after, nxt = [], 0.0, sat + 600
    for t, sid, frame in simulate([p], sensors, gone + leave, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
        if t >= nxt and t < sat + minutes * 60:
            occupied.append(1 - tr.count_distribution()["wohn"][0])
            nxt += 600
        if leave and t > gone + 20:
            after = max(after, 1 - tr.count_distribution()["wohn"][0])
    return tr, occupied, after


def test_a_sitter_for_an_hour_is_not_learned_as_background():
    # MODEL.md 4.3, 10: somebody sits 1.8 m in front of sensor a for an hour, the LD2450s rarely see
    # them. The background stays what the empty room gives (until 0.20: gate 2 of sensor a 5.0 ->
    # 2.2, its echo rate 0.33 -> 0.73 per hour), the person stays, and when they leave nobody is left
    m = Model()
    c = 2 / 3
    tr, occupied, after = _sit((1.3, 1.3), 60, leave=60.0)
    prior = ld2410.prior(m)
    for s in ("a", "b"):
        assert np.all(np.abs(tr.ld_background.b(s) / prior - 1) < 0.3), (s, tr.ld_background.b(s))
        assert tr.ld_background.rate(s) < 1.3 * m.ld_echo_rate, (s, tr.ld_background.rate(s) * 3600)
    assert min(occupied) > c, occupied
    assert after < c, after


def test_a_new_sensor_learns_an_empty_room_that_is_louder_than_the_prior():
    # the bedroom sensor of 7.10.: its empty room gave 7.8 in every still gate, the prior is 5. While
    # it learns, nobody may appear there (learning only where the filter sees nobody was tried: a
    # phantom held the energy and the background never learned it, 4 h light in the empty bedroom)
    m = Model()
    config = flat_config(entry=True)
    louder = ld2410.prior(m) * 1.56
    nobody = Person([(0.0, -1.0, 4.0), (1.0, -1.0, 4.0)])  # out in the stairwell, gone
    tr = Tracker(config, start=0.0, people=["outside"])
    sensors = [SimSensor(s) for s in config.sensors]
    for s in sensors:
        s.ld_background = louder
    worst = 0.0
    for t, sid, frame in simulate([nobody], sensors, 1800, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
        if t > 60:
            worst = max(worst, 1 - tr.count_distribution()["wohn"][0])
    assert worst < 2 / 3, worst
    for s in ("a", "b"):  # from 0.64 of it (the prior) to the empty room
        ratio = tr.ld_background.b(s)[9:] / louder[9:]
        assert abs(ratio.mean() - 1) < 0.1 and np.all(np.abs(ratio - 1) < 0.25), tr.ld_background.b(s)


def test_a_sitter_the_model_starts_with_is_not_learned_where_an_echo_source_holds_them():
    # nothing known (a start of the app, a new sensor), somebody already sits 1.8 m in front of sensor
    # a. The filter takes them for an echo source of a that never ends (it does not weigh a person
    # without a track against an echo source the way it weighs the source against them, MODEL.md 9);
    # a's background does not learn their energy (until 0.20: gate 2 5.0 -> 10.6 in an hour). Sensor
    # b, 5 m away, has nothing that explains their energy and learns part of it (MODEL.md 10)
    m = Model()
    config = flat_config(entry=True)
    seat = (1.3, 1.3)
    p = Person([(0.0, *seat), (3600.0, seat[0] + 0.05, seat[1])])
    tr = Tracker(config, start=0.0)
    sensors = [SimSensor(s, still_dropout=1.0, still_gap=900.0) for s in config.sensors]
    for t, sid, frame in simulate([p], sensors, 3600, walls=config.wall_segments):
        tr.process_frame(sid, t, frame)
    prior = ld2410.prior(m)
    assert np.all(np.abs(tr.ld_background.b("a") / prior - 1) < 0.3), tr.ld_background.b("a")
