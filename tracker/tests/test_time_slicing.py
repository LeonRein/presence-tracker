"""The model must not depend on how time is cut into frames and steps (MODEL.md 3, 4.1): a frame that
stands for an idle gap must weigh like the empty frames it stands for, the bodies must not push two
clouds apart further each time the clouds are stepped, and a long step must move the particles like
many short ones."""

import math

import numpy as np

from presence_tracker.cloud import STILL, WALK, Cloud, Motion
from presence_tracker.crowd import FRAME, Crowd
from presence_tracker.world import OBSERVED

from test_crowd import flat_config


def _cloud_at(crowd, pos, mode, last_hit, n=1):
    c = Cloud(n, len(crowd.sensors), crowd.rng)
    c.place[:] = OBSERVED
    c.pos[:] = pos
    c.mode[:] = mode
    c.last_hit[:] = last_hit
    return c


def test_a_hit_after_an_idle_gap_weighs_like_the_empty_frames_before_it():
    # the firmware leaves out empty frames (MODEL.md 4.1): a detection after a gap of 5 s means
    # 5 s of empty frames, then a hit - not "a hit sometime in these 5 s"
    crowd = Crowd(flat_config(), start=0.0, people=["observed"])
    sid = crowd.sensors[0]
    for mode in (STILL, WALK):
        for tau in (0.0, 10.0, 60.0):
            for gap in (1.0, 5.0):
                t = 1000.0
                c = _cloud_at(crowd, (2.0, 2.0), mode, t - gap - tau)
                miss, hit = crowd._detection(sid, c, np.array([0]), t, gap)
                # the same time as single frames
                n = int(round(gap / FRAME))
                p, tt = 1.0, t - gap
                for _ in range(n - 1):
                    tt += FRAME
                    m, _ = crowd._detection(sid, c, np.array([0]), tt, FRAME)
                    p *= m[0]
                _, h = crowd._detection(sid, c, np.array([0]), t, t - tt)
                assert math.isclose(hit[0], p * h[0], rel_tol=0.1), (mode, tau, gap, hit[0], p * h[0])
                assert math.isclose(miss[0], p * (1 - h[0]), rel_tol=0.1)


def test_somebody_who_came_in_during_the_gap_is_judged_from_then_on():
    # a particle that came out of a door during the gap was not there to be seen before
    crowd = Crowd(flat_config(), start=0.0, people=["observed"])
    sid = crowd.sensors[0]
    t = 1000.0
    c = _cloud_at(crowd, (2.0, 2.0), WALK, t - 0.3)
    _, hit = crowd._detection(sid, c, np.array([0]), t, 5.0)
    _, hit_short = crowd._detection(sid, c, np.array([0]), t, 0.3)
    assert math.isclose(hit[0], hit_short[0])
    # came out in this very step: judged by this frame alone
    c = _cloud_at(crowd, (2.0, 2.0), WALK, t)
    _, hit = crowd._detection(sid, c, np.array([0]), t, 5.0)
    assert math.isclose(hit[0], crowd._pd(sid, c.pos[:1])[0], rel_tol=0.05)


def test_bodies_do_not_push_further_without_news():
    # two overlapping clouds; nothing new happens: the body term (MODEL.md 3.1) holds once, it does not
    # sharpen every time it is evaluated
    crowd = Crowd(flat_config(), start=0.0, n=400, people=["observed", "observed"])
    for person in crowd.people:
        c = person.cloud
        c.pos[:] = crowd.rng.normal((3.0, 2.5), 0.4, (c.n, 2))
    crowd._bodies()
    once = [p.cloud.weights() for p in crowd.people]
    for _ in range(20):
        crowd._bodies()
    for person, w in zip(crowd.people, once):
        assert np.allclose(person.cloud.weights(), w, atol=1e-9)


def _spread_after(dt_total, n_steps, seed=3, stop_rate=0.0):
    rng = np.random.default_rng(seed)
    crowd = Crowd(flat_config(), start=0.0, n=4000, people=["observed"])
    m = Motion()
    m.stop_rate = stop_rate
    m.go_share = 0.0
    c = Cloud(4000, 2, rng)
    c.place[:] = OBSERVED
    c.pos[:] = (3.0, 2.5)
    c.vel[:] = (0.0, 0.0)
    c.mode[:] = WALK
    # an empty world without walls, so only the motion counts
    world = crowd.world
    world_walls, world.crosses_wall = world.crosses_wall, lambda a, b: np.zeros(len(a), dtype=bool)
    world_place, world.place_of = world.place_of, lambda p: np.zeros(len(p), dtype=np.int16)
    try:
        dt = dt_total / n_steps
        for k in range(1, n_steps + 1):
            c.predict(k * dt, dt, world, crowd.dwell, m)
    finally:
        world.crosses_wall, world.place_of = world_walls, world_place
    return c


def test_one_long_step_spreads_the_walkers_like_many_short_ones():
    # white noise in the acceleration: after T the position spreads with q^2 T^3 / 3 (Saerkkae & Solin
    # 2019), however the time is cut
    q = Motion.walk_noise
    for n_steps in (1, 20):
        c = _spread_after(2.0, n_steps)
        var = float(np.var(c.pos[:, 0]))
        assert math.isclose(var, q * q * 2.0**3 / 3, rel_tol=0.12), (n_steps, var)


def test_one_long_step_stops_the_walkers_in_time():
    # a walker stops at rate stop_rate; one who stops after s seconds walked only s seconds
    long = _spread_after(2.0, 1, stop_rate=0.5)
    short = _spread_after(2.0, 40, stop_rate=0.5)
    d_long = float(np.mean(np.hypot(*(long.pos - (3.0, 2.5)).T)))
    d_short = float(np.mean(np.hypot(*(short.pos - (3.0, 2.5)).T)))
    assert math.isclose(d_long, d_short, rel_tol=0.12), (d_long, d_short)
