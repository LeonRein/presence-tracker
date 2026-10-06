"""Automatic sensor calibration from one person walking through overlapping fields of view.

While a session runs, the one *moving* target of each frame is stored per sensor (floor
coordinates in the sensor frame). Moving only: someone sitting still elsewhere would otherwise
pair with the walker, and a single still spot says nothing about rotation. For two sensors, the
target of one is interpolated to the frame times of the other, giving pairs of the same point
seen by both.

The drawn positions are taken as given, the drawn headings are not. Per sensor the calibration
fits the heading, a scale (the LD2450's distances may be off by some percent) and whether its x
axis is mirrored. With the position p fixed, heading and scale are one complex number c: the
floor point z in the sensor frame (as a complex number) lies at p + c z in the house. For a pair
of sensors seeing the same point, c_a z_a + p_a = c_b z_b + p_b is linear in c_a and c_b, so all
sensors are fitted together by least squares: robustly, with RANSAC per sensor pair (wrong pairs
from a second person or reflections), then jointly on the pairs that agree. A sensor without
enough pairs with any other stays as it is. Each result gets a verdict.
"""

import cmath
import itertools
import math
import random
from collections import defaultdict

import numpy as np

from .model import Config, SensorConfig

MAX_GAP = 0.25  # s, interpolate only between frames this close
INLIER = 0.35  # m
MIN_SPEED = 0.05  # m/s, radial speed for a target to count as moving
MAX_SCALE_ERR = 0.2  # a fit that needs a scale further from 1 comes from wrong pairs, not from the radar
# quality thresholds
MIN_INLIERS = 150  # matching pairs for a good result
MIN_INLIER_RATIO = 0.25  # below this, the pairs are mostly wrong (second person, echoes)
MIN_SPREAD = 0.5  # m, standard deviation of the inlier points along their narrowest direction
CAL_WALL_MARGIN = 1.0  # m, points this far behind a wall are echoes even with a heading 20 degrees off


def _heading(c: complex) -> float:
    """Heading of a sensor whose frame is turned by the angle of c (its forward axis is y)."""
    return (math.degrees(cmath.phase(c)) + 90) % 360


def _complex(g: np.ndarray) -> np.ndarray:
    return g[:, 0] + 1j * g[:, 1]


def _plausible(c: complex) -> bool:
    return abs(abs(c) - 1) <= MAX_SCALE_ERR


def ransac(za: np.ndarray, zb: np.ndarray, d: complex, iterations: int = 1000, seed: int = 0):
    """Robust fit of c_a z_a - c_b z_b = d (d = p_b - p_a) from two points per hypothesis.
    Returns the inlier mask of the best hypothesis, or None."""
    rng = random.Random(seed)
    n = len(za)
    if n < 3:
        return None
    best = None
    for _ in range(iterations):
        i, j = rng.sample(range(n), 2)
        if abs(za[i] - za[j]) < 0.5:
            continue
        det = zb[i] * za[j] - za[i] * zb[j]
        if abs(det) < 1e-6:
            continue
        ca, cb = d * (zb[i] - zb[j]) / det, d * (za[i] - za[j]) / det
        if not (_plausible(ca) and _plausible(cb)):
            continue
        inliers = np.abs(ca * za - cb * zb - d) < INLIER
        if best is None or inliers.sum() > best.sum():
            best = inliers
    if best is None or best.sum() < 3:
        return None
    return best


def joint_fit(sensors: list, rows: list, iterations: int = 3):
    """Least squares c per sensor from all pairs: rows = [(a, b, za, zb, d, mask)]. Refits on the
    points that agree with the joint result. Returns (c by sensor, masks) or None."""
    col = {s: i for i, s in enumerate(sensors)}
    masks = [r[5] for r in rows]
    c = None
    for _ in range(iterations):
        parts, rhs = [], []
        for (a, b, za, zb, d, _), m in zip(rows, masks):
            block = np.zeros((int(m.sum()), len(sensors)), complex)
            block[:, col[a]], block[:, col[b]] = za[m], -zb[m]
            parts.append(block)
            rhs.append(np.full(len(block), d))
        A = np.concatenate(parts)
        if np.linalg.matrix_rank(A) < len(sensors):
            return None
        c = np.linalg.lstsq(A, np.concatenate(rhs), rcond=None)[0]
        masks = [np.abs(c[col[a]] * za - c[col[b]] * zb - d) < INLIER for a, b, za, zb, d, _ in rows]
    return {s: complex(c[col[s]]) for s in sensors}, masks


def verdict(pairs: int, inliers: int, spread: float) -> tuple:
    """(quality: ok | warn | bad, German reason).

    Judged by the number of matching pairs and how far they spread, not by their share: echoes
    walking along with the person make many wrong pairs, but they don't agree on one solution."""
    ratio = inliers / max(pairs, 1)
    if ratio < MIN_INLIER_RATIO:
        return "bad", (f"Nur {100 * ratio:.0f} % der Messungen passen zusammen. Wahrscheinlich war noch jemand im Bereich. "
                       "Allein wiederholen.")
    if inliers < MIN_INLIERS:
        return "bad", f"Nur {inliers} passende Messungen, mindestens {MIN_INLIERS} nötig. Länger durch die Überschneidung gehen."
    if spread < MIN_SPREAD:
        return "warn", "Die Messpunkte liegen fast auf einer Linie. Kreuz und quer gehen, damit die Drehung sicher bestimmt ist."
    return "ok", ""


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
        if not self.active:
            return
        # exactly one moving target; people sitting still elsewhere and echoes far behind a wall
        # don't matter
        moving = [d for d in detections if not d.stale and abs(d.speed) >= MIN_SPEED
                  and not self.config.hidden(sensor, d.pos, d.radial, CAL_WALL_MARGIN)]
        if len(moving) == 1:
            self.series[sensor.id].append((t, *moving[0].local))

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

    def solve(self, min_pairs: int = 30) -> dict:
        """New heading, scale and mirror for every placed sensor that shares enough pairs with
        another; positions stay as drawn.

        1. Per sensor pair and mirror combination: RANSAC, which pairs agree.
        2. Per group of connected sensors and mirror assignment: joint least squares; the
           assignment with the most agreeing pairs wins.
        3. Two sensors alone fit just as well reflected across the line between them (all
           mirrors flipped); the floor plan decides (the walked points have to be inside the
           rooms), else the drawn headings.
        """
        placed = [s.id for s in self.config.sensors if s.placed and s.enabled]
        sensors = [s for s in placed if s in self.series]
        if len(sensors) < 2:
            return {"error": "Mindestens zwei platzierte Sensoren brauchen Messungen einer Person in Bewegung."}

        def drawn(sid):
            s = self.config.sensor_by_id[sid]
            return complex(s.x, s.y)

        # 1. which pairs agree, per mirror combination
        edges = {}
        for a, b in itertools.combinations(sorted(sensors), 2):
            ra, rb = self._pairs(a, b)
            if len(ra) < min_pairs:
                continue
            d = drawn(b) - drawn(a)
            fits = {}
            for ma, mb in itertools.product((False, True), repeat=2):
                za, zb = _complex(self._ground(a, ra, ma)), _complex(self._ground(b, rb, mb))
                mask = ransac(za, zb, d)
                if mask is not None:
                    fits[(ma, mb)] = (za, zb, d, mask)
            if fits:
                edges[(a, b)] = fits
        if not edges:
            return {"error": "Kein Sensorpaar hat genug gemeinsame Messungen einer Person in Bewegung."}

        # 2. per group of connected sensors: the best mirror assignment, jointly fitted
        groups = []
        for a, b in edges:
            joined = [g for g in groups if a in g or b in g]
            for g in joined:
                groups.remove(g)
            groups.append(set().union({a, b}, *joined))

        def fit(group, mirrors):
            """Joint fit of the sensors in the group that have agreeing pairs with these mirrors."""
            rows = [(a, b, *fits[(mirrors[a], mirrors[b])]) for (a, b), fits in edges.items()
                    if a in group and (mirrors[a], mirrors[b]) in fits]
            if not rows:
                return None
            result = joint_fit(sorted({s for r in rows for s in r[:2]}), rows)
            if result is None:
                return None
            c, masks = result
            return sum(int(m.sum()) for m in masks), c, rows, masks

        def plan_key(mirrors, f):
            # the floor plan first, then the drawn headings, which are roughly right
            world = {s: (f[1][s], mirrors[s]) for s in f[1]}
            turn = sum(abs(self._turn(s, f[1][s])) for s in f[1])
            return round(self._inside_share(world), 2), -turn

        solved, stats = {}, {}
        for group in groups:
            group = sorted(group)
            best = None
            for flags in itertools.product((False, True), repeat=len(group) - 1):
                mirrors = dict(zip(group, (False, *flags)))
                f = fit(group, mirrors)
                if f is not None and (best is None or f[0] > best[1][0]):
                    best = (mirrors, f)
            if best is None:
                continue
            # 3. the reflected solution (all mirrors flipped), if it fits about as well
            flipped = {s: not m for s, m in best[0].items()}
            options = [best] + [(flipped, f) for f in [fit(group, flipped)] if f is not None and f[0] >= 0.9 * best[1][0]]
            mirrors, (_, c, rows, masks) = max(options, key=lambda o: plan_key(*o))
            for sid in c:
                solved[sid] = (c[sid], mirrors[sid])
                stats[sid] = self._stats(sid, c, rows, masks, drawn)

        out = {}
        for sid, (c, mirror) in solved.items():
            st = stats[sid]
            quality, reason = verdict(st["pairs"], st["inliers"], st["spread"])
            if not _plausible(c):
                quality, reason = "bad", (f"Maßstab {abs(c):.2f} ist kein plausibler Messfehler. "
                                          "Stimmen die eingezeichneten Positionen?")
            out[sid] = {
                "heading": round(_heading(c), 1), "mirror": bool(mirror), "scale": round(abs(c), 3),
                "turn": round(self._turn(sid, c), 1), "quality": quality, "reason": reason,
                **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in st.items()},
            }
        return {"sensors": out, "unsolved": [s for s in placed if s not in solved],
                "inside": round(self._inside_share(solved), 3)}

    def _stats(self, sid: str, c: dict, rows: list, masks: list, drawn) -> dict:
        """Pairs, agreeing pairs, their rms error and spread for one sensor."""
        pairs = inliers = 0
        err, points = [], []
        for (a, b, za, zb, d, _), m in zip(rows, masks):
            if sid not in (a, b):
                continue
            pairs += len(za)
            inliers += int(m.sum())
            err.append(np.abs(c[a] * za[m] - c[b] * zb[m] - d))
            points.append(c[a] * za[m] + drawn(a))
        err, points = np.concatenate(err), np.concatenate(points)
        xy = np.stack([points.real, points.imag])
        spread = float(np.sqrt(max(np.linalg.eigvalsh(np.cov(xy))[0], 0.0))) if len(points) > 2 else 0.0
        return {"pairs": pairs, "inliers": inliers, "rms": float(np.sqrt((err**2).mean())) if len(err) else 0.0,
                "spread": spread}

    def _turn(self, sid: str, c: complex) -> float:
        return (_heading(c) - self.config.sensor_by_id[sid].heading + 180) % 360 - 180

    def _inside_share(self, world: dict) -> float:
        """Share of the walked points inside the rooms, with these fits {sensor: (c, mirror)}.
        0.5 without rooms."""
        rooms = self.config.zones_of("room")
        if not rooms:
            return 0.5
        inside = total = 0
        for sid, (c, mirror) in world.items():
            s = self.config.sensor_by_id[sid]
            raw = np.array([pt[1:] for pt in self.series[sid]])
            if not len(raw):
                continue
            for z in c * _complex(self._ground(sid, raw[:: max(1, len(raw) // 300)], mirror)) + complex(s.x, s.y):
                total += 1
                inside += any(room.contains(z.real, z.imag, 0.3) for room in rooms)
        return inside / total if total else 0.5
