"""The tab "Gelerntes" (static/js: view.js _learned, panels.js learnedBars): the web UI draws what the core says was
learned (GET /api/learned), all sensors at once in their colours, the one chosen on the map strong and the others
faint, and knows no number of the model - the destination cells'
colour the outcome the core names likeliest, the gates' distances the core's gate length, the measured view of the
chosen sensor as sent (none chosen or none measured: nothing drawn). Runs the modules in Node; skipped without it."""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from test_config_api import an_app

JS = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def draw(learned, config, layer, sensor=None, colors=False):
    """The SVG of the learned layer with a stand-in for the map (1 m = 100 px, y up)."""
    script = f"""
import {{ MapView }} from {json.dumps((JS / 'view.js').as_uri())};
import {{ state }} from {json.dumps((JS / 'store.js').as_uri())};
Object.assign(state, {{ config: {json.dumps(config)}, learned: {json.dumps(learned)}, learnedLayer: {json.dumps(layer)},
  selection: {json.dumps({"kind": "sensor", "id": sensor} if sensor else None)}, learnedColors: {json.dumps(colors)} }});
const view = {{ s: 100, P: (x, y) => [x * 100, -y * 100] }};
view.pts = list => list.map(([x, y]) => view.P(x, y).join(',')).join(' ');
console.log(JSON.stringify(MapView.prototype._learned.call(view, {json.dumps(learned)})));"""
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def bars(background, live, gate):
    script = f"""
import {{ learnedBars }} from {json.dumps((JS / 'panels.js').as_uri())};
import {{ state }} from {json.dumps((JS / 'store.js').as_uri())};
state.derived = {{ ld2410_gate: {gate} }};
console.log(JSON.stringify(learnedBars({json.dumps(background)}, {json.dumps(live)})));"""
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def opacities(svg, attr="fill-opacity"):
    return [float(x) for x in re.findall(attr + r'="([0-9.]+)"', svg)]


def test_the_learned_layers_are_drawn_from_the_cores_numbers_only(tmp_path):
    app = an_app(tmp_path)
    learned, config = app.tracker.learned_view(), app.config.to_dict()
    # the destination map: an arrow per cell along its velocity, the cell in the colour of the outcome the core names
    d = learned["destinations"]
    d["walks"] = 2.0
    d["cells"] = [{"at": [1.0, 1.0], "square": [0.9, 0.9], "room": "wohn", "walks": 2.0, "velocity": [1.35, 0.0],
                   "shares": [0.9, 0.1, 0.0, 0.0], "likely": 1},
                  {"at": [2.0, 1.0], "square": [1.8, 0.9], "room": "wohn", "walks": 1.0, "velocity": [0.0, -0.5],
                   "shares": [0.0, 0.0, 0.0, 1.0], "likely": None}]
    svg = draw(learned, config, "destinations", colors=True)
    paths = re.findall(r'<path d="M([-0-9.]+),([-0-9.]+)L([-0-9.]+),([-0-9.]+)M', svg)
    (x0, y0, x1, y1), (u0, v0, u1, v1) = [[float(v) for v in p] for p in paths]
    assert y0 == y1 and x1 > x0 and u0 == u1 and v1 > v0  # east; south (screen y down)
    assert abs((x1 - x0) / (v1 - v0) - 1.35 / 0.5) < 0.05  # as long as fast
    assert opacities(svg, "stroke-opacity")[0] == 1.0 > opacities(svg, "stroke-opacity")[1]
    fills = re.findall(r'<rect [^>]*fill="([^"]+)"', svg)
    assert fills == ["#d9822b"]  # the outcome the core names, not the largest share; none where all stayed
    assert opacities(svg)[0] < 0.3  # as strong as its share (0.1)
    assert "<rect" not in draw(learned, config, "destinations", colors=False)

    # the measured view: none (Anteil 1) draws nothing; a measured one per room as sent, for the chosen sensor only
    assert draw(learned, config, "ld_view", "a") == ""
    learned["sensors"]["a"]["ld_view"] = {"wohn": 0.25}
    svg = draw(learned, config, "ld_view", "a")
    assert svg.count("<polygon") == 1 and "25 %" in svg
    assert draw(learned, config, "ld_view") == "" == draw(learned, config, "ld_view", "b")
    learned["sensors"]["a"]["ld_view"] = {"wohn": 0.998}  # not all of it: not "100 %"
    assert "99,8 %" in draw(learned, config, "ld_view", "a")


def test_the_learned_background_is_drawn_per_gate_of_the_cores_length(tmp_path):
    b = an_app(tmp_path).tracker.learned_view()["sensors"]["a"]["background"]
    live = {"move_gates": [50, 40, 30, 20, 10, 5, 5, 5, 5], "still_gates": [0, 0, 30, 20, 10, 5, 5, 5, 5]}
    svg = bars(b, live, 0.5)
    labels = re.findall(r'font-size="8" fill="var\(--muted\)">([^<]*)<', svg)
    assert labels[:3] == ["0,00", "1,00", "2,00"] and len(labels) == 10  # every other of 9 gates, moving and still
    # learned and now per gate; no still energy in gates 0 and 1
    assert svg.count("<title>gelernt") == 9 + 7 and svg.count("<title>jetzt") == 9 + 7
    assert svg.count("<title>Langzeitwert") == 9 + 7
    assert "Engineering Mode" not in svg and "Engineering Mode" in bars(b, {}, 0.5)
