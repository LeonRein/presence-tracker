"""Mean number of hypotheses and distinct objects over a replay (for CPU comparisons).

usage: hypstats.py TRACKER_DIR RECORDING CONFIG [seconds]"""
import collections
import json
import sys

sys.path.insert(0, sys.argv[1])
from presence_tracker.filter import Tracker  # noqa: E402
from presence_tracker.frames import SensorClock  # noqa: E402
from presence_tracker.hidden import Hidden  # noqa: E402
from presence_tracker.model import Config  # noqa: E402

limit = float(sys.argv[4]) if len(sys.argv) > 4 else 1e9
tr = Tracker(Config.from_dict(json.load(open(sys.argv[3]))))
clocks = collections.defaultdict(SensorClock)
t0 = None
nxt = 0.0
H, O, HID = [], [], []
for line in open(sys.argv[2]):
    m = json.loads(line)
    if not m["topic"].endswith("/frame"):
        continue
    sid = m["topic"].split("/")[1]
    tt = clocks[sid](m["t"], m["payload"].get("uptime_ms"))
    t0 = t0 or tt
    if tt - t0 > limit:
        break
    tr.process_frame(sid, tt, m["payload"])
    if tt >= nxt:
        tr.step(tt)
        nxt = tt + 1.0
        objs = tr._objects(phantoms=True)
        H.append(len(tr.hyps))
        O.append(len(objs))
        HID.append(sum(isinstance(o, Hidden) for o, _ in objs))
print(f"hypotheses {sum(H) / len(H):.2f}, objects {sum(O) / len(O):.2f}, of them on tiles {sum(HID) / len(HID):.2f}")
