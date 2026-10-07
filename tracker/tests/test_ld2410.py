"""The LD2410C's energy model (ld2410.py, MODEL.md 4.3)."""

import math

import numpy as np

from presence_tracker import ld2410
from presence_tracker.filtermodel import STILL, WALK, Model
from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


def test_upper_incomplete_gamma():
    for a in (1.6, 2.0, 2.5, 3.0):
        for x in (0.1, 1.0, 3.0, 3.6, 10.0, 50.0):
            t = np.linspace(x, x + 200, 400001)
            q = np.trapezoid(t ** (a - 1) * np.exp(-t), t) / math.gamma(a)
            assert abs(float(ld2410.log_gammaq(a, x)) - math.log(q)) < 1e-4


def test_the_profile_peaks_at_the_persons_gate_and_falls_with_distance():
    m = Model()
    near, far = ld2410.expected(m, [0, 0], [1.5, 4.5], [True, True])[STILL]
    assert int(np.argmax(near[9:])) + 2 in (2, 3) and int(np.argmax(far[9:])) + 2 in (5, 6)
    assert near.max() > 5 * far.max()
    walking = ld2410.expected(m, [0], [3.0], [True])[WALK][0]
    assert walking[:9].max() > ld2410.expected(m, [0], [3.0], [True])[STILL][0][:9].max()
    out_of_beam = ld2410.expected(m, [80], [2.0], [True])[STILL][0]
    assert out_of_beam.max() == 0


def _stats(e, n=11, dt=0.09):
    st = ld2410.Stats()
    for _ in range(n):
        st.add(np.asarray(e, dtype=float), dt)
    return st


def test_a_person_explains_what_the_background_does_not():
    m = Model()
    lik = ld2410.Likelihood(m)
    b = ld2410.prior(m)
    S = ld2410.expected(m, [0], [2.0], [True])[STILL][0]
    # 10 s of frames: they count by their correlation time (MODEL.md 4.3), 1 s would say little
    with_person = _stats(np.minimum(b + S, 100), n=110)
    empty = _stats(b, n=110)
    assert with_person.log_ratio((b + S)[None, :], b, lik)[0] > 5
    assert empty.log_ratio((b + S)[None, :], b, lik)[0] < -5
    # the same excess in every gate of a kind is the common gain, not somebody
    uniform = _stats(1.5 * b, n=110)
    far = ld2410.expected(m, [0], [5.5], [True])[STILL][0]
    assert uniform.log_ratio((b + far)[None, :], b, lik)[0] < 1.0


def test_the_background_is_learned_per_sensor_and_starts_over_when_moved(tmp_path):
    m = Model()
    cfg = Config.from_dict({"sensors": [{"id": "a", "x": 0, "y": 0, "heading": 0, "placed": True}], "zones": []})
    bg = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget)
    bg.use(cfg)
    st = _stats(2 * ld2410.prior(m), n=1, dt=3600.0)
    bg.learn("a", np.ones(ld2410.CELLS), st)
    assert np.allclose(bg.b("a"), 2 * ld2410.prior(m), rtol=0.2)
    bg.save(tmp_path / "ld.json")
    other = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget)
    other.load(tmp_path / "ld.json")
    other.use(cfg)
    assert np.allclose(other.b("a"), bg.b("a"))
    moved = Config.from_dict({"sensors": [{"id": "a", "x": 1, "y": 0, "heading": 0, "placed": True}], "zones": []})
    other.use(moved)
    assert np.allclose(other.b("a"), ld2410.prior(m))


def test_echo_sources_begin_live_end_and_are_counted():
    m = Model()
    e = ld2410.Echoes(m)
    e.predict(1.0, m, 0.001)
    assert abs(e.on() - 0.001) < 1e-6
    e.weigh(np.full(len(e.p) - 1, 5.0))  # the energies fit any source much better than none
    assert e.on() > 0.1
    assert abs(e.began() - e.on()) < 1e-9  # all of it began just now
    for _ in range(int(10 * m.ld_echo_life)):
        e.predict(1.0, m, 0.0)
    assert e.on() < 0.01  # and it ends
    bg = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget, 1e-4, 3600.0)
    bg.learn("a", np.ones(ld2410.CELLS), _stats(ld2410.prior(m), n=1, dt=3600.0))
    bg.learn_echoes("a", 10.0)
    assert abs(bg.rate("a") - (0.36 + 10) / 7200) < 1e-9


def test_all_energies_0_are_no_measurement():
    """Somebody sits 1.1 m in front of sensor a, both LD2450 track them; from 30 s a's LD2410C reports
    every energy 0 - the firmware's "no value" (NaN -> 0), never in 1.5 million recorded frames. Weighed,
    it outvoted both tracks: P 4e-6 (BUGS 14). It is no measurement, like a frame without energies."""
    config = flat_config(entry=True)
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    p = Person(walk((-1.0, 4.0), FLUR_DOOR, (0.8, 0.8), (0.82, 0.8), start=1, pauses={2: 200}))
    tr = Tracker(config, start=0.0, people=["outside"])
    low = 1.0
    for t, sid, frame in simulate([p], sim_sensors(config), 120, walls=config.wall_segments):
        if sid == "a" and t > 30 and frame.get("ld2410"):
            frame["ld2410"].update(move_gates=[0] * 9, still_gates=[0] * 9, moving=False, still=False)
        tr.process_frame(sid, t, frame)
        if t > 40:
            low = min(low, 1 - tr.count_distribution()["wohn"][0])
    assert low > c, low
    assert tr.runtime["a"].ld_e is None  # nothing taken as its energies
