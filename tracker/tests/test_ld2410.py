"""The LD2410C's energy model (ld2410.py, MODEL.md 4.3)."""

import numpy as np

from presence_tracker import ld2410
from presence_tracker.filtermodel import STILL, WALK, Model
from presence_tracker.filter import Tracker
from presence_tracker.model import Config
from presence_tracker.sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


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


def test_the_amplitude_grid_is_the_prior_on_a_grid_in_log_g():
    # MODEL.md 4.3: the posterior of a stay's amplitude g on a grid in log g with the prior's mass per
    # cell. The quadrature's lowest node was 0.58: a person giving back a third of the profile (7.10.
    # 22:12, Bad) was explained worse than an echo source at its level 0.3 (that it now carries one:
    # likelihood.rs, the_amplitude_grid_carries_a_weak_reflector, with these numbers)
    from presence_tracker.filtermodel import Shapes
    m = Model()
    sh = Shapes(m)
    amp, w = sh.amp, sh.amp_w
    assert abs(float(w @ amp) - 1.0) < 1e-6
    assert abs(float(w @ (amp - 1) ** 2) - 1 / m.ld_amp_shape) < 0.02  # Gamma(6, 6): variance 1/6
    assert amp.min() < 0.3 and amp.max() > 2.0


def test_the_background_is_learned_per_sensor_and_starts_over_when_moved(tmp_path):
    m = Model()
    cfg = Config.from_dict({"sensors": [{"id": "a", "x": 0, "y": 0, "heading": 0, "placed": True}], "zones": []})
    bg = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget)
    bg.use(cfg)
    st = _stats(2 * ld2410.prior(m), n=1, dt=3600.0)
    b = ld2410.prior(m)
    bg.learn("a", b, b[None, :], np.ones(1), st, ld2410.cell_values(m, *m.ld_shape))
    assert np.allclose(bg.b("a"), 2 * ld2410.prior(m), rtol=0.2)
    bg.save(tmp_path / "ld.json")
    other = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget)
    other.load(tmp_path / "ld.json")
    other.use(cfg)
    assert np.allclose(other.b("a"), bg.b("a"))
    moved = Config.from_dict({"sensors": [{"id": "a", "x": 1, "y": 0, "heading": 0, "placed": True}], "zones": []})
    other.use(moved)
    assert np.allclose(other.b("a"), ld2410.prior(m))


def test_a_learned_part_without_its_time_is_dropped_on_load():
    # a sensor's num without its den (a file broken by hand): until 0.28 kept, and b() failed at every
    # LD2410C frame of it (KeyError: a model failure each time); now that sensor starts from the prior
    m = Model()
    cells = [1.0] * len(ld2410.prior(m))
    bg = ld2410.Background(ld2410.prior(m), m.ld_prior_time, m.ld_forget)
    bg.load_dict({"num": {"a": cells, "b": cells}, "den": {"b": 10.0},
                  "num_a": {"a": cells}, "den_a": {"a": 5.0}, "quiet": {"a": 1.0}, "echoes": {"a": 0.1}})
    assert set(bg.num) == set(bg.den) == {"b"} and set(bg.num_a) == set(bg.den_a) == {"a"}
    assert np.all(np.isfinite(bg.b("a"))) and np.all(np.isfinite(bg.b("b")))


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


def test_the_end_of_a_track_is_not_the_end_of_the_stay():
    # MODEL.md 4.3, 5.4: a stay whose amplitude a track measured keeps it on the tiles (Swerling III:
    # fixed over a stay), drawn anew only with the detectability or with a new stay; what nothing
    # measured has g = 1, and back on a track the measured amplitude comes along
    from presence_tracker.gauss import Gauss
    from presence_tracker.hidden import Hidden
    crowd = Tracker(flat_config(), start=0.0)
    m, sh, tl = crowd.m, crowd.shapes, crowd.tiles
    g = Gauss.source(1, np.array([3.0, 3.0]), 0.01, m, sh)
    g.logw = np.log(np.array([1.0 - 1e-9, 1e-9]))  # standing
    a = int(np.argmin(abs(sh.amp - 0.3)))
    ka = np.zeros_like(g.ka)
    ka[:, a] = g.kw
    g.ka = ka
    h = g.to_tiles(tl)
    assert h.measured
    S = h.still.sum(axis=(0, 1))
    c = int(np.argmax(S))
    assert np.allclose(h.amp[:, a, c], 1.0)
    fresh = Hidden.at_place(tl, 0)
    assert not fresh.measured and np.allclose(fresh.amp[:, -1, :], 1.0)
    # a minute on: the stay goes on, a few draw anew (detectability, getting up and sitting down)
    for _ in range(60):
        h.move(1.0)
    kept = float(h.still.sum(axis=0)[:, c] @ h.amp[:, a, c] / h.still.sum(axis=0)[:, c].sum())
    assert 0.8 < kept < 1.0
    # mixed with somebody nothing measured: by their standing mass
    mix = Hidden.mixture([(0.5, h), (0.5, fresh)])
    assert mix.measured
    Sh, Sf = h.still.sum(axis=0)[:, c], fresh.still.sum(axis=0)[:, c]
    want = (Sh * h.amp[:, a, c]) / (Sh + Sf)
    assert np.allclose(mix.amp[:, a, c], want)
    # a track starts on them again: its amplitude is the measured one
    new, _ = Gauss.from_tiles(h, tl, np.ones(tl.n), np.ones(tl.n), 2, tl.centers[c], 0.01, m)
    assert float(new.amw @ sh.amp) < 0.5
    # hours on: what was measured is gone (nothing left measured at all: g = 1 again)
    h.leap(6 * 3600.0)
    assert float(h.amp[:, -1, c].min()) > 0.99


def _two_rooms():
    """Two rooms 4 x 4 m side by side, a door between at (4, 2), one sensor in each looking at it."""
    def rect(zid, x0, y0, x1, y1):
        return {"id": zid, "name": zid, "kind": "room", "shape": "rect", "points": [[x0, y0], [x1, y1]],
                "anchor": [(x0 + x1) / 2, (y0 + y1) / 2], "entry": False}

    def w(a, b):
        return {"points": [list(a), list(b)], "kind": "wall"}
    return Config.from_dict({
        "sensors": [{"id": "a", "x": 0.05, "y": 2.0, "heading": 0, "placed": True},
                    {"id": "b", "x": 7.95, "y": 2.0, "heading": 180, "placed": True}],
        "zones": [rect("links", 0, 0, 4, 4), rect("rechts", 4, 0, 8, 4)],
        "walls": [w((0, 0), (8, 0)), w((8, 0), (8, 4)), w((8, 4), (0, 4)), w((0, 4), (0, 0)), w((4, 0), (4, 4))],
        "doors": [{"id": "tuer", "x": 4.0, "y": 2.0, "width": 0.9}],
    })


def test_the_ld2410c_sees_another_room_as_far_as_measured():
    """MODEL.md 4.3 "Blick des LD2410C": the measured view of the other room scales the sight there;
    where it gives the LD2410C nothing above FLOOR the place is out of its view (weighed by 1), its own
    room keeps the geometric sight."""
    pts = np.array([[2.0, 2.0], [5.0, 2.0]])  # in front of a: its own room, and through the door
    base = Tracker(_two_rooms(), start=0.0)
    _, _, s0 = base._ld_geometry(0, pts)
    assert s0[0] > 0.5 and s0[1] > 0.5

    class Half(Model):
        ld_view = {"links<-rechts": 0.5}

    class Blind(Model):
        ld_view = {"links<-rechts": 0.0}

    half = Tracker(_two_rooms(), start=0.0, model=Half())
    _, _, s = half._ld_geometry(0, pts)
    assert np.allclose(s, [s0[0], 0.5 * s0[1]])
    _, _, s = half._ld_geometry(0, pts, view=False)
    assert np.allclose(s, s0)
    blind = Tracker(_two_rooms(), start=0.0, model=Blind())
    assert np.allclose(blind._ld_geometry(0, pts)[2], [s0[0], 0.0])
    # the other sensor is untouched; no tile of the other room is in a's view any more
    assert np.allclose(blind._ld_geometry(1, pts)[2], base._ld_geometry(1, pts)[2])
    i, j = blind.world.cell_of(blind.tiles.centers)
    other = blind.room_of[i, j] == blind.rooms.index("rechts")
    assert other[base._ld_tiles(0)[0]].any()
    assert not other[blind._ld_tiles(0)[0]].any()
