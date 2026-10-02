"""Automatic sensor calibration from one person walking through overlapping fields of view.

While a session runs, every frame with exactly one target is stored per sensor (floor
coordinates in the sensor frame). For two sensors, the target of one is interpolated to the
frame times of the other, giving pairs of the same point seen by both. One sensor is the anchor
(its pose stays as placed); the others get position, heading and the x direction (mirror) from a
2D rigid fit (Kabsch) with RANSAC against the already solved sensors, then a few rounds of
refinement with all pairs.
"""

import math
import random
from collections import defaultdict

import numpy as np

from .model import Config, SensorConfig

MAX_GAP = 0.25  # s, interpolate only between frames this close
INLIER = 0.35  # m


def _rot(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def _pose_matrix(s: SensorConfig):
    """world = p + R g, with g the floor point in the sensor frame (x right, y forward)."""
    return np.array([s.x, s.y]), _rot(math.radians(s.heading) - math.pi / 2)


def kabsch(src: np.ndarray, dst: np.ndarray):
    """Rotation angle and translation with dst ~ R src + t (least squares)."""
    cs, cd = src.mean(0), dst.mean(0)
    a, b = src - cs, dst - cd
    angle = math.atan2((a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum(), (a * b).sum())
    t = cd - _rot(angle) @ cs
    return angle, t


def ransac(src: np.ndarray, dst: np.ndarray, iterations: int = 200, seed: int = 0):
    rng = random.Random(seed)
    n = len(src)
    best = None
    for _ in range(iterations):
        i, j = rng.sample(range(n), 2)
        if np.linalg.norm(src[i] - src[j]) < 0.5:
            continue
        angle, t = kabsch(src[[i, j]], dst[[i, j]])
        err = np.linalg.norm(src @ _rot(angle).T + t - dst, axis=1)
        inliers = err < INLIER
        if best is None or inliers.sum() > best.sum():
            best = inliers
    if best is None or best.sum() < 3:
        return None
    angle, t = kabsch(src[best], dst[best])
    err = np.linalg.norm(src @ _rot(angle).T + t - dst, axis=1)
    inliers = err < INLIER
    return angle, t, inliers, float(np.sqrt((err[inliers] ** 2).mean()))


class Calibrator:
    def __init__(self, config: Config):
        self.config = config
        self.active = False
        self.series: dict[str, list] = defaultdict(list)  # sensor -> [(t, lx, ly)] raw LD2450 meters

    def start(self):
        self.series.clear()
        self.active = True

    def stop(self):
        self.active = False

    def on_frame(self, sensor: SensorConfig, t: float, detections: list):
        if self.active and len(detections) == 1 and not detections[0].ignored:
            self.series[sensor.id].append((t, *detections[0].local))

    def status(self) -> dict:
        ids = sorted(self.series)
        pairs = {}
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                n = len(self._pairs(a, b)[0])
                if n:
                    pairs[f"{a}|{b}"] = n
        return {"active": self.active, "frames": {k: len(v) for k, v in self.series.items()}, "pairs": pairs}

    def _ground(self, sensor_id: str, raw: np.ndarray, mirror: bool | None = None) -> np.ndarray:
        s = self.config.sensor_by_id[sensor_id]
        flip = s.mirror if mirror is None else mirror
        x = -raw[:, 0] if flip else raw[:, 0]
        y = raw[:, 1]
        slant = np.hypot(x, y)
        dh = s.height - self.config.params.target_height
        ground = np.sqrt(np.maximum(slant**2 - dh**2, 0.01))
        k = ground / np.maximum(slant, 1e-6)
        return np.stack([x * k, y * k], axis=1)

    def _pairs(self, a: str, b: str):
        """Raw points (a, b) of the same target: a interpolated to b's frame times."""
        sa, sb = self.series.get(a, []), self.series.get(b, [])
        if len(sa) < 2 or not sb:
            return np.empty((0, 2)), np.empty((0, 2))
        ta = np.array([p[0] for p in sa])
        pa = np.array([p[1:] for p in sa])
        out_a, out_b = [], []
        for t, lx, ly in sb:
            k = np.searchsorted(ta, t)
            if k == 0 or k >= len(ta):
                continue
            t0, t1 = ta[k - 1], ta[k]
            if t1 - t0 > MAX_GAP:
                continue
            w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            out_a.append(pa[k - 1] * (1 - w) + pa[k] * w)
            out_b.append((lx, ly))
        return np.array(out_a).reshape(-1, 2), np.array(out_b).reshape(-1, 2)

    def solve(self, anchor: str, min_pairs: int = 30) -> dict:
        """Proposed poses for all sensors that overlap (directly or via others) with the anchor."""
        sensors = [s.id for s in self.config.sensors if s.id in self.series]
        if anchor not in sensors:
            return {"error": "Der Anker-Sensor hat keine Daten."}
        pose = {anchor: _pose_matrix(self.config.sensor_by_id[anchor]) + (self.config.sensor_by_id[anchor].mirror,)}
        pair_cache = {}

        def pairs(a, b):
            key = (a, b)
            if key not in pair_cache:
                pair_cache[key] = self._pairs(a, b)
            return pair_cache[key]

        def fit(sid, solved):
            """Fit sensor sid against the solved sensors; tries both x directions."""
            best = None
            for mirror in (False, True):
                src, dst = [], []
                for other in solved:
                    ra, rb = pairs(other, sid)
                    if len(ra) == 0:
                        continue
                    p, R, other_mirror = pose[other]
                    world = self._ground(other, ra, other_mirror) @ R.T + p
                    src.append(self._ground(sid, rb, mirror))
                    dst.append(world)
                if not src:
                    return None
                src, dst = np.concatenate(src), np.concatenate(dst)
                if len(src) < min_pairs:
                    return None
                result = ransac(src, dst)
                if result is None:
                    continue
                angle, t, inliers, rms = result
                score = (inliers.sum(), -rms)
                if best is None or score > best[0]:
                    best = (score, angle, t, mirror, int(inliers.sum()), len(src), rms)
            return best

        pending = [s for s in sensors if s != anchor]
        results = {}
        while pending:
            fits = [(sid, fit(sid, list(pose))) for sid in pending]
            fits = [(sid, f) for sid, f in fits if f is not None]
            if not fits:
                break
            sid, f = max(fits, key=lambda item: item[1][0])
            _, angle, t, mirror, n_in, n, rms = f
            pose[sid] = (t, _rot(angle), mirror)
            results[sid] = {"inliers": n_in, "pairs": n, "rms": rms}
            pending.remove(sid)

        # refinement: re-fit every non-anchor sensor against all others
        for _ in range(3):
            for sid in list(results):
                f = fit(sid, [o for o in pose if o != sid])
                if f is None:
                    continue
                _, angle, t, mirror, n_in, n, rms = f
                pose[sid] = (t, _rot(angle), mirror)
                results[sid] = {"inliers": n_in, "pairs": n, "rms": rms}

        out = {}
        for sid, stats in results.items():
            p, R, mirror = pose[sid]
            heading = (math.degrees(math.atan2(R[1, 0], R[0, 0])) + 90) % 360
            current = self.config.sensor_by_id[sid]
            out[sid] = {
                "x": round(float(p[0]), 3), "y": round(float(p[1]), 3), "heading": round(heading, 1),
                "mirror": bool(mirror),
                "shift": round(math.hypot(p[0] - current.x, p[1] - current.y), 3),
                "turn": round((heading - current.heading + 180) % 360 - 180, 1),
                **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in stats.items()},
            }
        missing = [s for s in pending]
        return {"anchor": anchor, "sensors": out, "unsolved": missing}
