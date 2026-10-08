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


def test_windows_in_a_pause_are_not_scored(tmp_path):
    """Lernen pausieren (pause.py): the switch recorded in the recordings (tools/record.py) and the
    intervals of --pauses (tools/vacuum_history.py) pause learning in the replay; a window that reaches
    into a pause is not scored."""
    from presence_tracker import pause
    config = flat_config(entry=True)
    (tmp_path / "config.json").write_text(json.dumps(config.to_dict()))
    rec = tmp_path / "recordings"
    rec.mkdir()
    a = Person(walk((-1.0, 4.0), FLUR_DOOR, (3.0, 2.5), (3.1, 2.5), start=2, pauses={2: 60}))
    switched_off = False
    with open(rec / time.strftime("%Y%m%d-%H.jsonl", time.localtime(T0)), "w") as f:
        f.write(json.dumps({"t": T0 + 10, "topic": pause.COMMAND, "payload": "ON"}) + "\n")
        for t, sid, frame in simulate([a], sim_sensors(config), 60.0, walls=config.wall_segments):
            if t > 20 and not switched_off:
                f.write(json.dumps({"t": T0 + 20, "topic": pause.COMMAND, "payload": "OFF"}) + "\n")
                switched_off = True
            f.write(json.dumps({"t": T0 + t, "topic": f"presence/{sid}/frame", "payload": frame}) + "\n")
    (tmp_path / "vacuum.json").write_text(json.dumps({"intervals": [[T0 + 40, T0 + 45]]}))
    truth = {"app_starts": [stamp(0)], "reports": [
        {"name": "switch", "from": stamp(15), "to": stamp(25), "text": "", "rooms": {"wohn": 1}},
        {"name": "between", "from": stamp(30), "to": stamp(38), "text": "", "rooms": {"wohn": 1}},
        {"name": "vacuum", "from": stamp(44), "to": stamp(50), "text": "", "rooms": {"wohn": 1}},
    ]}
    (tmp_path / "truth.json").write_text(json.dumps(truth))
    args = [sys.executable, str(TOOLS / "report_eval.py"), "--config", str(tmp_path / "config.json"),
            "--truth", str(tmp_path / "truth.json"), "--recordings", str(rec),
            "--tracker", str(pathlib.Path(__file__).parents[1])]
    out = subprocess.run(args + ["--pauses", str(tmp_path / "vacuum.json")], capture_output=True, text=True,
                         check=True, timeout=300).stdout
    lines = out.splitlines()
    after = {x.split(":")[0]: lines[k + 1] for k, x in enumerate(lines) if x.split(":")[0] in ("switch", "between", "vacuum")}
    assert "nicht bewertet" in after["switch"] and "nicht bewertet" in after["vacuum"], out
    assert "wohn=1: P" in after["between"] and "of 1 occupied" in out, out
    every = subprocess.run(args + ["--no-pauses"], capture_output=True, text=True, check=True, timeout=300).stdout
    assert "nicht bewertet" not in every and "of 3 occupied" in every, every
