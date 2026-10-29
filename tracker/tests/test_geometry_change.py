"""What was collected and learned holds only under the geometry it was collected under (the core's key,
presence_core.geometry_key: where the sensors hang and how they see and are read, the walls, doors and zones).
A save of another geometry discards all of it for every sensor - the calibration's measurements, the ghost map,
the LD2410C background, the destination map, the measured view of the LD2410Cs - and says so; a save of
anything else (names, a sensor switched off, parameters) keeps all of it. At a start, a file of another geometry
(the configuration changed while the app did not run) is discarded the same way."""

import json
import pathlib
import shutil
import subprocess

import presence_core
import pytest

from presence_tracker.app import COLLECTED, App
from presence_tracker.filter import Tracker
from test_config_api import call
from test_filter import flat_config
from test_robust import run_a_while

UTIL = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js" / "util.js"
VIEW = {"wohn<-balkon": 0.5}  # a measured view of the LD2410C, as tools/ld2410/footprint.py measures it


def learned_app(tmp_path):
    """An app that collected calibration data and learned for a while, all of it saved."""
    data = flat_config(entry=True).to_dict()
    data["ld_view"] = dict(VIEW)
    (tmp_path / "tracker.json").write_text(json.dumps(data))
    app = App(tmp_path, publish=False)
    run_a_while(app)
    app._save_learned()
    assert app.calibrator.count() > 0 and all((tmp_path / name).exists() for name in COLLECTED)
    assert learned(app) != nothing_learned(app)
    return app


def learned(app) -> dict:
    return json.loads(json.dumps(app.tracker.learned()))


def nothing_learned(app) -> dict:
    return json.loads(json.dumps(Tracker(app.config).learned()))


def moved(d):
    d["sensors"][0]["x"] += 0.3


def turned_by_a_calibration(d):  # what "Übernehmen" in the calibration tab does with a result
    d["sensors"][1]["heading"] += 4.2
    d["sensors"][1]["scale"] = 1.04


def walled(d):
    d["walls"][0]["points"][1] = [6.0, 0.2]


def widened(d):
    d["sensors"][0]["fov"] = 100.0


@pytest.mark.parametrize("change", [moved, turned_by_a_calibration, walled, widened])
def test_a_save_of_another_geometry_discards_everything_collected_and_learned(tmp_path, change):
    app = learned_app(tmp_path)
    rows = app.calibrator.count()
    data = app.config.to_dict()
    change(data)
    [(status, put)] = call(app, ("PUT", "/api/config", data))
    assert status == 200
    assert put["discarded"] == {"calibration": rows, "ld_view": len(VIEW)}
    # the calibration tab: nothing collected
    assert app.calibrator.count() == 0 and app.calibrator.status()["frames"] == {}
    # the files are gone, the model has nothing learned, the measured view went with the old geometry
    assert not any((tmp_path / name).exists() for name in COLLECTED)
    assert learned(app) == nothing_learned(app)
    assert put["config"]["ld_view"] == {} == json.loads((tmp_path / "tracker.json").read_text())["ld_view"]
    # what is saved from now on is of the new geometry: a restart takes it
    run_a_while(app, 20.0)
    app._save_learned()
    again = App(tmp_path, publish=False)
    assert learned(again) == learned(app) and again.calibrator.count() == app.calibrator.count() > 0


def test_a_save_of_the_same_geometry_keeps_everything(tmp_path):
    app = learned_app(tmp_path)
    rows, before = app.calibrator.count(), learned(app)
    data = app.config.to_dict()
    data["params"]["light_cost"] = 3.0  # only the outputs
    data["params"]["wall_margin"] = 0.5  # the model, not the geometry
    data["sensors"][1]["name"] = "Ecke"
    data["sensors"][1]["enabled"] = False  # switched off, where it hangs
    data["zones"][0]["name"] = "Wohnen"
    [(status, put)] = call(app, ("PUT", "/api/config", data))
    assert status == 200 and put["discarded"] is None
    assert app.calibrator.count() == rows and all((tmp_path / name).exists() for name in COLLECTED)
    assert learned(app) == before and put["config"]["ld_view"] == VIEW


def test_a_start_with_another_geometry_discards_the_files_of_the_old_one(tmp_path, caplog):
    learned_app(tmp_path)
    data = json.loads((tmp_path / "tracker.json").read_text())
    moved(data)  # changed while the app did not run (another tracker.json put in place)
    (tmp_path / "tracker.json").write_text(json.dumps(data))
    caplog.set_level("INFO")
    again = App(tmp_path, publish=False)
    assert again.calibrator.count() == 0 and learned(again) == nothing_learned(again)
    assert not any((tmp_path / name).exists() for name in COLLECTED)
    assert sum("collected under another geometry" in r.message for r in caplog.records) == len(COLLECTED)


def test_a_start_with_the_same_geometry_keeps_the_files(tmp_path):
    app = learned_app(tmp_path)
    data = json.loads((tmp_path / "tracker.json").read_text())
    data["params"]["light_cost"] = 3.0
    (tmp_path / "tracker.json").write_text(json.dumps(data))
    again = App(tmp_path, publish=False)
    assert again.calibrator.count() == app.calibrator.count() and learned(again) == learned(app)


def test_each_file_carries_the_key_of_its_geometry(tmp_path):
    app = learned_app(tmp_path)
    key = presence_core.geometry_key(app.config.model_json())
    for name in COLLECTED[1:]:
        assert json.loads((tmp_path / name).read_text())["geometry"] == key
    assert app.calibrator.load(tmp_path / "calibration.npz")  # the calibrator checks its own


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_web_ui_says_what_was_discarded(tmp_path):
    """The words are the web UI's (static/js/util.js discardedText), the numbers the server's answer."""
    app = learned_app(tmp_path)
    data = app.config.to_dict()
    moved(data)
    [(_, put)] = call(app, ("PUT", "/api/config", data))
    script = (f"import {{ discardedText }} from {json.dumps(UTIL.as_uri())};\n"
              f"console.log(JSON.stringify([discardedText({json.dumps(put['discarded'])}), discardedText(null),"
              f" discardedText({{calibration: 0, ld_view: 0}})]));")
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    said, nothing, empty = json.loads(r.stdout)
    rows = f"{put['discarded']['calibration']:,}".replace(",", ".")
    assert said == (f"Sensorlage oder Grundriss geändert, verworfen: {rows} Kalibriermessungen aller Sensoren, alles "
                    "Gelernte (Geisterkarte, LD2410C-Hintergrund, Zielkarte), gemessener Blick der LD2410C "
                    "(1 Raumpaar).")
    assert nothing == "" and "verworfen: 0 Kalibriermessungen" in empty and "Blick" not in empty
