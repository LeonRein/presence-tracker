"""Sensor calibration (MODEL.md 10, "Kalibrierung"): heading and scale of every LD2450 from people
walking, for all sensors at once. The estimator is the core's (presence_core.calibrate: pairs, handovers,
the floor plan and the scales of the LD2450s in one posterior; what is proposed and why not); so are the
rows of every frame (presence_core.Walking: the walking targets through the boards' own tracks) and the
progress of the data (presence_core.calibration_status).

Here the app's session: the rows of the last KEEP seconds, collected all the time (in raw sensor
coordinates), stored for the next start with the key of the geometry they were collected under (the core's:
presence_core.geometry_key; a saved configuration of another one discards them, app.App._forget, as a file
of another one at the start: load), handed to the core in a thread; the web UI words the core's reasons. A
sensor turned or moved for real while its drawing stays needs `reset`. Everyday walking connects the sensors
as well as a calibration walk does, and over a day far better (MODEL.md 10: a 10-minute walk alone put the hall's heading 15 deg off); a walk alone through
rooms and doors adds what everyday walking doesn't reach. `tools/calibrate_offline.py` (its analyses need
a tree of 0.43.0, where the estimator was Python) feeds recordings through the same collection.
"""

import collections
import io
import json
import math
import time

import numpy as np

import presence_core
from presence_core import Frame

from .model import Config
from .util import atomic_write

KEEP = 24 * 3600.0  # s of walking measurements the app keeps (about 5 MB a day for six sensors)
T, SEG = 0, 1  # columns of the stored rows: t, track, raw x, raw y (m), raw radial speed (m/s), single (1/0)
COLUMNS = 6

def _bytes(data: dict) -> dict:
    """The rows per sensor as the core takes them: the bytes of their float64 rows."""
    return {sid: np.ascontiguousarray(a, dtype="<f8").reshape(-1, COLUMNS).tobytes() for sid, a in data.items()}


def solve(config: Config, data: dict, check_mirror: bool = False) -> dict:
    """New heading and scale for every placed sensor with walking measurements, each with its uncertainty;
    only what is determined is proposed (the core's: presence_core.calibrate). data: {sensor id: rows};
    check_mirror: also fit each sensor mirrored. The reasons why something is not proposed as the core gives
    them ("reasons": kind and numbers; the web UI words them), as is {"error": "no_walking"}."""
    out = json.loads(presence_core.calibrate(config.model_json(), _bytes(data), check_mirror))
    if "sensors" in out:
        out["sensors"] = {s.pop("id"): s for s in out["sensors"]}
    return out


class Calibrator:
    """The app's calibration data: the walking rows of every LD2450 frame of the last KEEP seconds."""

    CHUNK = 5000  # rows per sensor gathered before they are packed into an array

    def __init__(self, config: Config):
        self.config = config
        self.reset()

    def reset(self):
        self._rows: dict[str, list] = collections.defaultdict(list)
        self._chunks: dict[str, list] = collections.defaultdict(list)
        self._walking = presence_core.Walking(0)  # the boards' own tracks, track numbers from 0
        self._status, self._status_t = None, -math.inf
        self._gen = getattr(self, "_gen", 0) + 1  # a count started before a reset is not taken over
        self.since = None

    def on_frame(self, sensor_id: str, t: float, frame: Frame, learn: bool = True):
        """A checked LD2450 frame (presence_core.Frame) at the sensor's frame time. learn False (learning
        paused, pause.py): the sensor's tracks go on, its measurements are not kept."""
        rows = self._walking.frame(sensor_id, t, frame, not learn)
        if not learn:
            return
        if self.since is None:
            self.since = t
        mine = self._rows[sensor_id]
        mine.extend(rows)
        if len(mine) >= self.CHUNK:
            chunks = self._chunks[sensor_id]
            chunks.append(np.array(mine, float))
            mine.clear()
            while chunks and chunks[0][-1, T] < t - KEEP:
                chunks.pop(0)
            self.since = max(self.since, t - KEEP)

    def geometry(self) -> str:
        """The key of the geometry of the configuration (the core's): what the data are collected under."""
        return presence_core.geometry_key(self.config.model_json())

    def save(self, path):
        """The collected data, for the next start (a restart would otherwise lose up to a day)."""
        atomic_write(path, self.snapshot()())

    def snapshot(self):
        """The collected data as they are now (copied), with the key of their geometry, and a function that packs
        them for the file: it may run in a thread while the frames go on. Not compressed: np.savez_compressed
        took 98 ms for a day of data (1.6 MB instead of 6.7 MB), np.savez 2 ms (MODEL.md 10)."""
        parts = self._parts()
        since = np.array([self.since if self.since is not None else np.nan])
        geometry = np.array(self.geometry())

        def pack() -> bytes:
            buf = io.BytesIO()
            np.savez(buf, since=since, geometry=geometry, **{"s_" + sid: a for sid, a in self._join(parts).items()})
            return buf.getvalue()
        return pack

    def load(self, path) -> bool:
        """What `save` wrote, if it was collected under the geometry of the configuration; else nothing (False:
        the caller discards the file). Track numbers go on after the loaded ones."""
        self.reset()
        with np.load(path) as f:
            if str(f["geometry"]) != self.geometry():
                return False
            since = float(f["since"][0])
            self.since = None if math.isnan(since) else since
            last = -1
            for key in f.files:
                if key.startswith("s_") and len(f[key]):
                    self._chunks[key[2:]].append(np.array(f[key], float))
                    last = max(last, int(f[key][:, SEG].max()))
        self._walking = presence_core.Walking(last + 1)
        return True

    def _parts(self) -> dict:
        """Per sensor the packed arrays (never changed afterwards) and a copy of the rows gathered
        since: quick, in the loop; _join may then run in a thread while on_frame goes on."""
        return {sid: (list(self._chunks.get(sid, [])), list(self._rows.get(sid, [])))
                for sid in sorted(set(self._rows) | set(self._chunks))}

    @staticmethod
    def _join(parts: dict) -> dict:
        out = {}
        for sid, (chunks, rows) in parts.items():
            a = np.concatenate(chunks + [np.array(rows, float).reshape(-1, COLUMNS)])
            if len(a):
                out[sid] = a
        return out

    def data(self) -> dict:
        return self._join(self._parts())

    def count(self) -> int:
        """The walking measurements kept, of all sensors."""
        return sum(sum(len(c) for c in chunks) + len(rows) for chunks, rows in self._parts().values())

    def status(self) -> dict:
        """Since when and how many hours are kept ("keep_h"), walking measurements per sensor ("frames"), the
        walking points of each as solve counts them ("points") and its hours with walking data ("hours", a
        proposal needs "min_hours"), and seconds in which two sensors each had exactly one moving target
        (recounted every 30 s, here in the caller's thread: the app counts in a thread, status_job)."""
        if self.status_due():
            self.use_status(self.status_job()())
        return self._status

    def status_due(self) -> bool:
        return self._status is None or time.monotonic() - self._status_t > 30

    def last_status(self) -> dict | None:
        """The last count (None: none yet)."""
        return self._status

    def status_job(self):
        """A copy of the data as they are now (in the loop), and a function the core counts them with that
        may run in a thread: with a day of data about 0.25 s on Home Assistant with the Python count of
        0.43.0, every 30 s while a browser is open (MODEL.md 10). Its result goes to use_status, in the
        loop."""
        parts, since, gen, t = self._parts(), self.since, self._gen, time.monotonic()
        config = self.config.model_json()

        def count():
            status = json.loads(presence_core.calibration_status(config, _bytes(self._join(parts))))
            return gen, t, {"since": since, "keep_h": KEEP / 3600, **status}
        return count

    def use_status(self, counted: tuple):
        """Take over what a status_job counted, unless the data were reset meanwhile."""
        gen, t, status = counted
        if gen == self._gen:
            self._status, self._status_t = status, t

    def solve(self, check_mirror: bool = False) -> dict:
        return solve(self.config, self.data(), check_mirror)
