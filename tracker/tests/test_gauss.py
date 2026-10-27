"""The Gaussian people (MODEL.md 5.2) and what makes the filter deterministic, through the tracker.
How a mixture moves, meets walls and is weighed is tested in the Rust core
(core/presence-core/src/filter/mixture.rs)."""



from presence_tracker.sim import Person

from test_filter import FLUR_DOOR, flat_config, run
from test_frames import walk


def test_the_same_data_give_the_same_result():
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (3.1, 2.5), start=3, pauses={2: 30}))
    end = a.waypoints[-1][0]
    _, s1 = run(config, [a], end, None)
    _, s2 = run(config, [a], end, None)
    assert [x[1] for x in s1] == [x[1] for x in s2]


def test_getting_up_after_a_long_stay_is_followed():
    # somebody sits for ten minutes and then walks off: a component with a small weight, lifted by
    # the first measurements of the walk - not a ghost, no second person (the morning episode)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3, 2.5), (3.05, 2.5), (5.0, 1.0), (5.05, 1.0), start=3,
                    pauses={2: 600, 4: 20}))
    tr, samples = run(config, [a], a.waypoints[-1][0], None)
    t_up = a.waypoints[3][0]
    after = [n for t, _, n in samples if t_up + 3 <= t <= a.waypoints[-1][0]]
    assert after and all(n == 1 for n in after), after
