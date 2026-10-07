"""The web UI's number fields (static/js/util.js numberError, bindNumber): an emptied or invalid field
keeps its previous value instead of saving 0. Runs the module in Node; skipped without it."""

import json
import pathlib
import shutil
import subprocess

import pytest

from presence_tracker.model import limits_dict

UTIL = pathlib.Path(__file__).parents[1] / "presence_tracker" / "static" / "js" / "util.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

SCRIPT = """
import { numberError, bindNumber } from %(util)s;
const lim = %(lim)s;
const out = {};
for (const raw of ['', '  ', 'abc', '0', '-1', '2', '1000', '1001', '1e400']) out[raw] = numberError(raw, lim);
// bindNumber with a stand-in for the input element
const applied = [];
const input = { value: '2', classList: { toggle() {} }, toggleAttribute() {}, closest: () => null };
bindNumber(input, lim, v => applied.push(v));
for (const typed of ['', '0', '3', 'x']) { input.value = typed; input.onchange(); out['after ' + typed] = input.value; }
const empty = { value: '0.5', classList: { toggle() {} }, toggleAttribute() {}, closest: () => null };
bindNumber(empty, [0, 1, true, true], v => applied.push(v), { allowEmpty: true });
empty.value = ''; empty.onchange();
console.log(JSON.stringify({ errors: out, applied, min: input.min ?? null, max: input.max }));
"""


def test_empty_or_out_of_range_keeps_the_previous_value():
    lim = limits_dict()["params"]["light_cost"]  # (0, 1000]
    script = SCRIPT % {"util": json.dumps(UTIL.as_uri()), "lim": json.dumps(lim)}
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(r.stdout)
    errors = out["errors"]
    assert errors["2"] == "" and errors["1000"] == ""
    for bad in ["", "  ", "abc", "0", "-1", "1001", "1e400"]:
        assert errors[bad], bad
    # emptied, 0 and text keep 2; 3 is taken
    assert errors["after "] == "2" and errors["after 0"] == "2" and errors["after 3"] == "3" and errors["after x"] == "3"
    assert out["applied"] == [3, None]  # allowEmpty: empty means "the default"
    assert out["min"] is None and out["max"] == 1000  # the lower bound is excluded: no min attribute
