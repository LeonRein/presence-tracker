"""The learners learn where the Python filter learned and are read after it (docs/rust-core.md 2.0): one
test per row of the table. The last row (nothing in a pause) is test_pause's."""

import numpy as np

from presence_tracker.filter import Tracker
from presence_tracker.sim import Person, SimSensor, simulate
from presence_tracker.frames import Frame

from test_filter import FLUR_DOOR, flat_config
from test_frames import walk


def _walked(seconds):
    """Somebody comes into the flat and stands in the middle; the tracker after `seconds`, and the frames
    after them."""
    config = flat_config(entry=True)
    p = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.0), (3.05, 2.0), start=2, pauses={3: 600}))
    tr = Tracker(config, start=0.0, people=["outside"])
    frames = list(simulate([p], [SimSensor(s) for s in config.sensors], seconds + 20.0, walls=config.wall_segments))
    for t, sid, frame in frames:
        if t > seconds:
            break
        tr.process_frame(sid, t, Frame.of(frame))
    return tr, [f for f in frames if f[0] > seconds]


def _spy(log, obj, name, tag=None, args=None):
    """Logs the calls of obj.name as tag (a function of the arguments, or a name), with what args makes of
    the arguments."""
    f = getattr(obj, name)

    def spied(*a, **k):
        log.append((tag(*a) if callable(tag) else tag or name, None if args is None else args(*a)))
        return f(*a, **k)
    setattr(obj, name, spied)


def _in_order(log, *names):
    """Whether each name was called, the first calls in this order."""
    tags = [tag for tag, _ in log]
    assert all(n in tags for n in names), (names, tags)
    at = [tags.index(n) for n in names]
    return at == sorted(at)


def test_the_ghost_map_learns_a_birth_before_a_track_born_in_the_same_frame_reads_it():
    # row 1: the total rate before the watch, add_watch and fade, the births of the tracks ended in
    # this frame, then the rate at the new track's birth
    tr, frames = _walked(12.0)
    # the next frame of a sensor tracking the person, its target 1 m closer to the sensor: no
    # continuation (more than the LD2450 tracks' gate), its track ends and a new one is born
    t, sid, frame = next((t, sid, f) for t, sid, f in frames
                         if f["targets"] and tr._c.live_tracks(tr.sidx[sid]))
    first = frame["targets"][0]
    frame = dict(frame, targets=[dict(first, y=first["y"] - 1000)] + frame["targets"][1:])
    log = []
    gm = tr.ghost_map
    for name in ("add_watch", "fade", "add_birth"):
        _spy(log, gm, name)
    _spy(log, tr, "_ghost_rate_total", "rate_total")
    _spy(log, tr, "_ghost_rate", "rate_at_birth")
    _spy(log, tr, "_call", lambda name, *a: name)
    _spy(log, tr, "_end", "end")
    tr.process_frame(sid, t, Frame.of(frame))
    assert _in_order(log, "rate_total", "watch", "add_watch", "fade", "end", "add_birth", "rate_at_birth",
                     "branch_all")


def _weighings(tr, frames, log):
    for t, sid, frame in frames:
        tr.process_frame(sid, t, Frame.of(frame))
        if sum(tag == "learn_echoes" for tag, _ in log) >= 2:
            return
    raise AssertionError("no two weighings")


def test_the_ld2410c_background_learns_from_b_and_the_weights_before_the_weighing():
    # row 2: b and the hypotheses' weights read at the start of the weighing (before its frames weigh
    # them), learned with them afterwards
    tr, frames = _walked(8.0)
    log, before = [], []
    bg = tr.ld_background
    _spy(log, bg, "b", args=lambda sid: sid)
    _spy(log, bg, "learn", args=lambda sid, b, mus, w, *rest: (sid, np.array(b), np.array(w)))
    _spy(log, bg, "learn_echoes")
    call = tr._call

    def weights(name, *a):
        if name == "ld_weigh":
            before.append((tr.sensors[a[0]], np.array(a[1]), np.array(call("hyp_weights"))))
        return call(name, *a)
    tr._call = weights
    _weighings(tr, frames, log)
    learned = [arg for tag, arg in log if tag == "learn"]
    assert len(learned) == len(before[:len(learned)]) >= 2
    for (sid, b, w), (sid0, b0, w0) in zip(learned, before):
        assert sid == sid0 and np.array_equal(b, b0) and np.array_equal(w, w0)


def test_the_echo_rate_learns_with_the_rate_read_before_the_sources_moved_on():
    # row 3: learn_echoes right after learn, with the rate read before the weighing (echo.predict is in it)
    tr, frames = _walked(8.0)
    log = []
    bg = tr.ld_background
    rates = []
    rate = type(bg).rate

    def read(sid):
        rates.append((sid, rate(bg, sid)))
        log.append(("rate", None))
        return rates[-1][1]
    bg.rate = read
    _spy(log, bg, "learn")
    _spy(log, bg, "learn_echoes", args=lambda sid, began, r, nobody, seconds: (sid, r))
    _weighings(tr, frames, log)
    tags = [tag for tag, _ in log if tag in ("rate", "learn", "learn_echoes")]
    assert tags[:6] == ["rate", "learn", "learn_echoes"] * 2, tags
    used = [arg for tag, arg in log if tag == "learn_echoes"]
    assert used == rates[:len(used)]


def test_the_destination_map_observes_after_the_move_and_the_recycling():
    # row 4: in step, after _flush and _recycle, at most every MAX_STEP
    tr, frames = _walked(8.0)
    log = []
    _spy(log, tr, "_flush", "flush")
    _spy(log, tr, "_recycle", "recycle")
    _spy(log, tr._dest_learner, "observe")
    t = frames[0][0]
    tr._recycled = -1e9  # recycling due
    tr._dest_t = -1e9
    tr._flushed = tr.now - 10.0
    tr.step(t)
    assert [tag for tag, _ in log] == ["flush", "recycle", "observe"]
