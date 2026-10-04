"""Tiny page for the phone: one button per step of TESTDREHBUCH.md, the press time is logged on
this machine (its clock is the reference, like the recordings). Log: one JSON line per press in
~/.config/presence-tracker/drehbuch/<date>.jsonl.

    python3 tools/drehbuch_server.py [--port 8765]
"""

import argparse
import datetime
import html
import json
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "tracker" / "TESTDREHBUCH.md"
LOG_DIR = pathlib.Path.home() / ".config" / "presence-tracker" / "drehbuch"


def steps() -> list:
    out = []
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", line)
        if m:
            text = re.sub(r"\*\*(.*?)\*\*", r"\1", m.group(3).strip())
            out.append({"n": int(m.group(1)), "who": m.group(2).strip(), "what": text, "dur": m.group(4).strip()})
    return out


def log_file() -> pathlib.Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{datetime.date.today().isoformat()}.jsonl"


def presses() -> list:
    f = log_file()
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drehbuch</title><style>
body{font-family:system-ui,sans-serif;margin:0;padding:12px 16px 80px;background:#f6f6f4;color:#222}
h1{font-size:20px;margin:4px 0 12px}
.step{background:#fff;border-radius:12px;padding:12px;margin:10px 0;box-shadow:0 1px 3px #0002}
.head{display:flex;gap:10px;align-items:baseline}
.n{font-weight:700;font-size:22px;min-width:28px}
.who{font-weight:600}
.dur{color:#666;font-size:14px}
.what{margin:6px 0 10px;font-size:15px}
button{width:100%;font-size:18px;padding:14px;border:0;border-radius:10px;background:#2f6fde;color:#fff}
.done button{background:#2e9d5a}
.times{color:#2e9d5a;font-size:14px;margin-top:6px}
.note{display:flex;gap:8px;margin-top:20px}
.note input{flex:1;font-size:16px;padding:10px;border-radius:8px;border:1px solid #bbb}
.note button{width:auto}
#clock{font-variant-numeric:tabular-nums;color:#666}
@media (prefers-color-scheme: dark){body{background:#151515;color:#eee}.step{background:#222}.dur,#clock{color:#aaa}}
</style></head><body>
<h1>Testdrehbuch <span id="clock"></span></h1>
<div id="steps">STEPS</div>
<div class="note"><input id="note" placeholder="Notiz (z. B. 'A sitzt doch am Tisch')"><button onclick="send(null)">Notiz</button></div>
<script>
async function send(n){
  const note = document.getElementById('note').value;
  const r = await fetch('press', {method:'POST', headers:{'Content-Type':'application/json'},
                         body: JSON.stringify({step:n, note: n===null ? note : ''})});
  if (n===null) document.getElementById('note').value='';
  show(await r.json());
}
function show(list){
  document.querySelectorAll('.step').forEach(el => {
    const n = +el.dataset.n, ts = list.filter(p => p.step === n).map(p => p.time.slice(11, 19));
    el.classList.toggle('done', ts.length > 0);
    el.querySelector('.times').textContent = ts.length ? 'gestartet: ' + ts.join(', ') : '';
  });
}
setInterval(() => document.getElementById('clock').textContent = new Date().toLocaleTimeString('de-DE'), 1000);
fetch('presses').then(r => r.json()).then(show);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/presses"):
            return self._send(json.dumps(presses()).encode(), "application/json")
        cards = "".join(
            f'<div class="step" data-n="{s["n"]}"><div class="head"><span class="n">{s["n"]}</span>'
            f'<span class="who">{html.escape(s["who"])}</span><span class="dur">{html.escape(s["dur"])}</span></div>'
            f'<div class="what">{html.escape(s["what"])}</div>'
            f'<button onclick="send({s["n"]})">Start</button><div class="times"></div></div>'
            for s in steps())
        self._send(PAGE.replace("STEPS", cards).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        now = datetime.datetime.now().astimezone()
        entry = {"time": now.isoformat(timespec="seconds"), "unix": round(now.timestamp(), 2),
                 "step": data.get("step"), "note": data.get("note", "")}
        with log_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print("press", entry, flush=True)
        self._send(json.dumps(presses()).encode(), "application/json")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"Drehbuch on http://0.0.0.0:{args.port}/ - log {log_file()}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
