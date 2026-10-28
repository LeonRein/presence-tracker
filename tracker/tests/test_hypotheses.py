"""A sitter no LD2450 sees for a quarter of an hour stays (MODEL.md 4.1, 4.3). The grids of a stay's
detectability and amplitude (the grid keeps long unseen sitting as the Gamma prior says, the joint prior
has the measured marginals) are tested in the Rust core (params/shapes.rs); so is the bookkeeping of the hypotheses (5.1, 5.6: merged before the
cut, the cut and the evidence, a track's end with its ghost alternative all but gone, shifting the
odds) is tested in the Rust core (core/presence-core/src/filter/tests.rs, merge.rs)."""



from presence_tracker.filter import Tracker
from presence_tracker.sim import Person, simulate
from presence_tracker.frames import Frame

from test_existence import known
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def test_a_sitter_no_ld2450_sees_for_a_quarter_of_an_hour_stays():
    # nothing known; somebody comes in and sits; then neither LD2450 sees them for 15 min, only the
    # LD2410C's energies (simulated, they fluctuate) say somebody is there: the light is on most of
    # the time and at the end, and nobody else is made up (the grid 0.89 of the time, end 0.98; the
    # quadrature until 0.21 0.86, 0.97, log evidence -14)
    config = flat_config(entry=True)
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 3.0), (3.05, 3.0), start=5, pauses={3: 1200}))
    sensors = sim_sensors(config)
    sit = a.waypoints[3][0]
    for s in sensors:
        s.blind_to, s.blind_after, s.blind_until = (0,), sit + 10, sit + 910
    crowd = Tracker(config, start=0.0)
    on = []
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    for t, sid, frame in simulate([a], sensors, sit + 900, walls=config.wall_segments):
        crowd.process_frame(sid, t, Frame.of(frame))
        if t > sit + 10:
            on.append(1 - crowd.count_distribution()["wohn"][0] > c)
    assert sum(on) / len(on) > 0.85 and on[-1], sum(on) / len(on)
    assert known(crowd) < 1.1, known(crowd)
