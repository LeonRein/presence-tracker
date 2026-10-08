"""When the vacuum robot was out, from Home Assistant's history: the pauses of learning for the offline
tools, for the days before the switch "Lernen pausieren" (MODEL.md 10, "Hintergrundaktivität"), as
Node-RED switches it: on while the robot is not docked, off MARGIN s after it docked.

usage: HA_URL=http://homeassistant:8123 HA_TOKEN=... python tools/vacuum_history.py --entity vacuum.x
           --from "YYYY-mm-dd HH:MM:SS" [--to ...] [--margin 120] --out FILE
       python tools/vacuum_history.py --history FILE.json [--margin 120] --out FILE

The token is read from the environment and never written anywhere. --history: the states as Home
Assistant's REST API gives them (/api/history/period, a list of {"state", "last_changed"}, or the list
of such lists), e.g. saved by hand. Active: every state but INACTIVE; "unavailable" and "unknown" keep
the state before (the integration reconnects while the robot stays where it is). The output (private:
it says when somebody started the robot) is read by tools/report_eval.py, entries.py, replay.py,
calibrate_offline.py and ld2410/phantom.py with --pauses: {"entity", "margin", "intervals": [[start, end
(None: still out)], ...] (Unix time), "local": the same as local time, for reading}.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

INACTIVE = {"docked", "idle", "charging", "error"}
UNKNOWN = {"unavailable", "unknown", ""}
MARGIN = 120.0  # s after docking, as the Node-RED flow


def parse_time(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def fetch(url: str, token: str, entity: str, t0: float, t1: float) -> list:
    iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()  # noqa: E731
    q = urllib.parse.urlencode({"filter_entity_id": entity, "end_time": iso(t1), "minimal_response": "",
                                "no_attributes": "", "significant_changes_only": "0"})
    req = urllib.request.Request(f"{url.rstrip('/')}/api/history/period/{urllib.parse.quote(iso(t0))}?{q}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def states_of(data) -> list:
    """[(Unix time, state)] from the REST API's answer (or one entity's list of it)."""
    if data and isinstance(data[0], list):
        data = data[0]
    out = []
    for s in data:
        when = s.get("last_changed") or s.get("last_updated")
        out.append((datetime.datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp(), str(s.get("state", ""))))
    return sorted(out)


def intervals(states: list, margin: float) -> list:
    """[[start, end or None], ...]: from the first active state to the next inactive one + margin
    (merged when the robot leaves again before)."""
    out, active = [], False
    for t, st in states:
        if st in UNKNOWN:
            continue
        now = st not in INACTIVE
        if now and not active:
            if out and out[-1][1] is not None and t <= out[-1][1]:
                out[-1][1] = None  # out again within the margin: one pause
            else:
                out.append([t, None])
        elif active and not now:
            out[-1][1] = t + margin
        active = now
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entity")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--history")
    ap.add_argument("--margin", type=float, default=MARGIN)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.history:
        data = json.load(open(a.history))
    else:
        url, token = os.environ.get("HA_URL"), os.environ.get("HA_TOKEN")
        if not (url and token and a.entity and a.start):
            ap.error("--entity and --from, HA_URL and HA_TOKEN in the environment (or --history)")
        data = fetch(url, token, a.entity, parse_time(a.start), parse_time(a.end) if a.end else time.time())
    iv = intervals(states_of(data), a.margin)
    local = lambda t: None if t is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))  # noqa: E731
    out = {"entity": a.entity, "margin": a.margin, "intervals": iv, "local": [[local(s), local(e)] for s, e in iv]}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    for s, e in out["local"]:
        print(f"{s} - {e or 'still out'}")
    print(f"{len(iv)} intervals -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
