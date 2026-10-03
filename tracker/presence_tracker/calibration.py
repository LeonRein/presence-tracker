"""Automatic sensor calibration from one person walking through overlapping fields of view.

While a session runs, the one *moving* target of each frame is stored per sensor (floor
coordinates in the sensor frame). Moving only: someone sitting still elsewhere would otherwise
pair with the walker, and a single still spot says nothing about rotation. For two sensors, the
target of one is interpolated to the frame times of the other, giving pairs of the same point
seen by both.

Positions are easy to draw on the map, headings are not. So no sensor is taken as given: the
sensors' poses relative to each other come from the pairs alone (2D rigid fits with RANSAC,
Kabsch), and this rigid constellation is then placed onto the drawn positions. Only hypotheses
whose sensor distances roughly match the drawing are considered (wrong pairs from a second
person or reflections otherwise produce a consensus meters away). Each result gets a verdict.
"""

import math
import random
from collections import defaultdict

import numpy as np

from .model import Config, SensorConfig

MAX_GAP = 0.25  # s, interpolate only between frames this close
INLIER = 0.35  # m
MIN_SPEED = 0.05  # m/s, radial speed for a target to count as moving
MAX_DIST_ERR = 1.0  # m, measured sensor distance vs. drawn distance, for a hypothesis to be considered
MAX_POS_RESIDUAL = 0.5  # m, a sensor this far from its drawn position after placing gets a warning
# quality thresholds
MIN_INLIERS = 150  # matching pairs for a good result
MIN_INLIER_RATIO = 0.25  # below this, the pairs are mostly wrong (second person, echoes)
MIN_SPREAD = 0.5  # m, standard deviation of the inlier points along their narrowest direction
CAL_WALL_MARGIN = 1.0  # m, points this far behind a wall are echoes even with a heading 20 degrees off


def _rot(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def _pose_matrix(s: SensorConfig):
    """world = p + R g, with g the floor point in the sensor frame (x right, y forward)."""
    return np.array([s.x, s.y]), _rot(math.radians(s.heading) - math.pi / 2)


def _heading(angle: float) -> float:
    return (math.degrees(angle) + 90) % 360


def kabsch(src: np.ndarray, dst: np.ndarray):
    """Rotation angle and translation with dst ~ R src + t (least squares)."""
    cs, cd = src.mean(0), dst.mean(0)
    a, b = src - cs, dst - cd
    angle = math.atan2((a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum(), (a * b).sum())
    t = cd - _rot(angle) @ cs
    return angle, t


def ransac(src: np.ndarray, dst: np.ndarray, valid=None, iterations: int = 1000, seed: int = 0):
    """Robust rigid fit. valid(angle, t) rejects hypotheses (e.g. far from the drawn pose)."""
    rng = random.Random(seed)
    n = len(src)
    if n < 3:
        return None
    best = None
    for _ in range(iterations):
        i, j = rng.sample(range(n), 2)
        if np.linalg.norm(src[i] - src[j]) < 0.5:
            continue
        angle, t = kabsch(src[[i, j]], dst[[i, j]])
        if valid and not valid(angle, t):
            continue
        inliers = np.linalg.norm(src @ _rot(angle).T + t - dst, axis=1) < INLIER
        if best is None or inliers.sum() > best.sum():
            best = inliers
    if best is None or best.sum() < 3:
        return None
    for _ in range(3):  # refit on the inliers, which may change them
        angle, t = kabsch(src[best], dst[best])
        err = np.linalg.norm(src @ _rot(angle).T + t - dst, axis=1)
        best = err < INLIER
        if best.sum() < 3:
            return None
    spread = float(np.sqrt(max(np.linalg.eigvalsh(np.cov(dst[best].T))[0], 0.0)))
    return angle, t, best, float(np.sqrt((err[best] ** 2).mean())), spread


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
        moving = [d for d in detections if not d.ignored and not d.stale and abs(d.speed) >= MIN_SPEED
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
        """New poses for all sensors that overlap (directly or via others).

        1. Constellation: the sensors relative to each other, purely from the pairs. The most
           connected sensor is the root of an arbitrary frame; nobody's drawn heading is trusted.
        2. Placement: the rigid constellation is rotated and moved onto the drawn positions
           (least squares). The headings follow from that.
        3. A mirrored constellation fits the positions just as well; the floor plan decides:
           the walked points have to be inside the rooms.
        """
        sensors = [s.id for s in self.config.sensors if s.id in self.series and s.placed and s.enabled]
        if len(sensors) < 2:
            return {"error": "Mindestens zwei platzierte Sensoren brauchen Messungen einer Person in Bewegung."}
        pair_cache = {}

        def pairs(x, y):
            if (x, y) not in pair_cache:
                pair_cache[(x, y)] = self._pairs(x, y)
            return pair_cache[(x, y)]

        def drawn(sid):
            s = self.config.sensor_by_id[sid]
            return np.array([s.x, s.y])

        def constellation(root, root_mirror):
            pose = {root: (np.zeros(2), np.eye(2), root_mirror)}  # position, rotation, mirror
            stats = {}

            def plausible(sid, solved):
                # the measured distance to every solved sensor must roughly match the drawing
                want = {o: np.linalg.norm(drawn(sid) - drawn(o)) for o in solved}

                def valid(angle, t):
                    return all(abs(np.linalg.norm(t - pose[o][0]) - want[o]) <= MAX_DIST_ERR for o in solved)
                return valid

            def fit(sid, solved):
                best = None
                for mirror in (False, True):
                    src, dst = [], []
                    for other in solved:
                        ra, rb = pairs(other, sid)
                        if len(ra) == 0:
                            continue
                        p, R, other_mirror = pose[other]
                        dst.append(self._ground(other, ra, other_mirror) @ R.T + p)
                        src.append(self._ground(sid, rb, mirror))
                    if not src:
                        return None
                    src, dst = np.concatenate(src), np.concatenate(dst)
                    if len(src) < min_pairs:
                        return None
                    result = ransac(src, dst, valid=plausible(sid, solved))
                    if result is None:
                        continue
                    angle, t, inliers, rms, spread = result
                    score = (inliers.sum(), -rms)
                    if best is None or score > best[0]:
                        best = (score, angle, t, mirror, {"inliers": int(inliers.sum()), "pairs": len(src),
                                                          "rms": rms, "spread": spread})
                return best

            pending = [s for s in sensors if s != root]
            while pending:
                fits = [(sid, fit(sid, list(pose))) for sid in pending]
                fits = [(sid, f) for sid, f in fits if f is not None]
                if not fits:
                    break
                sid, (_, angle, t, mirror, st) = max(fits, key=lambda item: item[1][0])
                pose[sid] = (t, _rot(angle), mirror)
                stats[sid] = st
                pending.remove(sid)
            for _ in range(3):  # refinement against all others
                for sid in list(stats):
                    f = fit(sid, [o for o in pose if o != sid])
                    if f is not None:
                        _, angle, t, mirror, st = f
                        pose[sid] = (t, _rot(angle), mirror)
                        stats[sid] = st
            # the root shares the quality of its best connection
            if stats:
                stats[root] = max(stats.values(), key=lambda st: st["inliers"])
            return pose, stats, pending

        def connections(sid):
            return sum(len(pairs(min(sid, o), max(sid, o))[0]) for o in sensors if o != sid)

        root = max(sensors, key=connections)
        best = None
        for root_mirror in (False, True):
            pose, stats, pending = constellation(root, root_mirror)
            solved = list(pose)
            if len(solved) < 2:
                continue
            # rotate and move the constellation onto the drawn positions (no scaling, no mirroring)
            phi, tau = kabsch(np.array([pose[s][0] for s in solved]), np.array([drawn(s) for s in solved]))
            Rp = _rot(phi)
            world = {s: (Rp @ pose[s][0] + tau, Rp @ pose[s][1], pose[s][2]) for s in solved}
            inside = self._inside_share(world)
            turn = sum(abs(self._turn(s, world[s][1])) for s in solved)
            key = (round(inside, 2), -turn)
            if best is None or key > best[0]:
                best = (key, world, stats, pending, inside)
        if best is None:
            return {"error": "Kein Sensorpaar hat genug gemeinsame Messungen, oder die gemessenen Abstände "
                             "passen nicht zur Zeichnung (mehr als 1 m daneben)."}
        _, world, stats, pending, inside = best

        out = {}
        for sid, (p, R, mirror) in world.items():
            current = self.config.sensor_by_id[sid]
            st = stats[sid]
            quality, reason = verdict(st["pairs"], st["inliers"], st["spread"])
            residual = float(np.linalg.norm(p - drawn(sid)))
            if quality == "ok" and residual > MAX_POS_RESIDUAL:
                quality, reason = "warn", (f"Liegt nach der Messung {100 * residual:.0f} cm neben der eingezeichneten "
                                           "Position. Sind die Positionen richtig eingezeichnet?")
            out[sid] = {
                "x": round(float(p[0]), 3), "y": round(float(p[1]), 3),
                "heading": round(_heading(math.atan2(R[1, 0], R[0, 0])), 1), "mirror": bool(mirror),
                "shift": round(residual, 3), "turn": round(self._turn(sid, R), 1),
                "quality": quality, "reason": reason,
                **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in st.items()},
            }
        ids = list(world)
        distances = [{"a": a, "b": b, "measured": round(float(np.linalg.norm(world[a][0] - world[b][0])), 2),
                      "drawn": round(float(np.linalg.norm(drawn(a) - drawn(b))), 2)}
                     for i, a in enumerate(ids) for b in ids[i + 1:]
                     if len(pairs(min(a, b), max(a, b))[0])]
        return {"sensors": out, "unsolved": pending, "inside": round(inside, 3), "distances": distances}

    def _turn(self, sid: str, R: np.ndarray) -> float:
        heading = _heading(math.atan2(R[1, 0], R[0, 0]))
        return (heading - self.config.sensor_by_id[sid].heading + 180) % 360 - 180

    def _inside_share(self, world: dict) -> float:
        """Share of the walked points inside the rooms, with these poses. 0.5 without rooms."""
        rooms = self.config.zones_of("room")
        if not rooms:
            return 0.5
        inside = total = 0
        for sid, (p, R, mirror) in world.items():
            raw = np.array([pt[1:] for pt in self.series[sid]])
            if not len(raw):
                continue
            for x, y in self._ground(sid, raw[:: max(1, len(raw) // 300)], mirror) @ R.T + p:
                total += 1
                inside += any(z.contains(x, y, 0.3) for z in rooms)
        return inside / total if total else 0.5
