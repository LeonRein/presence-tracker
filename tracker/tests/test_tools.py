"""tools/report_eval.py on a small simulated recording: the truth windows are scored each on its own."""

import json
import pathlib
import subprocess
import sys
import time

from presence_tracker.sim import Person, simulate
from test_filter import FLUR_DOOR, flat_config
from test_frames import sim_sensors, walk

TOOLS = pathlib.Path(__file__).parents[2] / "tools"
T0 = time.mktime(time.strptime("2026-10-01 12:00:00", "%Y-%m-%d %H:%M:%S"))


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + t))


def test_a_window_that_excuses_walks_leaves_the_others_alone(tmp_path):
    """Two windows at the same time, the first one excusing walks (a night: whoever an LD2450 measures
    walks through): until 0.18 the second one was scored with the first one's filter too, without the
    rooms in which somebody was measured."""
    config = flat_config(entry=True)
    (tmp_path / "config.json").write_text(json.dumps(config.to_dict()))
    rec = tmp_path / "recordings"
    rec.mkdir()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.1, 2.5), start=2, pauses={2: 60}))
    with open(rec / time.strftime("%Y%m%d-%H.jsonl", time.localtime(T0)), "w") as f:
        for t, sid, frame in simulate([a], sim_sensors(config), 50.0, walls=config.wall_segments):
            f.write(json.dumps({"t": T0 + t, "topic": f"presence/{sid}/frame", "payload": frame}) + "\n")
    truth = {"app_starts": [stamp(0)], "reports": [
        {"name": "night", "from": stamp(20), "to": stamp(45), "text": "", "rooms": {"wohn": 0}, "walks": 30},
        {"name": "sits", "from": stamp(20), "to": stamp(45), "text": "", "rooms": {"wohn": 1}},
    ]}
    (tmp_path / "truth.json").write_text(json.dumps(truth))
    r = subprocess.run([sys.executable, str(TOOLS / "report_eval.py"), "--config", str(tmp_path / "config.json"),
                        "--truth", str(tmp_path / "truth.json"), "--recordings", str(rec),
                        "--tracker", str(pathlib.Path(__file__).parents[1])],
                       capture_output=True, text=True, check=True, timeout=300)
    lines = r.stdout.splitlines()
    sits = lines[lines.index(next(x for x in lines if x.startswith("sits:"))) + 1]
    assert "wohn=1: P 1.00" in sits, r.stdout
    assert "wrongly off 0.00 of 1 occupied" in r.stdout
