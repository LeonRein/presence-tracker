"""Look into the LD2410C part of the filter: replay from START (a fresh tracker, as an app start) to
END and print, every N-th time sensor S's energies are weighed between FROM and END, the mean energies
per cell, the learned background, what the people of the strongest hypothesis put in (each with its
mass in ROOM), and P(somebody in ROOM).

usage: diag.py --config FILE --recordings DIR START FROM END SENSOR ROOM [--every N]"""
import argparse
import collections
import glob
import json
import os
import sys
import time

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--recordings", required=True)
    ap.add_argument("--tracker", default=os.path.join(TOOLS, "..", "..", "tracker"))
    ap.add_argument("--day", default="2026-10-06")
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("start")
    ap.add_argument("t_from")
    ap.add_argument("end")
    ap.add_argument("sensor")
    ap.add_argument("room")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.tracker))
    from presence_tracker import ld2410
    from presence_tracker.filter import Tracker
    from presence_tracker.frames import SensorClock
    from presence_tracker.hidden import Hidden, Undetected
    from presence_tracker.model import Config

    config = Config.from_dict(json.load(open(a.config)))
    t_start, t_from, t_end = (time.mktime(time.strptime(f"{a.day} {v}", "%Y-%m-%d %H:%M:%S")) for v in (a.start, a.t_from, a.end))
    t_from += 86400 if t_from < t_start else 0  # after midnight
    t_end += 86400 if t_end < t_start else 0
    tr = Tracker(config)
    si_want = tr.sensors.index(next(s for s in tr.sensors if a.sensor in s))
    room_k = tr.rooms.index(a.room)
    in_room = tr.tiles.room == room_k
    orig = Tracker._ld_weigh
    count = [0]

    def mass_in_room(o):
        if isinstance(o, Hidden):
            return float(o.in_view()[in_room].sum())
        return float(tr._in_view(o)[in_room].sum())

    def wrapped(self, si, st):
        if si == si_want and t_from <= self.now <= t_end:
            count[0] += 1
            if count[0] % a.every == 0:
                b = self.ld_background.b(self.sensors[si])
                e = np.where(st.t_unc > 0, st.t_e / np.maximum(st.t_unc, 1e-9), 100.0)
                hw = self.hyp_weights()
                idx_seen = set(self._ld_tiles(si)[0].tolist())
                for k, h in enumerate(self.hyps):
                    print(f"   hypothesis {k}: weight {hw[k]:.3f}, mass in {a.room} per person: "
                          + " ".join(f"{type(o).__name__[:6]} {mass_in_room(o):.2f}" for o in h.objects()))
                    for o in h.objects():
                        if isinstance(o, Hidden) and mass_in_room(o) > 0.3:
                            mm = o.in_view() * in_room
                            top = np.argsort(-mm)[:3]
                            print("      " + "; ".join(f"tile at {np.round(self.tiles.centers[c], 1)} {mm[c]:.2f}"
                                                      f"{'' if c in idx_seen else ' (LD2410C: unseen)'}" for c in top))
                hy = self.hyps[0]
                print(time.strftime("%H:%M:%S", time.localtime(self.now)),
                      f"P({a.room}) {1 - self.count_distribution()[a.room][0]:.2f}  hyps {len(self.hyps)}")
                print("   e   ", " ".join(f"{v:4.0f}" for v in e))
                print("   b   ", " ".join(f"{v:4.0f}" for v in b))
                for o in list(hy.groups.values()) + hy.hidden + [hy.ppp]:
                    m, S = self._ld_points(si, o)
                    kind = "unknown" if isinstance(o, Undetected) else ("hidden" if isinstance(o, Hidden) else "gauss")
                    if len(m):
                        print(f"   {kind[:6]:6s}", " ".join(f"{v:4.0f}" for v in m @ S),
                              f" mass seen {m.sum():.2f} in {a.room} {mass_in_room(o):.2f}")
                    if isinstance(o, Hidden) and mass_in_room(o) > 0.3:
                        idx = self._ld_tiles(si)[0]
                        mm = o.in_view()
                        for c in np.argsort(-mm)[:4]:
                            k = np.nonzero(idx == c)[0]
                            print(f"        tile {c} at {np.round(self.tiles.centers[c], 2)} mass {mm[c]:.2f}"
                                  f" seen by the LD2410C: {'yes' if len(k) else 'no'}")
        orig(self, si, st)

    Tracker._ld_weigh = wrapped
    clocks = collections.defaultdict(SensorClock)
    nstep = 0.0
    for path in sorted(glob.glob(os.path.join(a.recordings, "*.jsonl"))):
        for line in open(path):
            msg = json.loads(line)
            if not msg["topic"].endswith("/frame") or msg["t"] < t_start:
                continue
            if msg["t"] > t_end:
                return
            sid = msg["topic"].split("/")[1]
            tt = clocks[sid](msg["t"], msg["payload"].get("uptime_ms"))
            tr.process_frame(sid, tt, msg["payload"])
            if tt >= nstep:
                tr.step(tt)
                nstep = tt + 0.2


if __name__ == "__main__":
    main()
