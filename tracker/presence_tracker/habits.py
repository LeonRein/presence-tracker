"""Where people stop, where they get up, where they stay unseen (MODEL.md 3.1 and 3.3), learned
per 0.5 m cell of the map from the particle clouds, weighted by their probability.

Each rate is the prior rate times a factor per cell:

  factor = (K + events) / (K + expected)

events: what the clouds did there (weighted, counted a few seconds later, when the measurements
since have confirmed or refuted it); expected: what the prior rate would have produced in the
same (weighted) time there. Without data the factor is 1; it never needs a rule.
"""

import numpy as np

CELL = 0.5  # m
ORIGIN = (-30.0, -30.0)
SIZE = 120
K = 3.0  # prior events: a cell needs a few to move away from the prior
HALF_LIFE = 14 * 24 * 3600.0  # s
KINDS = ("stop", "go", "drop")


def cells(pos: np.ndarray) -> tuple:
    i = np.clip(((pos[:, 0] - ORIGIN[0]) / CELL).astype(int), 0, SIZE - 1)
    j = np.clip(((pos[:, 1] - ORIGIN[1]) / CELL).astype(int), 0, SIZE - 1)
    return i, j


class Habits:
    def __init__(self):
        self.events = {k: np.zeros((SIZE, SIZE)) for k in KINDS}
        self.expected = {k: np.zeros((SIZE, SIZE)) for k in KINDS}
        self.last_decay = None
        self.changed = False

    def factor(self, kind: str, pos: np.ndarray) -> np.ndarray:
        i, j = cells(pos)
        return (K + self.events[kind][i, j]) / (K + self.expected[kind][i, j])

    def add(self, kind: str, pos: np.ndarray, events=None, expected=None):
        if len(pos) == 0:
            return
        i, j = cells(pos)
        if events is not None:
            np.add.at(self.events[kind], (i, j), events)
        if expected is not None:
            np.add.at(self.expected[kind], (i, j), expected)
        self.changed = True

    def decay(self, t: float):
        if self.last_decay is None:
            self.last_decay = t
        if t - self.last_decay < 600:
            return
        f = 0.5 ** ((t - self.last_decay) / HALF_LIFE)
        for store in (self.events, self.expected):
            for grid in store.values():
                grid *= f
        self.last_decay = t

    def forget(self):
        """A sensor was moved: where people drop out depends on every sensor."""
        self.events["drop"][:] = 0
        self.expected["drop"][:] = 0
        self.changed = True

    def to_dict(self) -> dict:
        def sparse(g):
            return {f"{i},{j}": round(float(g[i, j]), 3) for i, j in zip(*np.nonzero(g > 1e-3))}
        return {"events": {k: sparse(v) for k, v in self.events.items()},
                "expected": {k: sparse(v) for k, v in self.expected.items()}, "last_decay": self.last_decay}

    def load_dict(self, data: dict):
        for store, name in ((self.events, "events"), (self.expected, "expected")):
            for k in KINDS:
                store[k][:] = 0
                for key, v in data.get(name, {}).get(k, {}).items():
                    i, j = map(int, key.split(","))
                    store[k][i, j] = v
        self.last_decay = data.get("last_decay")
