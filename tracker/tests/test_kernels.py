"""The compiled kernels (kernels.py): the tracker calls each with the types warm() compiles, so the
image's cache covers everything and the app never compiles while it runs (on Home Assistant that
would stop it for many seconds)."""

from presence_tracker import kernels
from presence_tracker.sim import Person

from test_filter import BALCONY_DOOR, FLUR_DOOR, flat_config, run
from test_frames import walk


def _kernels():
    return {n: k for n, k in vars(kernels).items() if hasattr(k, "signatures")}


def test_the_tracker_calls_the_kernels_only_as_warm_compiles_them():
    kernels.warm()
    before = {n: list(k.signatures) for n, k in _kernels().items()}
    assert all(len(s) == 1 for n, s in before.items() if not n.startswith("_")), before  # helpers: from the cache none
    config = flat_config()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (1.0, 1.0), (5.0, 4.0), (5.1, 4.0), start=1, pauses={3: 5}))
    b = Person(walk((7.0, 2.0), BALCONY_DOOR, (5.0, 1.0), (1.0, 4.0), (1.1, 4.0), start=1, pauses={3: 5}))
    crowd, _ = run(config, [a, b], 25, ["flur", "balkon"], bias=0.2, bias_time=3.5, resolution=0.5, ghost_rate=20)
    crowd.zone_states()
    after = {n: list(k.signatures) for n, k in _kernels().items()}
    assert after == before
