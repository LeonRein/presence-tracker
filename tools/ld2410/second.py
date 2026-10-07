"""Does the LD2410C want a second person where one tracked person is alone? Replays from START (a fresh
tracker, as an app start) and, every time sensor SENSOR's energies are weighed within the windows (times
in which the person with an LD2450 track is known to be alone in ROOM), compares the energies given the
background and what the people with a track put in (the strongest hypothesis) with the same plus a standing
person somewhere in ROOM (the tiles in the LD2410C's view, by area). The energies are shared between people
by superposition (MODEL.md 4.3): the extra person gets only what the tracked one does not explain.

Printed: per cell the mean energy seen and the model's mean (background + the tracked people); the log
Bayes factor of a second person that stays on one tile for all windows (uniform over the room, and the
best tile), the part of the censored readings (100) alone, and the same given the tracked person's gain
on the profile fitted over the windows (maximum likelihood; the amplitude per stay, 4.3). Positive: the
energies want somebody else in the room. MODEL.md 10 ("Energie aufteilen").

usage: second.py --config FILE --recordings DIR [--day YYYY-MM-DD] START SENSOR ROOM HH:MM:SS-HH:MM:SS..."""
import argparse
import collections
import copy
import json
import math
import os
import sys
import time

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))


def lme(x, w):
    """log sum_i w_i exp(x_i)"""
    top = float(x.max())
    return top + math.log(float(w @ np.exp(x - top)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--recordings", required=True)
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "..", "tracker"))
    ap.add_argument("--day", default="2026-10-07")
    ap.add_argument("start")
    ap.add_argument("sensor")
    ap.add_argument("room")
    ap.add_argument("windows", nargs="+")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker import ld2410
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.model import Config

    def at(v, ref=None):
        t = time.mktime(time.strptime(f"{a.day} {v}", "%Y-%m-%d %H:%M:%S"))
        return t + 86400 if ref is not None and t < ref else t  # after midnight

    t_start = at(a.start)
    windows = [tuple(at(v, t_start) for v in w.split("-")) for w in a.windows]
    t_end = max(b for _, b in windows)
    config = Config.from_dict(json.load(open(a.config)))
    tr = Tracker(config)
    si_want = next(k for k, s in enumerate(tr.sensors) if a.sensor in s)
    room_k = tr.rooms.index(a.room)
    blocks = []
    orig = Tracker._ld_weigh

    def wrapped(self, si, st):
        if si == si_want and any(t0 <= self.now <= t1 for t0, t1 in windows):
            m = self.m
            lik = ld2410.Likelihood(m)
            b = self.ld_background.b(self.sensors[si])
            M = self._ld_memory.get(si)
            decay = math.exp(-st.time / m.ld_memory)
            wm = m.ld_memory / st.time * (1 - decay) if M is not None else 0.0
            now = ld2410.cell_values(m, 1.0, 1.0 - wm)
            b0 = b + wm * M * (now < 1) if M is not None else b
            hy = self.hyps[int(np.argmax(self.hyp_weights()))]
            tracked = np.zeros(ld2410.CELLS)
            for o in hy.groups.values():
                mm, S = self._ld_points(si, o)
                tracked += mm @ (S * now)
            idx, ss, _ = self._ld_tiles(si)
            sel = self.tiles.room[idx] == room_k
            cens = copy.copy(st)
            cens.t_unc, cens.t_e, cens.t_le = np.zeros(ld2410.CELLS), np.zeros(ld2410.CELLS), np.zeros(ld2410.CELLS)
            blocks.append((copy.deepcopy(st), cens, b0.copy(), tracked, ss[sel] * now, self.tiles.area[idx][sel], lik))
        orig(self, si, st)

    Tracker._ld_weigh = wrapped
    clocks = collections.defaultdict(SensorClock)
    nstep = 0.0
    h = t_start - t_start % 3600
    done = False
    while h <= t_end and not done:
        path = os.path.join(a.recordings, time.strftime("%Y%m%d-%H", time.localtime(h)) + ".jsonl")
        h += 3600
        if not os.path.exists(path):
            continue
        for line in open(path):
            msg = json.loads(line)
            if not msg["topic"].endswith("/frame") or msg["t"] < t_start:
                continue
            if msg["t"] > t_end:
                done = True
                break
            sid = msg["topic"].split("/")[1]
            tt = clocks[sid](msg["t"], msg["payload"].get("uptime_ms"))
            tr.process_frame(sid, tt, msg["payload"])
            if tt >= nstep:
                tr.step(tt)
                nstep = tt + 0.2
    if not blocks:
        print("no weighings in the windows")
        return
    w = blocks[0][5] / blocks[0][5].sum()
    seconds = sum(st.time for st, *_ in blocks)

    def second(gain):
        tot = tot_c = 0.0
        for st, cens, b0, tracked, S, _, lik in blocks:
            base = b0 + gain * tracked
            tot = tot + st.log_ratio(base[None, :] + S, base, lik)
            tot_c = tot_c + cens.log_ratio(base[None, :] + S, base, lik)
        return tot, tot_c

    grid = np.exp(np.linspace(math.log(0.3), math.log(5.0), 41))
    ll = np.array([sum(st.loglik(b0 + g * tracked, lik) for st, _, b0, tracked, _, _, lik in blocks) for g in grid])
    k = int(np.argmax(ll))
    one = int(np.argmin(abs(grid - 1)))
    print(f"{a.sensor} / {a.room}: {len(blocks)} weighings, {seconds:.0f} s")
    T = sum(st.t_unc + st.t_cens for st, *_ in blocks)
    seen = sum(st.t_e + 100.0 * st.t_cens for st, *_ in blocks) / np.maximum(T, 1e-9)
    model = sum(st.time * (b0 + tracked) for st, _, b0, tracked, *_ in blocks) / seconds
    print("  cells         moving 0-8, still 2-8")
    print("  energy seen   " + " ".join(f"{v:4.0f}" for v in seen) + "   (100 counted as 100)")
    print("  model, gain 1 " + " ".join(f"{v:4.0f}" for v in model) + "   (background + the tracked people)")
    for name, g in (("profile as modelled (gain 1)", 1.0), (f"tracked person's gain {grid[k]:.2f} (fitted, log lik {ll[k] - ll[one]:+.1f})", grid[k])):
        tot, tot_c = second(g)
        print(f"  {name}: second person log BF uniform over the room {lme(tot, w):+.1f}, best tile {tot.max():+.1f}; "
              f"censored readings alone: uniform {lme(tot_c, w):+.1f}, best tile {tot_c.max():+.1f}")


if __name__ == "__main__":
    main()
