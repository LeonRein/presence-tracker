"""Load the LD2410C gate energies and the LD2450 targets of the same housing from recordings
(one JSON per MQTT message per line) into numpy arrays per sensor, cached as .npz.

Per sensor: t (frame time from the sensor uptime, SensorClock), mg/sg (n, 9) move/still gate
energies, moving/still flags, md/sd distances (m), me/se energies, tg (n, 3, 4):
x, y (m, sensor frame, mirror ignored), speed (m/s), valid."""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tracker"))
from presence_tracker.frames import SensorClock  # noqa: E402

REC = "/home/leon/checkout/presence-tracker/recordings/20261006-*.jsonl"
CACHE = os.path.expanduser("~/.cache/presence-tracker/ld2410_frames.npz")


def load(pattern=REC, cache=None):
    """The frames of the recordings matching pattern (cached; the cache is named after the files and
    their sizes, so a growing recording is read again)."""
    if cache is None:
        if pattern == REC:
            cache = CACHE
        else:
            import hashlib
            key = "|".join(f"{f}:{os.path.getsize(f)}" for f in sorted(glob.glob(pattern)))
            cache = os.path.join(os.path.dirname(CACHE), "ld2410_" + hashlib.sha1(key.encode()).hexdigest()[:12] + ".npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        return z["data"].item()
    rows = {}
    clocks = {}
    for f in sorted(glob.glob(pattern)):
        for line in open(f):
            m = json.loads(line)
            if not m["topic"].endswith("/frame"):
                continue
            sid = m["topic"].split("/")[1].replace("presence-", "")
            p = m["payload"]
            ld = p.get("ld2410") or {}
            if "move_gates" not in ld:
                continue
            t = clocks.setdefault(sid, SensorClock())(m["t"], p.get("uptime_ms"))
            tg = np.zeros((3, 4))
            for i, x in enumerate(p.get("targets", [])[:3]):
                if x.get("x") or x.get("y"):
                    tg[i] = (x["x"] / 1000, x["y"] / 1000, x.get("speed", 0) / 1000, 1)
            rows.setdefault(sid, []).append((t, m["t"], ld["move_gates"], ld["still_gates"], ld.get("moving", False),
                                             ld.get("still", False), ld.get("moving_distance", 0) / 1000,
                                             ld.get("still_distance", 0) / 1000, ld.get("moving_energy", 0),
                                             ld.get("still_energy", 0), tg))
    data = {}
    for sid, r in rows.items():
        data[sid] = dict(
            t=np.array([x[0] for x in r]), recv=np.array([x[1] for x in r]),
            mg=np.array([x[2] for x in r], dtype=float), sg=np.array([x[3] for x in r], dtype=float),
            moving=np.array([x[4] for x in r], bool), still=np.array([x[5] for x in r], bool),
            md=np.array([x[6] for x in r]), sd=np.array([x[7] for x in r]),
            me=np.array([x[8] for x in r], float), se=np.array([x[9] for x in r], float),
            tg=np.array([x[10] for x in r]))
    np.savez(cache, data=np.array(data, dtype=object))
    return data


if __name__ == "__main__":
    d = load()
    for sid, x in d.items():
        dt = np.diff(x["t"])
        print(sid, len(x["t"]), f"{(x['t'][-1] - x['t'][0]) / 3600:.2f} h", "median dt", np.median(dt),
              "frames with gaps > 1 s", int((dt > 1).sum()))
