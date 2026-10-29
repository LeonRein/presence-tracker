"""The LD2410C's energies in the filter (MODEL.md 4.3). The energy model itself, the background and its
learning, the amplitude of a stay on the tiles and the view of other rooms: the Rust core's tests
(ld2410/*.rs, filter/mixture.rs, world/ld.rs)."""

import pytest

from presence_tracker.filter import Tracker
from presence_tracker.sim import Person, simulate
from presence_tracker.frames import Frame
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk


@pytest.mark.hooks
def test_all_energies_0_are_no_measurement():
    """Somebody sits 1.1 m in front of sensor a, both LD2450 track them; from 30 s a's LD2410C reports
    every energy 0 - the firmware's "no value" (NaN -> 0), never in 1.5 million recorded frames. Weighed,
    it outvoted both tracks: P 4e-6 (BUGS 14). It is no measurement, like a frame without energies."""
    config = flat_config(entry=True)
    c = config.params.light_cost / (config.params.light_cost + 1.0)
    p = Person(walk((-1.0, 4.0), FLUR_DOOR, (0.8, 0.8), (0.82, 0.8), start=1, pauses={2: 200}))
    tr = Tracker(config, start=0.0, people=["outside"])
    low = 1.0
    for t, sid, frame in simulate([p], sim_sensors(config), 120, plan=config):
        if sid == "a" and t > 30 and frame.get("ld2410"):
            frame["ld2410"].update(move_gates=[0] * 9, still_gates=[0] * 9, moving=False, still=False)
        tr.process_frame(sid, t, Frame.of(frame))
        if t > 40:
            low = min(low, 1 - tr.count_distribution()["wohn"][0])
    assert low > c, low
    assert tr._c.ld_last(tr.sidx["a"]) is None  # nothing taken as its energies
