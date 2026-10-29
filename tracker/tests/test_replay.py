"""presence-replay (core/presence-replay, docs/rust-core.md 6) drives the core without Python as the app does;
its trace is byte for byte what the Python shell gives on the same recording (rust-port/trace.py): the test
of the bindings. Needs the program built (cargo build --release -p presence-replay; CI does)."""

import collections
import dataclasses
import json
import os
import pathlib
import subprocess

import pytest

from presence_tracker.filter import Tracker
from presence_tracker.frames import Frame, SensorClock
from sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk

REPLAY = pathlib.Path(__file__).parents[1] / "core" / "target" / "release" / "presence-replay"


def shell_trace(config, recording: pathlib.Path, limit: float) -> str:
    """rust-port/trace.py through the shell: every 0.1 s of frame time a step and the zones' states, every
    1 s a line."""
    tr = Tracker(config)
    clocks = collections.defaultdict(SensorClock)
    t0, nxt, nd, out = None, 0.0, 0.0, []
    for line in recording.read_text().splitlines():
        m = json.loads(line)
        if not m["topic"].endswith("/frame"):
            continue
        sid = m["topic"].split("/")[1]
        f = Frame.of(m["payload"])
        tt = clocks[sid](m["t"], f)
        t0 = tt if t0 is None else t0
        if tt - t0 > limit:
            break
        tr.process_frame(sid, tt, f)
        if tt >= nxt and tr.start is not None:
            tr.step(tt)
            zones = tr.zone_states()
            nxt = tt + 0.1
            if tt >= nd:
                nd = tt + 1.0
                w = sorted((float(x) for x in tr.hyp_weights()), reverse=True)
                out.append(json.dumps({"t": tt - t0, "hyps": len(w), "w": w, "counts": tr.count_distribution(),
                                       "places": tr.place_distribution(),
                                       "zones": {z: dataclasses.asdict(s) for z, s in zones.items()}}) + "\n")
    return "".join(out)


# in CI it must have been built: a missing program fails there
@pytest.mark.skipif(not REPLAY.exists() and not os.environ.get("CI"),
                    reason="presence-replay not built (cd core && cargo build --release -p presence-replay)")
def test_the_replay_traces_what_the_shell_does(tmp_path):
    assert REPLAY.exists(), "presence-replay not built (cd core && cargo build --release -p presence-replay)"
    config = flat_config(entry=True)
    (tmp_path / "config.json").write_text(json.dumps(config.to_dict()))
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.1, 2.5), start=2, pauses={2: 40}))
    b = Person(walk((3.0, 1.0), (1.0, 1.0), start=10))
    recording = tmp_path / "rec.jsonl"
    with open(recording, "w") as f:
        f.write(json.dumps({"t": 0.0, "topic": "presence/pause", "payload": "off"}) + "\n")  # not a frame
        for t, sid, frame in simulate([a, b], sim_sensors(config), 90.0, plan=config):
            f.write(json.dumps({"t": 1e9 + t, "topic": f"presence/{sid}/frame", "payload": frame}) + "\n")
    subprocess.run([str(REPLAY), "trace", str(tmp_path / "config.json"), str(recording), "80", str(tmp_path / "rs.jsonl")],
                   check=True, timeout=120)
    rust = (tmp_path / "rs.jsonl").read_text()
    shell = shell_trace(config, recording, 80.0)
    assert len(shell.splitlines()) >= 40 and rust == shell
