"""Interacting multiple model (IMM) Kalman filter for one person.

State [x, y, vx, vy] in the house frame. Two motion models:
  WALK:  constant velocity with white-noise acceleration
  STILL: position wanders a little, velocity decays to zero within a fraction of a second
The model probability is the "moving / still" classification of the track.
"""

import math

import numpy as np

WALK, STILL = 0, 1
_I4 = np.eye(4)
_H_POS = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
_LOG_2PI = math.log(2 * math.pi)


class IMMState:
    __slots__ = ("x", "P", "mu")

    def __init__(self, x: np.ndarray, P: np.ndarray, mu: np.ndarray):
        self.x = x  # (2, 4)  per-model mean
        self.P = P  # (2, 4, 4) per-model covariance
        self.mu = mu  # (2,)  model probabilities

    @classmethod
    def from_position(cls, pos, R, walk_prob: float = 0.5) -> "IMMState":
        x = np.zeros((2, 4))
        x[:, :2] = pos
        P = np.zeros((2, 4, 4))
        P[:, :2, :2] = R
        P[WALK, 2:, 2:] = np.eye(2) * 1.0**2
        P[STILL, 2:, 2:] = np.eye(2) * 0.1**2
        return cls(x, P, np.array([walk_prob, 1 - walk_prob]))

    def mean(self) -> tuple:
        """Combined estimate (x, P)."""
        x = self.mu @ self.x
        P = np.zeros((4, 4))
        for j in range(2):
            d = self.x[j] - x
            P += self.mu[j] * (self.P[j] + np.outer(d, d))
        return x, P

    def copy(self) -> "IMMState":
        return IMMState(self.x.copy(), self.P.copy(), self.mu.copy())


class IMM:
    def __init__(self, params):
        self.p = params

    def _models(self, dt: float):
        F = np.empty((2, 4, 4))
        Q = np.zeros((2, 4, 4))
        # WALK: constant velocity
        F[WALK] = _I4
        F[WALK, 0, 2] = F[WALK, 1, 3] = dt
        q = self.p.walk_accel**2
        q11, q12, q22 = q * dt**3 / 3, q * dt**2 / 2, q * dt
        Q[WALK, 0, 0] = Q[WALK, 1, 1] = q11
        Q[WALK, 0, 2] = Q[WALK, 2, 0] = Q[WALK, 1, 3] = Q[WALK, 3, 1] = q12
        Q[WALK, 2, 2] = Q[WALK, 3, 3] = q22
        # STILL: velocity decays with 0.15 s, position random walk
        a = math.exp(-dt / 0.15)
        F[STILL] = _I4
        F[STILL, 0, 2] = F[STILL, 1, 3] = 0.15 * (1 - a)
        F[STILL, 2, 2] = F[STILL, 3, 3] = a
        Q[STILL, 0, 0] = Q[STILL, 1, 1] = self.p.still_jitter**2 * dt
        Q[STILL, 2, 2] = Q[STILL, 3, 3] = 0.05**2 * dt
        return F, Q

    def predict(self, s: IMMState, dt: float, force_still: bool = False, max_sigma: float | None = None):
        if dt <= 0:
            return
        if force_still:
            Pi = np.array([[0.0, 1.0], [0.0, 1.0]])
        else:
            p_ws = 1 - math.exp(-self.p.walk_to_still * dt)
            p_sw = 1 - math.exp(-self.p.still_to_walk * dt)
            Pi = np.array([[1 - p_ws, p_ws], [p_sw, 1 - p_sw]])
        # mixing
        c = s.mu @ Pi
        c = np.maximum(c, 1e-12)
        w = Pi * s.mu[:, None] / c[None, :]  # w[i, j] = P(model i before | model j now)
        x0 = w.T @ s.x
        P0 = np.empty_like(s.P)
        for j in range(2):
            acc = np.zeros((4, 4))
            for i in range(2):
                if w[i, j] < 1e-9:
                    continue
                d = s.x[i] - x0[j]
                acc += w[i, j] * (s.P[i] + np.outer(d, d))
            P0[j] = acc
        F, Q = self._models(dt)
        s.x = np.einsum("jab,jb->ja", F, x0)
        s.P = F @ P0 @ F.transpose(0, 2, 1) + Q
        s.mu = c / c.sum()
        if max_sigma is not None:
            limit = max_sigma**2
            for j in range(2):
                var = max(s.P[j, 0, 0], s.P[j, 1, 1])
                if var > limit:
                    k = math.sqrt(limit / var)
                    s.P[j, :2, :] *= k
                    s.P[j, :, :2] *= k

    def update(self, s: IMMState, z: np.ndarray, R: np.ndarray, radial: np.ndarray | None = None) -> float:
        """Measurement update. z = [x, y] or [x, y, v_radial] when `radial` (unit vector
        from the sensor to the target) is given. Returns the log-likelihood of the measurement."""
        if radial is None:
            H = _H_POS
        else:
            H = np.zeros((3, 4))
            H[0, 0] = H[1, 1] = 1.0
            H[2, 2:] = radial
        logl = np.empty(2)
        for j in range(2):
            Pj = s.P[j]
            PHt = Pj @ H.T
            S = H @ PHt + R
            Sinv = np.linalg.inv(S)
            y = z - H @ s.x[j]
            K = PHt @ Sinv
            s.x[j] = s.x[j] + K @ y
            Pn = (_I4 - K @ H) @ Pj
            s.P[j] = 0.5 * (Pn + Pn.T)
            sign, logdet = np.linalg.slogdet(S)
            logl[j] = -0.5 * (y @ Sinv @ y + logdet + len(z) * _LOG_2PI)
        m = logl.max()
        weights = s.mu * np.exp(logl - m)
        total = weights.sum()
        s.mu = np.clip(weights / total, 1e-4, 1 - 1e-4)
        s.mu /= s.mu.sum()
        return float(m + math.log(total))

    @staticmethod
    def likelihood(s: IMMState, z: np.ndarray, R: np.ndarray) -> float:
        """Density of a position measurement under the predicted track (per m^2)."""
        x, P = s.mean()
        S = P[:2, :2] + R
        y = z - x[:2]
        d2 = float(y @ np.linalg.solve(S, y))
        return math.exp(-0.5 * d2) / (2 * math.pi * math.sqrt(max(np.linalg.det(S), 1e-12)))

    @staticmethod
    def association_cost(s: IMMState, z: np.ndarray, R: np.ndarray, radial: np.ndarray) -> tuple:
        """(cost, position_d2): cost = -2 ln of the mixture likelihood of a full measurement
        [x, y, v_radial] over the motion models (up to a constant), position_d2 = the smallest
        position-only Mahalanobis distance for gating. A still model expects v_radial ~ 0, so a
        walker's measurement is costly for someone sitting: no speed threshold needed."""
        H = np.zeros((3, 4))
        H[0, 0] = H[1, 1] = 1.0
        H[2, 2:] = radial
        terms = []
        d2pos = math.inf
        for j in range(2):
            S = H @ s.P[j] @ H.T + R
            y = z - H @ s.x[j]
            d2 = float(y @ np.linalg.solve(S, y))
            terms.append(math.log(max(s.mu[j], 1e-12)) - 0.5 * d2)
            Sp = s.P[j][:2, :2] + R[:2, :2]
            d2pos = min(d2pos, float(y[:2] @ np.linalg.solve(Sp, y[:2])))
        m = max(terms)
        return -2.0 * (m + math.log(sum(math.exp(v - m) for v in terms))), d2pos

    @staticmethod
    def gate_distance(s: IMMState, z: np.ndarray, R: np.ndarray) -> float:
        """Squared Mahalanobis distance of a position measurement to the combined estimate."""
        x, P = s.mean()
        S = P[:2, :2] + R
        y = z - x[:2]
        return float(y @ np.linalg.solve(S, y))
