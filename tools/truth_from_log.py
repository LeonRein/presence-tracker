"""Turn the "Jetzt gerade" entries of the Drehbuch page into episodes of the truth database.

usage: python tools/truth_from_log.py --config FILE [--db DIR] [--hold HOURS] [--warmup MIN]

Each entry says how many people are in each observed room from its time on; it holds until the next
entry, an "unknown" entry, or --hold hours. Consecutive entries form one episode ("live-<date>-<time>").
The model starts --warmup minutes before the first entry with nothing known (like after a restart).
--config: the app's config (sensor poses, floor plan) valid then, a file in <db>/configs. Episodes with
the same id are replaced; others are kept.
"""

import argparse
import datetime
import json
import pathlib

LOG_DIR = pathlib.Path.home() / ".config" / "presence-tracker" / "drehbuch"


def fmt(unix: float) -> str:
    t = datetime.datetime.fromtimestamp(unix)
    return t.strftime("%Y-%m-%d %H:%M:%S") + f".{int(round(unix % 1 * 10)) % 10}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(pathlib.Path.home() / ".config" / "presence-tracker" / "truth"))
    ap.add_argument("--config", required=True, help="config file name in <db>/configs")
    ap.add_argument("--hold", type=float, default=2.0, help="hours an entry holds without a next one")
    ap.add_argument("--warmup", type=float, default=10.0, help="minutes the model runs before the first entry")
    a = ap.parse_args()
    db_path = pathlib.Path(a.db) / "episodes.json"
    db = json.loads(db_path.read_text())
    if not (pathlib.Path(a.db) / "configs" / a.config).exists():
        raise SystemExit(f"no config {a.config} in {a.db}/configs")
    entries = sorted((json.loads(line) for f in sorted(LOG_DIR.glob("*.jsonl"))
                      for line in f.read_text(encoding="utf-8").splitlines() if line.strip()), key=lambda e: e["unix"])
    entries = [e for e in entries if "truth" in e]
    runs, cur = [], []
    for e in entries:
        if cur and e["unix"] - cur[-1]["unix"] > a.hold * 3600:
            runs.append((cur, cur[-1]["unix"] + a.hold * 3600))
            cur = []
        if e["truth"] is None:
            if cur:
                runs.append((cur, e["unix"]))
            cur = []
            continue
        cur.append(e)
    if cur:
        runs.append((cur, cur[-1]["unix"] + a.hold * 3600))
    new = []
    for run, end in runs:
        first = run[0]["unix"]
        new.append({
            "id": "live-" + datetime.datetime.fromtimestamp(first).strftime("%Y-%m-%d-%H%M"),
            "config": a.config, "what": "noted on the Drehbuch page (Jetzt gerade)", "source": "Leon",
            "start": {"time": fmt(first - a.warmup * 60), "people": None},
            "end": fmt(min(end, datetime.datetime.now().timestamp())),
            "truth": [[fmt(first - a.warmup * 60), None, "warm-up: not scored"]]
                     + [[fmt(e["unix"]), e["truth"], e.get("note", "")] for e in run],
        })
    ids = {e["id"] for e in new}
    db["episodes"] = [e for e in db["episodes"] if e["id"] not in ids] + new
    db_path.write_text(json.dumps(db, indent=1, ensure_ascii=False))
    for e in new:
        print(e["id"], len(e["truth"]) - 1, "entries, until", e["end"])


if __name__ == "__main__":
    main()
