"""The particle filter over the residents (pf.py) on simulated scenes."""

from presence_tracker.pf import PersonFilter
from presence_tracker.sensormodel import SensorModel
from presence_tracker.sim import Person, simulate

from test_tracker import DOOR, balcony_config, room_config, sim_sensors, walk


def run_pf(config, people, duration, sensors=None, walls=False, every=0.5):
    sensors = sensors or sim_sensors(config)
    pf = PersonFilter(config, SensorModel(config), n=400)
    samples = []
    next_sample = 0.0
    kw = {"walls": config.wall_segments} if walls else {}
    for t, sid, frame in simulate(people, sensors, duration, **kw):
        pf.process_frame(sid, t, frame)
        if t >= next_sample:
            pf.step(t)
            samples.append((t, pf.summary()))
            next_sample = t + every
    return pf, samples


def in_room(summary):
    return sum(1 for pe in summary["people"] if pe["room"] > 0.5)


def test_someone_sitting_through_a_long_dropout_stays():
    config = room_config(residents=1)
    person = Person(walk(DOOR, (3, 2.5), (4, 2), start=2, pauses={2: 120}))
    sensors = sim_sensors(config, still_dropout=0.1, still_gap=25)
    pf, samples = run_pf(config, [person], 100, sensors)
    assert all(in_room(s) == 1 for t, s in samples if 20 <= t <= 100)
    x, y = next(s for t, s in samples if t >= 90)["people"][0]["pos"]
    assert abs(x - 4) < 0.6 and abs(y - 2) < 0.6


def test_two_people_are_two():
    config = room_config(residents=2)
    a = Person(walk(DOOR, (1.5, 1.5), start=2, pauses={1: 60}))
    b = Person(walk(DOOR, (4.5, 3.0), start=6, pauses={1: 60}))
    pf, samples = run_pf(config, [a, b], 50)
    assert all(in_room(s) == 2 for t, s in samples if 20 <= t <= 50)


def test_out_to_the_balcony_and_back():
    config = balcony_config()
    config.params.residents = 1
    balcony = next(iter(config.regions))
    person = Person(walk(DOOR, (3, 2.5), (5.5, 2.0), (7.0, 2.0), (5.0, 2.0), (3, 3), start=2, pauses={3: 40}))
    pf, samples = run_pf(config, [person], person.waypoints[-1][0] + 3, walls=True)
    on_balcony = [s for t, s in samples if 25 <= t <= 45]
    assert all(s["places"][balcony]["occupied"] > 0.5 and in_room(s) == 0 for s in on_balcony)
    end = person.waypoints[-1][0]
    assert all(in_room(s) == 1 for t, s in samples if end - 1 <= t)
